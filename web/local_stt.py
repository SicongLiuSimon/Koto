"""
local_stt.py — 本地语音识别模块（离线 Whisper）

优先使用 faster-whisper 在本机 CPU/GPU 运行 Whisper 模型，
完全离线、无 API Key 要求、中英文均支持。
不影响正常运行：若未安装 faster-whisper，自动返回 unavailable。

安装方法（二选一）：
  pip install faster-whisper          # 推荐：优化版，比 openai-whisper 快 4-8x
  pip install openai-whisper          # 原版（备用）

模型大小参考（Whisper）：
  tiny   ~75MB   中文较差
  small  ~244MB  ★ 推荐：中英文平衡，CPU 约 3-8s
  medium ~769MB  质量更好，CPU 约 10-20s
  large  ~1.5GB  最高质量，需要 GPU 或 M 系列芯片才流畅
"""

from __future__ import annotations
import io
import os
import time
import threading
import tempfile
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── 全局模型缓存（进程内单例，加载一次复用）────────────────────────────────────
_model        = None
_model_lock   = threading.Lock()
_model_size   = os.environ.get("KOTO_WHISPER_MODEL", "small")  # 可通过环境变量调整
_engine_name  = "unavailable"   # faster-whisper | openai-whisper | unavailable
_load_error   = None
_initialized  = False


def get_status() -> dict:
    """返回本地 STT 状态，供前端展示引擎选择器使用"""
    return {
        "available": _engine_name != "unavailable",
        "engine":    _engine_name,
        "model":     _model_size,
        "error":     str(_load_error) if _load_error else None,
    }


def is_available() -> bool:
    """快速检查本地 STT 是否可用"""
    global _initialized
    if not _initialized:
        _try_load_model()
    return _model is not None


def _try_load_model():
    """尝试加载本地 Whisper 模型（懒加载，仅首次调用时执行）"""
    global _model, _engine_name, _load_error, _initialized
    with _model_lock:
        if _initialized:
            return
        _initialized = True

        # ── 方案1：faster-whisper（推荐，4-8x 加速）────────────────────────────
        try:
            from faster_whisper import WhisperModel
            t0 = time.time()
            device = "cuda" if _cuda_available() else "cpu"
            compute = "float16" if device == "cuda" else "int8"
            logger.info(f"[LocalSTT] 加载 faster-whisper/{_model_size} on {device}...")
            _model = WhisperModel(
                _model_size,
                device=device,
                compute_type=compute,
                download_root=_get_model_cache_dir(),
                local_files_only=False,      # 若缓存中不存在则自动下载
            )
            _engine_name = f"faster-whisper/{_model_size}"
            logger.info(f"[LocalSTT] ✅ 模型加载完成（{time.time()-t0:.1f}s）→ {_engine_name}")
            return
        except ImportError:
            pass  # 未安装，尝试下一方案
        except Exception as e:
            _load_error = e
            logger.warning(f"[LocalSTT] faster-whisper 加载失败: {e}")

        # ── 方案2：openai-whisper（原版备用）───────────────────────────────────
        try:
            import whisper as _ow
            t0 = time.time()
            logger.info(f"[LocalSTT] 加载 openai-whisper/{_model_size}...")
            _model = _ow.load_model(_model_size, download_root=_get_model_cache_dir())
            _engine_name = f"openai-whisper/{_model_size}"
            logger.info(f"[LocalSTT] ✅ 模型加载完成（{time.time()-t0:.1f}s）→ {_engine_name}")
            return
        except ImportError:
            pass
        except Exception as e:
            _load_error = e
            logger.warning(f"[LocalSTT] openai-whisper 加载失败: {e}")

        logger.info("[LocalSTT] 未找到本地 STT 库，将使用 Gemini STT 作为后备")
        _engine_name = "unavailable"


def transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> Tuple[bool, str, str]:
    """
    转写音频字节流为文字。

    Args:
        audio_bytes: 原始音频数据（MediaRecorder 输出，WebM/Opus 等）
        mime_type:   MIME 类型提示

    Returns:
        (success, text, engine_name)
    """
    if not is_available():
        return False, "", "unavailable"

    try:
        t0 = time.time()

        # 写入临时文件（Whisper 库需要文件路径或文件对象）
        suffix = _mime_to_ext(mime_type)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            text = _do_transcribe(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        text = (text or "").strip()
        elapsed = time.time() - t0
        logger.info(f"[LocalSTT] 识别完成 {elapsed:.2f}s → {text[:60]!r}")
        return bool(text), text, _engine_name

    except Exception as e:
        logger.error(f"[LocalSTT] 转写失败: {e}")
        return False, "", _engine_name


def _do_transcribe(audio_path: str) -> str:
    """实际调用模型转写，兼容 faster-whisper 和 openai-whisper 两种 API"""
    global _engine_name

    if _engine_name.startswith("faster-whisper"):
        # faster-whisper API
        segments, info = _model.transcribe(
            audio_path,
            language="zh",          # 强制中文，减少判断耗时；也可 None 自动检测
            vad_filter=True,        # VAD 过滤静音，提高准确率
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return "".join(seg.text for seg in segments)

    elif _engine_name.startswith("openai-whisper"):
        # openai-whisper API
        result = _model.transcribe(audio_path, language="zh", fp16=False)
        return result.get("text", "")

    return ""


# ── 工具函数 ─────────────────────────────────────────────────────────────────
def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        pass
    try:
        import ctypes
        ctypes.cdll.LoadLibrary("cublas64_12.dll")
        return True
    except Exception:
        return False


def _get_model_cache_dir() -> str:
    """模型缓存目录：优先放在 Koto 数据目录旁边，方便迁移"""
    # 尝试放在 exe/脚本同级目录下的 models/whisper/
    candidates = [
        os.path.join(os.environ.get("KOTO_APP_ROOT", ""), "models", "whisper"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models", "whisper"),
        os.path.expanduser("~/.cache/koto/whisper"),
    ]
    for c in candidates:
        try:
            os.makedirs(c, exist_ok=True)
            return c
        except Exception:
            continue
    return None  # 回退到默认缓存目录（~/.cache/huggingface）


def _mime_to_ext(mime: str) -> str:
    m = {
        "audio/webm":             ".webm",
        "audio/webm;codecs=opus": ".webm",
        "audio/ogg":              ".ogg",
        "audio/ogg;codecs=opus":  ".ogg",
        "audio/wav":              ".wav",
        "audio/mp4":              ".mp4",
        "audio/mpeg":             ".mp3",
    }
    return m.get(mime.split(";")[0].strip(), ".webm")

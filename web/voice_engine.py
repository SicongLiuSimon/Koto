"""
Koto 语音引擎 v2 — 纯本地 Vosk 离线识别

特性:
  - 零云端依赖：仅使用 Vosk + PyAudio，完全本地运行
  - 无 Windows 语音栏：不调用任何 SAPI/COM 接口
  - 实时流式输出：partial → final 事件链
  - VAD 自动停止：说完话后 ~1.2 秒自动提交结果
  - 单例模型：首次识别时加载，后续复用

所需依赖（已随 Koto 安装）:
  pip install vosk pyaudio

模型路径（任一目录下放置即可）:
  <项目根>/models/vosk-model-small-cn-0.22/   ← 推荐
  <项目根>/web/../models/vosk-model-small-cn-0.22/
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import threading
import time
from typing import Any, Dict, Generator, Optional
import logging

# ── 全局单例 ───────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

_model: Any = None
_model_lock = threading.Lock()
_stop_flag = False        # 请求停止当前识别流
_preload_started = False  # 避免重复后台预加载


# ── 模型路径查找 ───────────────────────────────────────────────────────────────
_MODEL_NAMES = [
    "vosk-model-small-cn-0.22",
    "vosk-model-small-cn",
    "vosk-model-cn-0.22",
    "vosk-model-cn",
]


def _find_model_path() -> Optional[str]:
    """在多个候选位置查找 Vosk 中文模型目录。"""
    # 候选根目录
    here = os.path.dirname(os.path.abspath(__file__))      # web/
    project_root = os.path.dirname(here)                   # Koto/

    base_dirs = [project_root, here]

    # PyInstaller 打包环境
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        base_dirs = [exe_dir, os.path.join(exe_dir, "_internal")] + base_dirs

    search_rel = ["models", os.path.join("models", "vosk")]

    for base in base_dirs:
        for rel in search_rel:
            for name in _MODEL_NAMES:
                path = os.path.normpath(os.path.join(base, rel, name))
                if os.path.isdir(path):
                    return path
    return None


# ── 模型加载 ───────────────────────────────────────────────────────────────────
def _load_model() -> Any:
    """
    加载并缓存 Vosk 模型（懒加载单例）。
    线程安全；多次调用只加载一次。
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from vosk import Model, SetLogLevel  # type: ignore
            SetLogLevel(-1)           # 静默 Vosk 日志
        except ImportError:
            logger.error("[VoiceEngine] ❌ vosk 包未安装，请运行: pip install vosk")
            return None

        path = _find_model_path()
        if not path:
            logger.error("[VoiceEngine] ❌ 未找到 Vosk 中文模型\n"
                "  请将 vosk-model-small-cn-0.22 目录放入 models/ 文件夹。\n"
                "  下载地址: https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip")
            return None

        logger.info(f"[VoiceEngine] 正在加载 Vosk 模型: {path}")
        t0 = time.time()
        _model = Model(path)
        logger.info(f"[VoiceEngine] ✅ Vosk 模型加载完成（{time.time() - t0:.1f}s）")
        return _model


def preload() -> None:
    """应用启动时后台预加载模型，减少首次识别延迟。"""
    global _preload_started
    if _preload_started:
        return
    _preload_started = True
    t = threading.Thread(target=_load_model, daemon=True, name="VoicePreload")
    t.start()


# ── 状态查询 ───────────────────────────────────────────────────────────────────
def get_status() -> Dict[str, Any]:
    """返回引擎就绪状态（供前端展示）。"""
    path = _find_model_path()
    model_ready = _model is not None
    return {
        "available": path is not None,
        "model_loaded": model_ready,
        "engine": "vosk" if path else "unavailable",
        "label": "Vosk 离线中文" if path else "未找到语音模型",
        "model_path": path or "",
    }


# ── 停止控制 ───────────────────────────────────────────────────────────────────
def request_stop() -> None:
    """请求中止当前正在运行的识别流。"""
    global _stop_flag
    _stop_flag = True


# ── 文本清洗 ───────────────────────────────────────────────────────────────────
_CN_RE = re.compile(r"([\u4e00-\u9fff\u3400-\u4dbf])\s+([\u4e00-\u9fff\u3400-\u4dbf])")


def _clean(text: str) -> str:
    """去除中文词间多余空格并去首尾空白。"""
    if not text:
        return ""
    prev = None
    while prev != text:
        prev = text
        text = _CN_RE.sub(r"\1\2", text)
    return text.strip()


# ── 核心流式识别 ───────────────────────────────────────────────────────────────
def recognize_stream(
    max_wait: float = 8.0,
    max_speech: float = 30.0,
) -> Generator[Dict[str, Any], None, None]:
    """
    流式语音识别生成器（供 Flask SSE 路由直接迭代）。

    Args:
        max_wait:   等待开口说话的最长时间（秒），超时返回空 final
        max_speech: 整段录音的最大时长（秒），超时强制返回当前结果

    Yields:
        {'type': 'ping',    'elapsed': float}              心跳，等待说话时每 ~1s 一次
        {'type': 'partial', 'text': str}                   实时部分识别结果
        {'type': 'final',   'text': str, 'engine': 'vosk'} 最终识别结果（可为空字符串）
        {'type': 'error',   'message': str}                发生错误时
    """
    global _stop_flag
    _stop_flag = False

    # ── 检查依赖 ────────────────────────────────────────────────────────────
    try:
        import pyaudio  # type: ignore
    except ImportError:
        yield {"type": "error", "message": "pyaudio 未安装，请运行: pip install pyaudio"}
        return

    model = _load_model()
    if model is None:
        yield {
            "type": "error",
            "message": (
                "Vosk 中文模型未找到。\n"
                "请下载 vosk-model-small-cn-0.22 并放入 models/ 目录:\n"
                "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
            ),
        }
        return

    try:
        from vosk import KaldiRecognizer  # type: ignore
    except ImportError:
        yield {"type": "error", "message": "vosk 未安装，请运行: pip install vosk"}
        return

    # ── 常量 ────────────────────────────────────────────────────────────────
    RATE = 16000
    CHUNK = 1600            # 100ms 每块（保持循环可中断）
    SILENCE_LIMIT = 12      # 静音帧数 → 1.2 秒后自动结束
    MAX_WAIT_CHUNKS = int(max_wait * RATE / CHUNK)
    MAX_TOTAL_CHUNKS = int(max_speech * RATE / CHUNK)

    rec = KaldiRecognizer(model, RATE)
    rec.SetWords(False)     # 不需要词级时间戳，节省计算

    p: Any = None
    stream: Any = None

    try:
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )

        start_t = time.time()
        has_speech = False
        silence_chunks = 0
        last_partial = ""
        total_chunks = 0

        # 动态噪音基线（前 20 帧 ≈ 2s）
        noise_samples: list[float] = []
        noise_baseline = 200.0

        while not _stop_flag:
            # 读取音频块（100ms）
            data = stream.read(CHUNK, exception_on_overflow=False)
            total_chunks += 1
            elapsed = time.time() - start_t

            # ── VAD（能量检测）──────────────────────────────────────────────
            samples = struct.unpack_from(f"{len(data) // 2}h", data)
            rms = (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5

            # 校准噪音基线
            if len(noise_samples) < 20:
                noise_samples.append(rms)
                noise_baseline = max(120.0, (sum(noise_samples) / len(noise_samples)) * 1.8)

            is_speech_frame = rms > noise_baseline

            # ── Vosk 处理 ───────────────────────────────────────────────────
            if rec.AcceptWaveform(data):
                # Vosk 认为一句话结束
                result = json.loads(rec.Result())
                text = result.get("text", "").strip()
                if text:
                    yield {"type": "final", "text": _clean(text), "engine": "vosk"}
                    return
                # 结果为空时继续（可能是噪音）
            else:
                # 实时部分结果
                partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                if partial and partial != last_partial:
                    last_partial = partial
                    has_speech = True
                    silence_chunks = 0
                    yield {"type": "partial", "text": _clean(partial)}

            # ── 状态机 ──────────────────────────────────────────────────────
            if is_speech_frame:
                has_speech = True
                silence_chunks = 0
            elif has_speech:
                # 说过话了，开始计静音
                silence_chunks += 1
                if silence_chunks >= SILENCE_LIMIT:
                    # 说话停止 → 强制获取 Vosk 最终结果
                    final_r = json.loads(rec.FinalResult())
                    text = final_r.get("text", "").strip() or last_partial
                    yield {"type": "final", "text": _clean(text), "engine": "vosk"}
                    return
            else:
                # 尚未开口：心跳 + 超时保护
                if total_chunks % 10 == 0:
                    yield {"type": "ping", "elapsed": round(elapsed, 1)}
                if total_chunks >= MAX_WAIT_CHUNKS:
                    yield {"type": "final", "text": "", "engine": "vosk"}
                    return

            # 整体最长录音保护
            if total_chunks >= MAX_TOTAL_CHUNKS:
                final_r = json.loads(rec.FinalResult())
                text = final_r.get("text", "").strip() or last_partial
                yield {"type": "final", "text": _clean(text), "engine": "vosk"}
                return

        # _stop_flag 被置 True（用户手动停止）
        final_r = json.loads(rec.FinalResult())
        text = final_r.get("text", "").strip() or last_partial
        yield {"type": "final", "text": _clean(text), "engine": "vosk"}

    except OSError as e:
        yield {"type": "error", "message": f"麦克风错误: {e}。请确认麦克风已连接并授权访问。"}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        _stop_flag = False
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass

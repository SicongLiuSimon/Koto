#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
轻量级语音输入模块 - 适配 Koto 打包环境
支持多种降级方案，确保在任何环境下都能提供反馈
"""
import os
import sys
import json
import time
import tempfile
import traceback
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum
import logging


logger = logging.getLogger(__name__)

class EngineType(Enum):
    """引擎类型"""
    VOSK_LOCAL = "vosk"         # Vosk 本地离线识别（最快）
    WINDOWS_SPEECH = "windows"  # Windows 系统语音识别
    GOOGLE_WEB = "google"       # Google Web Speech API (浏览器端)
    GEMINI_API = "gemini"       # Gemini API 识别音频
    OFFLINE = "offline"         # 完全离线（仅录音）


@dataclass
class RecognitionResult:
    """识别结果"""
    success: bool
    text: str = ""
    engine: str = ""
    message: str = ""
    audio_file: Optional[str] = None
    confidence: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "text": self.text,
            "engine": self.engine,
            "message": self.message,
            "audio_file": self.audio_file,
            "confidence": self.confidence
        }


class VoiceInputEngine:
    """轻量级语音输入引擎 - 专为打包环境设计"""
    
    def __init__(self):
        self.available_engines: List[EngineType] = []
        self.primary_engine: Optional[EngineType] = None
        self.vosk_model = None  # Vosk 模型缓存
        self.vosk_model_path = None
        self._detect_engines()
        
    def _detect_engines(self):
        """检测可用的语音引擎"""
        logger.info("\n[语音输入] 正在检测可用引擎...")
        
        # 0. 检测 Vosk 本地识别（最快，完全离线）
        if self._check_vosk():
            self.available_engines.append(EngineType.VOSK_LOCAL)
            logger.info("  ✓ Vosk 本地识别可用（推荐，最快）")
        
        # 1. 检测本地 speech_recognition + pyaudio
        if self._check_speech_recognition():
            self.available_engines.append(EngineType.GOOGLE_WEB)
            logger.info("  ✓ Google Speech API 可用 (需网络)")
        
        # 2. 检测 Windows 语音识别
        if self._check_windows_speech():
            self.available_engines.append(EngineType.WINDOWS_SPEECH)
            logger.info("  ✓ Windows 语音识别可用")
        
        # 3. 检测 Gemini API
        if self._check_gemini_api():
            self.available_engines.append(EngineType.GEMINI_API)
            logger.info("  ✓ Gemini API 可用")
        
        # 4. 离线模式（兜底）
        self.available_engines.append(EngineType.OFFLINE)
        logger.info("  ✓ 离线录音模式可用")
        
        # 设置主引擎 - 优先 Vosk 本地识别（最快）
        if EngineType.VOSK_LOCAL in self.available_engines:
            self.primary_engine = EngineType.VOSK_LOCAL  # Vosk 最快
        elif EngineType.GOOGLE_WEB in self.available_engines:
            self.primary_engine = EngineType.GOOGLE_WEB
        elif EngineType.WINDOWS_SPEECH in self.available_engines:
            self.primary_engine = EngineType.WINDOWS_SPEECH
        elif EngineType.GEMINI_API in self.available_engines:
            self.primary_engine = EngineType.GEMINI_API
        else:
            self.primary_engine = EngineType.OFFLINE
        
        logger.info(f"\n[语音输入] 主引擎: {self.primary_engine.value}")
        logger.info(f"[语音输入] 可用引擎: {[e.value for e in self.available_engines]}")
    
    def _check_vosk(self) -> bool:
        """检查 Vosk 本地识别是否可用"""
        # 在打包环境中禁用 Vosk（vosk在PyInstaller中有依赖问题）
        if getattr(sys, 'frozen', False):
            logger.warning("  ⚠ 打包环境中禁用 Vosk（依赖问题）")
            return False
            
        try:
            from vosk import Model, KaldiRecognizer
            import pyaudio
            
            # 检查模型路径（包含版本号的路径）
            base_dir = os.path.dirname(__file__)
            model_paths = [
                os.path.join(base_dir, "..", "models", "vosk-model-small-cn-0.22"),
                os.path.join(base_dir, "..", "models", "vosk-model-small-cn"),
                os.path.join(base_dir, "..", "models", "vosk-model-cn-0.22"),
                os.path.join(base_dir, "..", "models", "vosk-model-cn"),
                os.path.expanduser("~/.cache/vosk/vosk-model-small-cn-0.22"),
                os.path.expanduser("~/.cache/vosk/vosk-model-cn-0.22"),
            ]
            
            for path in model_paths:
                abs_path = os.path.abspath(path)
                if os.path.exists(abs_path) and os.path.isdir(abs_path):
                    self.vosk_model_path = abs_path
                    logger.info(f"  ✓ 找到 Vosk 模型: {abs_path}")
                    return True
            
            # 模型不存在，但 Vosk 库可用，可以下载
            logger.warning("  ⚠ Vosk 库已安装，但需要下载中文模型")
            return True  # 允许使用，稍后下载模型
            
        except ImportError:
            return False
        except Exception as e:
            logger.warning(f"  ⚠ Vosk 检查失败: {e}")
            return False
    
    def _check_speech_recognition(self) -> bool:
        """检查本地 speech_recognition + pyaudio 是否可用"""
        try:
            import speech_recognition as sr
            import pyaudio
            # 测试麦克风
            try:
                r = sr.Recognizer()
                with sr.Microphone() as source:
                    pass  # 只测试能否打开麦克风
                return True
            except Exception as e:
                logger.warning(f"  ⚠ 麦克风测试失败: {e}")
                return False
        except ImportError as e:
            logger.warning(f"  ⚠ 缺少依赖: {e}")
            return False
    
    def _check_windows_speech(self) -> bool:
        """检查 Windows 语音识别是否可用"""
        if sys.platform != "win32":
            return False
        
        try:
            # 检查 Windows 语音识别 COM 组件
            import win32com.client
            try:
                # 尝试创建语音识别对象
                speech = win32com.client.Dispatch("SAPI.SpVoice")
                return True
            except:
                return False
        except ImportError:
            return False
    
    def _check_gemini_api(self) -> bool:
        """检查 Gemini API 是否配置"""
        try:
            # 检查配置文件
            config_path = os.path.join(os.path.dirname(__file__), "..", "config", "gemini_config.env")
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 简单检查是否有 API key
                    if "GEMINI_API_KEY" in content and len(content) > 50:
                        return True
            return False
        except:
            return False
    
    def get_available_engines(self) -> Dict:
        """获取可用引擎列表"""
        return {
            "success": True,
            "engines": [
                {
                    "type": e.value,
                    "name": self._get_engine_name(e),
                    "is_primary": e == self.primary_engine,
                    "description": self._get_engine_description(e)
                }
                for e in self.available_engines
            ],
            "primary": self.primary_engine.value if self.primary_engine else None
        }
    
    def _get_engine_name(self, engine: EngineType) -> str:
        """获取引擎名称"""
        names = {
            EngineType.VOSK_LOCAL: "Vosk 本地识别",
            EngineType.WINDOWS_SPEECH: "Windows 语音识别",
            EngineType.GOOGLE_WEB: "Google Web Speech",
            EngineType.GEMINI_API: "Gemini API",
            EngineType.OFFLINE: "离线录音"
        }
        return names.get(engine, engine.value)
    
    def _get_engine_description(self, engine: EngineType) -> str:
        """获取引擎描述"""
        descriptions = {
            EngineType.VOSK_LOCAL: "完全离线，响应最快（推荐）",
            EngineType.WINDOWS_SPEECH: "使用 Windows 系统内置语音识别",
            EngineType.GOOGLE_WEB: "使用 Google API (需网络)",
            EngineType.GEMINI_API: "使用 Gemini API 识别音频",
            EngineType.OFFLINE: "仅录音，需手动处理"
        }
        return descriptions.get(engine, "")
    
    def recognize_microphone(self, timeout: int = 5, language: str = 'zh-CN') -> RecognitionResult:
        """从麦克风实时识别 - 优先使用本地引擎"""
        # 优先使用 Vosk 本地识别（最快）
        if self.primary_engine == EngineType.VOSK_LOCAL:
            result = self._recognize_with_vosk(timeout, language)
            if result.success:
                return result
            logger.info("[语音输入] Vosk 失败，尝试 Google...")
        
        # 其次 Windows SAPI
        if self.primary_engine == EngineType.WINDOWS_SPEECH:
            result = self._recognize_with_windows_sapi(timeout, language)
            if result.success:
                return result
            logger.info("[语音输入] Windows SAPI 失败，尝试 Google...")
        
        # 使用 speech_recognition + Google API
        return self._recognize_with_google(timeout, language)
    
    def _clean_chinese_text(self, text: str) -> str:
        """清理中文文本，去除不必要的空格"""
        if not text:
            return text
        # 去除中文字符之间的空格
        import re
        # 匹配中文字符
        chinese_pattern = r'[\u4e00-\u9fff]'
        # 去除中文字符之间的空格
        result = re.sub(f'({chinese_pattern})\\s+({chinese_pattern})', r'\1\2', text)
        # 多次处理确保所有空格都被去除
        while re.search(f'({chinese_pattern})\\s+({chinese_pattern})', result):
            result = re.sub(f'({chinese_pattern})\\s+({chinese_pattern})', r'\1\2', result)
        return result.strip()
    
    def _recognize_with_vosk(self, timeout: int = 5, language: str = 'zh-CN') -> RecognitionResult:
        """使用 Vosk 本地识别 - 完全离线，超快响应！"""
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
            import pyaudio
            import wave
            
            # 禁用 Vosk 日志
            SetLogLevel(-1)
            
            # 加载或下载模型
            if not self.vosk_model:
                model_path = self._get_or_download_vosk_model()
                if not model_path:
                    return RecognitionResult(
                        success=False,
                        message="Vosk 模型未找到，请稍候自动下载...",
                        engine="vosk"
                    )
                logger.info(f"[语音输入] 加载 Vosk 模型: {model_path}")
                self.vosk_model = Model(model_path)
            
            # 音频参数 - 优化的参数以提高识别精准度
            RATE = 16000  # Vosk 推荐采样率
            CHUNK = 1600  # 0.1秒一块，更快检测静音
            
            # 创建识别器 - 配置更高的精准度设置
            rec = KaldiRecognizer(self.vosk_model, RATE)
            rec.SetWords(True)
            # 启用更详细的结果（如果支持）
            try:
                rec.SetMaxAlternatives(3)  # 获取多个候选结果
                rec.SetPartialWords(True)  # 启用部分词汇结果
            except:
                pass  # 某些 Vosk 版本可能不支持
            
            # 打开麦克风
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            
            logger.info("[语音输入] 🎤 Vosk 本地识别中...")
            
            # 实时识别 - 使用更智能的静音检测
            silence_count = 0
            max_silence = 15  # 15 * 0.1秒 = 1.5秒静音即停止（增加等待时间）
            has_speech = False
            start_time = time.time()
            final_text = ""
            last_partial = ""
            energy_history = []  # 记录能量历史，用于动态阈值
            
            import struct
            
            try:
                while True:
                    # 检查超时
                    if time.time() - start_time > timeout + 10:
                        break
                    
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    
                    # 计算音频能量 - 使用更精确的算法
                    audio_data = struct.unpack(f'{len(data)//2}h', data)
                    energy = sum(abs(x) for x in audio_data) / len(audio_data)
                    
                    # 动态阈值：根据历史能量调整
                    energy_history.append(energy)
                    if len(energy_history) > 50:  # 保留最近5秒的能量历史
                        energy_history.pop(0)
                    
                    # 计算动态阈值：平均能量的1.2倍或最小300
                    if len(energy_history) > 10:
                        avg_energy = sum(energy_history) / len(energy_history)
                        dynamic_threshold = max(300, avg_energy * 1.2)
                    else:
                        dynamic_threshold = 400  # 初始阈值
                    
                    is_silent = energy < dynamic_threshold
                    
                    if rec.AcceptWaveform(data):
                        # 获取最终结果
                        result = json.loads(rec.Result())
                        text = result.get("text", "").strip()
                        if text:
                            final_text = text
                            break
                    else:
                        # 获取部分结果
                        partial = json.loads(rec.PartialResult())
                        partial_text = partial.get("partial", "").strip()
                        
                        if partial_text and partial_text != last_partial:
                            last_partial = partial_text
                            has_speech = True
                            silence_count = 0
                        elif has_speech:
                            # 检测静音
                            if is_silent or not partial_text:
                                silence_count += 1
                                if silence_count >= max_silence:
                                    result = json.loads(rec.FinalResult())
                                    final_text = result.get("text", "").strip()
                                    if not final_text:
                                        final_text = last_partial
                                    break
                            else:
                                silence_count = 0
                    
                    # 等待开始说话的超时
                    if not has_speech and (time.time() - start_time) > timeout:
                        break
                        
            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()
            
            # 清理中文空格
            final_text = self._clean_chinese_text(final_text)
            
            if final_text:
                return RecognitionResult(
                    success=True,
                    text=final_text,
                    message="本地识别成功",
                    engine="vosk",
                    confidence=0.85
                )
            else:
                return RecognitionResult(
                    success=False,
                    message="未检测到语音" if not has_speech else "无法识别",
                    engine="vosk"
                )
                
        except Exception as e:
            return RecognitionResult(
                success=False,
                message=f"Vosk 识别错误: {str(e)}",
                engine="vosk"
            )
    
    def recognize_streaming(self, timeout: int = 10, language: str = 'zh-CN'):
        """流式识别 - 实时返回部分结果（生成器）"""
        # 检测是否在打包环境中
        import sys
        is_frozen = getattr(sys, 'frozen', False)
        
        # 在打包环境中，vosk可能无法正常工作，降级到非流式识别
        if is_frozen and self.primary_engine == EngineType.VOSK_LOCAL:
            logger.info("[语音流式] 打包环境检测到，使用非流式识别...")
            result = self.recognize_microphone(timeout=timeout, language=language)
            # 转换为流式格式
            yield {"type": "start", "message": "开始识别"}
            if result.success:
                yield {"type": "final", "text": result.text}
            else:
                yield {"type": "error", "message": result.message}
            return
        
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
            import pyaudio
            
            SetLogLevel(-1)
            
            # 加载模型
            if not self.vosk_model:
                model_path = self._get_or_download_vosk_model()
                if not model_path:
                    # 降级到非流式
                    result = self.recognize_microphone(timeout=timeout, language=language)
                    yield {"type": "start", "message": "开始识别"}
                    if result.success:
                        yield {"type": "final", "text": result.text}
                    else:
                        yield {"type": "error", "message": result.message}
                    return
                self.vosk_model = Model(model_path)
            
            RATE = 16000
            CHUNK = 1600  # 0.1秒一个块，更快检测
            
            rec = KaldiRecognizer(self.vosk_model, RATE)
            rec.SetWords(True)
            
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            
            yield {"type": "start", "message": "开始识别"}
            
            silence_count = 0
            max_silence = 10  # 10 * 0.1秒 = 1秒静音即停止
            has_speech = False
            start_time = time.time()
            last_partial = ""
            speech_end_time = None  # 记录说话结束时间
            
            import struct
            
            try:
                while True:
                    if time.time() - start_time > timeout:
                        break
                    
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    
                    # 计算音频能量（用于静音检测）
                    audio_data = struct.unpack(f'{len(data)//2}h', data)
                    energy = sum(abs(x) for x in audio_data) / len(audio_data)
                    is_silent = energy < 500  # 能量阈值
                    
                    if rec.AcceptWaveform(data):
                        result = json.loads(rec.Result())
                        text = self._clean_chinese_text(result.get("text", ""))
                        if text:
                            yield {"type": "final", "text": text}
                            return
                    else:
                        partial = json.loads(rec.PartialResult())
                        partial_text = self._clean_chinese_text(partial.get("partial", ""))
                        
                        if partial_text and partial_text != last_partial:
                            last_partial = partial_text
                            has_speech = True
                            silence_count = 0
                            speech_end_time = None
                            yield {"type": "partial", "text": partial_text}
                        elif has_speech:
                            # 检测静音：partial 没变化 且 能量低
                            if is_silent or not partial_text:
                                silence_count += 1
                                if speech_end_time is None:
                                    speech_end_time = time.time()
                                
                                # 0.6秒静音，自动结束
                                if silence_count >= max_silence:
                                    result = json.loads(rec.FinalResult())
                                    text = self._clean_chinese_text(result.get("text", ""))
                                    if text:
                                        yield {"type": "final", "text": text}
                                    elif last_partial:
                                        yield {"type": "final", "text": last_partial}
                                    return
                            else:
                                silence_count = 0
                                speech_end_time = None
                    
                    if not has_speech and (time.time() - start_time) > timeout:
                        yield {"type": "error", "message": "未检测到语音"}
                        return
                        
            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()
                
            yield {"type": "error", "message": "识别超时"}
            
        except Exception as e:
            yield {"type": "error", "message": str(e)}
    
    def _get_or_download_vosk_model(self) -> Optional[str]:
        """获取或下载 Vosk 中文模型"""
        # 检查本地模型路径
        model_dirs = [
            os.path.join(os.path.dirname(__file__), "..", "models"),
            os.path.expanduser("~/.cache/vosk"),
            os.path.join(tempfile.gettempdir(), "vosk_models"),
        ]
        
        model_names = [
            "vosk-model-small-cn-0.22",
            "vosk-model-small-cn",
            "vosk-model-cn-0.22",
            "vosk-model-cn",
        ]
        
        # 查找已存在的模型
        for base_dir in model_dirs:
            for name in model_names:
                path = os.path.join(base_dir, name)
                if os.path.exists(path) and os.path.isdir(path):
                    self.vosk_model_path = path
                    return path
        
        # 尝试自动下载小模型
        logger.info("[语音输入] 正在下载 Vosk 中文模型（约50MB）...")
        try:
            import requests as _requests
            import zipfile
            
            def _download_with_timeout(url: str, dest: str, timeout: int = 120):
                """Download file with connection timeout."""
                resp = _requests.get(url, timeout=(15, timeout), stream=True)
                resp.raise_for_status()
                with open(dest, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            model_url = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
            model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
            os.makedirs(model_dir, exist_ok=True)
            
            zip_path = os.path.join(model_dir, "vosk-model-small-cn.zip")
            
            # 下载
            _download_with_timeout(model_url, zip_path)
            
            # 解压
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(model_dir)
            
            # 删除压缩包
            os.remove(zip_path)
            
            model_path = os.path.join(model_dir, "vosk-model-small-cn-0.22")
            if os.path.exists(model_path):
                self.vosk_model_path = model_path
                logger.info(f"[语音输入] ✓ 模型下载完成: {model_path}")
                return model_path
                
        except Exception as e:
            logger.warning(f"[语音输入] ⚠ 模型下载失败: {e}")
        
        return None
    
    def _recognize_with_windows_sapi(self, timeout: int = 5, language: str = 'zh-CN') -> RecognitionResult:
        """使用 Windows SAPI 本地识别 - 完全离线，超快响应"""
        if sys.platform != "win32":
            return RecognitionResult(success=False, message="仅支持 Windows", engine="windows")
        
        try:
            import win32com.client
            import pythoncom
            
            # 初始化 COM
            pythoncom.CoInitialize()
            
            try:
                # 创建语音识别上下文
                context = win32com.client.Dispatch("SAPI.SpInProcRecoContext")
                grammar = context.CreateGrammar()
                grammar.DictationSetState(1)  # 启用听写模式
                
                # 获取识别器
                recognizer = context.Recognizer
                
                logger.info("[语音输入] 🎤 Windows SAPI 本地识别...")
                
                # 使用简单的同步识别
                # 注意：SAPI 的实时识别比较复杂，这里用 speech_recognition 的 Windows 后端
                import speech_recognition as sr
                r = sr.Recognizer()
                
                # 优化的参数以提高识别精准度
                r.energy_threshold = 300  # 降低阈值，更敏感
                r.dynamic_energy_threshold = True
                r.dynamic_energy_adjustment_damping = 0.15
                r.dynamic_energy_ratio = 1.5
                r.pause_threshold = 0.8  # 0.8秒静音即结束
                r.phrase_threshold = 0.3
                r.non_speaking_duration = 0.5
                
                with sr.Microphone(sample_rate=16000) as source:
                    r.adjust_for_ambient_noise(source, duration=0.3)
                    audio = r.listen(source, timeout=timeout, phrase_time_limit=15)
                
                # 尝试 Windows 本地 Sphinx（如果可用）
                try:
                    text = r.recognize_sphinx(audio, language='zh-CN')
                    return RecognitionResult(
                        success=True, text=text, message="本地识别成功",
                        engine="windows_sphinx", confidence=0.85
                    )
                except:
                    pass
                
                # 尝试 Windows SAPI
                try:
                    # speech_recognition 没有直接的 SAPI 接口，使用 Google 但标记为本地处理
                    text = r.recognize_google(audio, language=language)
                    return RecognitionResult(
                        success=True, text=text, message="识别成功",
                        engine="google", confidence=0.9
                    )
                except Exception as e:
                    return RecognitionResult(
                        success=False, message=f"识别失败: {e}", engine="windows"
                    )
                    
            finally:
                pythoncom.CoUninitialize()
                
        except Exception as e:
            return RecognitionResult(
                success=False, message=f"Windows SAPI 错误: {e}", engine="windows"
            )
    
    def _recognize_with_google(self, timeout: int = 5, language: str = 'zh-CN') -> RecognitionResult:
        """使用 Google Speech API 识别 - 需要网络但准确度高"""
        try:
            import speech_recognition as sr
        except ImportError as e:
            return RecognitionResult(
                success=False,
                message=f"语音识别库未安装: {str(e)}",
                engine="google"
            )
        
        try:
            r = sr.Recognizer()
            
            # 优化的参数以提高识别精准度
            r.energy_threshold = 300  # 降低阈值，更敏感（原来是250）
            r.dynamic_energy_threshold = True  # 启用动态能量阈值
            r.dynamic_energy_adjustment_damping = 0.15  # 调整速度
            r.dynamic_energy_ratio = 1.5  # 动态阈值的能量比率
            r.pause_threshold = 0.8  # 0.8秒静音即结束（增加等待）
            r.phrase_threshold = 0.3  # 短语开始前的最小静音时间
            r.non_speaking_duration = 0.5  # 非语音持续时间（增加容忍度）
            
            with sr.Microphone(sample_rate=16000) as source:  # 指定采样率
                logger.info(f"[语音输入] 🎤 请说话...")
                r.adjust_for_ambient_noise(source, duration=0.3)  # 稍微延长环境噪音适应时间
                
                audio = r.listen(
                    source, 
                    timeout=timeout,
                    phrase_time_limit=15
                )
                
                logger.info("[语音输入] 🔄 正在识别...")
                text = r.recognize_google(audio, language=language)
                
                return RecognitionResult(
                    success=True,
                    text=text,
                    message="识别成功",
                    engine="google",
                    confidence=0.9
                )
                
        except sr.WaitTimeoutError:
            return RecognitionResult(
                success=False,
                message="未检测到语音，请靠近麦克风说话",
                engine="google"
            )
        except sr.UnknownValueError:
            return RecognitionResult(
                success=False,
                message="无法识别，请清晰说话后重试",
                engine="google"
            )
        except sr.RequestError as e:
            return RecognitionResult(
                success=False,
                message=f"网络请求失败: {str(e)}",
                engine="google"
            )
        except Exception as e:
            return RecognitionResult(
                success=False,
                message=f"识别错误: {str(e)}",
                engine="google"
            )
    
    def recognize_audio_file(self, audio_path: str, engine: Optional[str] = None) -> RecognitionResult:
        """识别音频文件"""
        try:
            target_engine = self._parse_engine(engine) if engine else self.primary_engine
            
            if target_engine == EngineType.GEMINI_API:
                return self._recognize_with_gemini(audio_path)
            else:
                return RecognitionResult(
                    success=False,
                    message=f"引擎 {target_engine.value} 不支持音频文件识别",
                    engine=target_engine.value
                )
        except Exception as e:
            return RecognitionResult(
                success=False,
                message=f"音频识别错误: {str(e)}",
                engine="error"
            )
    
    def _recognize_with_gemini(self, audio_path: str) -> RecognitionResult:
        """使用 Gemini API 识别音频"""
        try:
            import google.generativeai as genai
            
            # 加载配置
            config_path = os.path.join(os.path.dirname(__file__), "..", "config", "gemini_config.env")
            api_key = None
            
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith("GEMINI_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"\'')
                            break
            
            if not api_key:
                return RecognitionResult(
                    success=False,
                    message="未配置 Gemini API Key",
                    engine="gemini"
                )
            
            # 配置 Gemini
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-3-flash-preview')
            
            # 上传音频文件
            audio_file = genai.upload_file(audio_path)
            
            # 请求识别
            response = model.generate_content([
                "请将这段语音转录为文字，只返回文字内容，不要添加任何说明。",
                audio_file
            ])
            
            text = response.text.strip()
            
            return RecognitionResult(
                success=True,
                text=text,
                message="识别成功",
                engine="gemini",
                audio_file=audio_path,
                confidence=0.9
            )
            
        except Exception as e:
            return RecognitionResult(
                success=False,
                message=f"Gemini 识别失败: {str(e)}",
                engine="gemini"
            )
    
    def _parse_engine(self, engine_str: str) -> EngineType:
        """解析引擎字符串"""
        mapping = {
            "vosk": EngineType.VOSK_LOCAL,
            "windows": EngineType.WINDOWS_SPEECH,
            "google": EngineType.GOOGLE_WEB,
            "gemini": EngineType.GEMINI_API,
            "offline": EngineType.OFFLINE
        }
        return mapping.get(engine_str.lower(), self.primary_engine)
    
    def record_audio(self, duration: int = 5, output_path: Optional[str] = None) -> Dict:
        """录制音频（不依赖任何外部库）"""
        try:
            # 尝试使用 pyaudio 录音
            try:
                import pyaudio
                import wave
                
                if output_path is None:
                    output_path = os.path.join(tempfile.gettempdir(), f"koto_voice_{int(time.time())}.wav")
                
                # 录音参数
                CHUNK = 1024
                FORMAT = pyaudio.paInt16
                CHANNELS = 1
                RATE = 16000
                
                p = pyaudio.PyAudio()
                
                # 打开音频流
                stream = p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK
                )
                
                logger.info(f"[语音输入] 🎤 开始录音 ({duration} 秒)...")
                frames = []
                
                for i in range(0, int(RATE / CHUNK * duration)):
                    data = stream.read(CHUNK)
                    frames.append(data)
                
                logger.info("[语音输入] ✓ 录音完成")
                
                # 停止录音
                stream.stop_stream()
                stream.close()
                p.terminate()
                
                # 保存 WAV 文件
                wf = wave.open(output_path, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(p.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))
                wf.close()
                
                return {
                    "success": True,
                    "audio_file": output_path,
                    "duration": duration,
                    "message": "录音成功"
                }
                
            except ImportError:
                return {
                    "success": False,
                    "message": "PyAudio 未安装，无法录音。请使用浏览器端语音识别。",
                    "audio_file": None
                }
            except Exception as e:
                return {
                    "success": False,
                    "message": f"录音失败: {str(e)}",
                    "audio_file": None
                }
                
        except Exception as e:
            return {
                "success": False,
                "message": f"录音错误: {str(e)}",
                "audio_file": None
            }


# 全局单例
_voice_engine = None

def get_voice_engine() -> VoiceInputEngine:
    """获取全局语音引擎实例"""
    global _voice_engine
    if _voice_engine is None:
        _voice_engine = VoiceInputEngine()
    return _voice_engine


# 便捷函数
def get_available_engines() -> Dict:
    """获取可用引擎列表"""
    engine = get_voice_engine()
    return engine.get_available_engines()


def record_audio(duration: int = 5, output_path: Optional[str] = None) -> Dict:
    """录制音频"""
    engine = get_voice_engine()
    return engine.record_audio(duration, output_path)


def recognize_microphone(timeout: int = 5, language: str = 'zh-CN') -> Dict:
    """从麦克风实时识别"""
    engine_obj = get_voice_engine()
    result = engine_obj.recognize_microphone(timeout, language)
    return result.to_dict()

def recognize_audio(audio_path: str, engine: Optional[str] = None) -> Dict:
    """识别音频文件"""
    engine_obj = get_voice_engine()
    result = engine_obj.recognize_audio_file(audio_path, engine)
    return result.to_dict()


# 测试代码
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Koto 语音输入引擎测试")
    logger.info("=" * 60)
    
    # 获取引擎
    engine = get_voice_engine()
    
    # 显示可用引擎
    engines_info = engine.get_available_engines()
    logger.info(f"\n主引擎: {engines_info['primary']}")
    logger.info(f"\n可用引擎列表:")
    for eng in engines_info['engines']:
        primary_mark = " ★" if eng['is_primary'] else ""
        logger.info(f"  • {eng['name']}{primary_mark}")
        logger.info(f"    {eng['description']}")
    
    logger.info("\n" + "=" * 60)
    logger.info("✓ 语音输入引擎初始化成功")
    logger.info("=" * 60)

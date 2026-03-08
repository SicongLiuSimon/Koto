"""
Koto 快速本地语音识别模块
优先使用 Vosk 离线识别（无需网络），降级到 Google Speech API
"""
import os
import sys
import json
import time
import re
import struct
import tempfile
import threading
import queue
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class VoiceResult:
    """语音识别结果"""
    success: bool
    text: str = ""
    engine: str = ""
    message: str = ""
    confidence: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "text": self.text,
            "engine": self.engine,
            "message": self.message,
            "confidence": self.confidence
        }


def _clean_chinese_text(text: str) -> str:
    """清理中文文本，去除不必要的空格"""
    if not text:
        return text
    chinese_pattern = r'[\u4e00-\u9fff]'
    result = re.sub(f'({chinese_pattern})\\s+({chinese_pattern})', r'\1\2', text)
    while re.search(f'({chinese_pattern})\\s+({chinese_pattern})', result):
        result = re.sub(f'({chinese_pattern})\\s+({chinese_pattern})', r'\1\2', result)
    return result.strip()


class FastVoiceRecognizer:
    """快速本地语音识别器 - 优先Vosk离线，降级Google"""
    
    def __init__(self):
        self.available_engines = []
        self.primary_engine = None
        self.vosk_model = None
        self.vosk_model_path = None
        
        # 预初始化缓存
        self._pyaudio_instance = None
        self._sr_recognizer = None
        self._vosk_model_loading = False
        self._init_lock = threading.Lock()
        
        self._detect_engines()
        
        # 后台预初始化麦克风和模型（不阻塞）
        self._start_background_init()
    
    def _detect_engines(self):
        """检测可用的语音引擎 - 优先Vosk本地离线"""
        print("\n[快速语音] 检测可用引擎...")
        
        is_frozen = getattr(sys, 'frozen', False)
        
        # 1. Vosk 离线识别（所有环境首选）
        if self._check_vosk():
            self.available_engines.append("vosk")
            self.primary_engine = "vosk"
            print("  ✅ Vosk离线识别可用（推荐，无需网络）")
        
        # 2. Win32 SAPI（Windows内置离线备用）
        if self._check_win32_sapi():
            self.available_engines.append("win32_sapi")
            if not self.primary_engine:
                self.primary_engine = "win32_sapi"
            print("  ✅ Windows SAPI可用（本地离线）")
        
        # 3. Windows语音识别 + speech_recognition（本地麦克风，Google API云端）
        if self._check_windows_sapi():
            self.available_engines.append("windows_sapi")
            if not self.primary_engine:
                self.primary_engine = "windows_sapi"
            print("  ✅ Windows语音识别可用（本地录音，须网络识别）")
        
        # 4. speech_recognition库（仅本地录音，Google API已禁用）
        if self._check_speech_recognition():
            self.available_engines.append("speech_recognition")
            if not self.primary_engine:
                self.primary_engine = "speech_recognition"
            print("  ✅ speech_recognition可用")
        
        if not self.available_engines:
            print("  ⚠️  无可用引擎，语音功能将受限")
            self.available_engines.append("offline")
            self.primary_engine = "offline"
        
        print(f"  主引擎: {self.primary_engine}")
    
    def _check_vosk(self) -> bool:
        """检查Vosk离线识别是否可用（所有环境均支持）"""
        try:
            from vosk import Model, KaldiRecognizer
            import pyaudio
            
            # 模型搜索路径：开发环境 & 打包环境都覆盖
            if getattr(sys, 'frozen', False):
                # PyInstaller 打包：相对于 exe 同级目录
                exe_dir = os.path.dirname(sys.executable)
                roots = [exe_dir, os.path.join(exe_dir, '_internal')]
            else:
                roots = [os.path.dirname(__file__)]
            
            model_names = [
                "vosk-model-small-cn-0.22",
                "vosk-model-small-cn",
                "vosk-model-cn-0.22",
                "vosk-model-cn",
            ]
            
            for root in roots:
                for rel in ["../models", "models"]:
                    for name in model_names:
                        path = os.path.abspath(os.path.join(root, rel, name))
                        if os.path.exists(path) and os.path.isdir(path):
                            self.vosk_model_path = path
                            return True
            
            # 没找到模型 → 尝试后台自动下载
            self._schedule_vosk_download()
            return False
        except ImportError:
            print("  ℹ️  vosk 未安装，运行: pip install vosk")
            return False
        except Exception:
            return False
    
    def _schedule_vosk_download(self):
        """后台线程自动下载 vosk-model-small-cn-0.22 (~50MB)"""
        import threading
        
        def _do_download():
            import urllib.request
            import zipfile
            
            # 确定目标目录
            base = os.path.dirname(__file__)
            models_dir = os.path.abspath(os.path.join(base, "..", "models"))
            os.makedirs(models_dir, exist_ok=True)
            
            model_name = "vosk-model-small-cn-0.22"
            target_dir = os.path.join(models_dir, model_name)
            if os.path.exists(target_dir):
                return  # 已下载，不重复
            
            zip_path = os.path.join(models_dir, f"{model_name}.zip")
            url = f"https://alphacephei.com/vosk/models/{model_name}.zip"
            
            try:
                print(f"[Vosk] 🔽 开始下载中文离线模型 (~50MB)...")
                print(f"[Vosk] URL: {url}")
                urllib.request.urlretrieve(url, zip_path)
                print(f"[Vosk] 📦 解压模型到 {models_dir}...")
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    zf.extractall(models_dir)
                os.remove(zip_path)
                print(f"[Vosk] ✅ 模型下载完成，重启应用生效: {target_dir}")
            except Exception as e:
                print(f"[Vosk] ❌ 模型下载失败: {e}")
                print(f"[Vosk]    手动下载: {url}")
                print(f"[Vosk]    解压到: {models_dir}")
                if os.path.exists(zip_path):
                    try: os.remove(zip_path)
                    except: pass
        
        print("[Vosk] 📥 未找到本地模型，启动后台自动下载...")
        t = threading.Thread(target=_do_download, daemon=True)
        t.start()
    
    def _start_background_init(self):
        """后台预初始化音频硬件和模型"""
        def init_thread():
            try:
                # 预加载Vosk模型（如果可用）
                if self.primary_engine == "vosk" and self.vosk_model_path:
                    self._load_vosk_model_async()
                
                # 预初始化speech_recognition
                if "speech_recognition" in self.available_engines:
                    self._init_sr_recognizer()
            except Exception as e:
                print(f"[快速语音] 后台初始化出错（非严重）: {e}")
        
        # 只在主引擎是vosk或有sr时才后台初始化
        if self.primary_engine == "vosk" or "speech_recognition" in self.available_engines:
            init_thread_obj = threading.Thread(target=init_thread, daemon=True)
            init_thread_obj.start()
    
    def _load_vosk_model_async(self):
        """异步加载Vosk模型"""
        if self._vosk_model_loading or self.vosk_model:
            return
        
        with self._init_lock:
            if self.vosk_model or not self.vosk_model_path:
                return
            
            try:
                self._vosk_model_loading = True
                from vosk import Model, SetLogLevel
                SetLogLevel(-1)
                print(f"[快速语音] 后台加载Vosk模型: {self.vosk_model_path}")
                self.vosk_model = Model(self.vosk_model_path)
            except Exception as e:
                print(f"[快速语音] Vosk模型加载失败: {e}")
            finally:
                self._vosk_model_loading = False
    
    def _load_vosk_model(self):
        """加载Vosk模型（即时加载，如果后台未完成）"""
        if self.vosk_model is None and self.vosk_model_path:
            with self._init_lock:
                if self.vosk_model is None:
                    from vosk import Model, SetLogLevel
                    SetLogLevel(-1)
                    print(f"[快速语音] 立即加载Vosk模型: {self.vosk_model_path}")
                    self.vosk_model = Model(self.vosk_model_path)
    
    def _init_sr_recognizer(self):
        """预初始化speech_recognition识别器"""
        if self._sr_recognizer is None:
            try:
                import speech_recognition as sr
                self._sr_recognizer = sr.Recognizer()
                # 预设参数
                self._sr_recognizer.energy_threshold = 300  # 更敏感
                self._sr_recognizer.dynamic_energy_threshold = True
                self._sr_recognizer.dynamic_energy_adjustment_damping = 0.15
                self._sr_recognizer.pause_threshold = 0.3  # 更快检测
                self._sr_recognizer.non_speaking_duration = 0.2  # 更快
            except Exception as e:
                print(f"[快速语音] 初始化speech_recognition失败: {e}")
    
    def get_sr_recognizer(self):
        """获取或创建speech_recognition识别器"""
        if self._sr_recognizer is None:
            self._init_sr_recognizer()
        return self._sr_recognizer
    
    def _check_windows_sapi(self) -> bool:
        """检查Windows SAPI是否可用"""
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                pass
            return True
        except:
            return False
    
    def _check_speech_recognition(self) -> bool:
        """检查speech_recognition库"""
        try:
            import speech_recognition as sr
            return True
        except ImportError:
            return False
    
    def _check_win32_sapi(self) -> bool:
        """检查Win32 SAPI COM接口（带麦克风可用性验证）"""
        if sys.platform != 'win32':
            return False
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            # 验证 SpSharedRecognizer 可创建
            reco = win32com.client.Dispatch("SAPI.SpSharedRecognizer")
            return reco is not None
        except Exception:
            return False
    
    def recognize(self, timeout: int = 5, language: str = 'zh-CN') -> VoiceResult:
        """快速识别语音 - 优先Vosk离线，禁用云端Google API"""
        # 优先Vosk离线识别
        if self.primary_engine == "vosk":
            result = self._recognize_with_vosk(timeout, language)
            if result.success:
                return result
            print("[快速语音] Vosk失败，降级Win32 SAPI...")
        
        # 降级：Win32 SAPI（Windows内置，完全离线）
        if "win32_sapi" in self.available_engines:
            return self._recognize_with_win32(timeout, language)
        
        # 本地录音（无网络Google API）
        if "windows_sapi" in self.available_engines or "speech_recognition" in self.available_engines:
            return self._recognize_with_sr_local(timeout, language)
        
        return VoiceResult(
            success=False,
            message="无可用本地语音引擎。请安装 vosk 并下载中文模型。",
            engine="none"
        )
    
    def _recognize_with_vosk(self, timeout: int = 5, language: str = 'zh-CN') -> VoiceResult:
        """使用Vosk本地离线识别 - 无需网络（优化版：立即开始，灵敏度高）"""
        try:
            from vosk import KaldiRecognizer
            import pyaudio
            
            self._load_vosk_model()
            if not self.vosk_model:
                return VoiceResult(success=False, message="Vosk模型未加载", engine="vosk")
            
            RATE = 16000
            CHUNK = 800  # 0.05秒，更频繁更新（从1600改为800）
            
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
            
            print(f"[快速语音] 🎤 Vosk离线识别中（{timeout}秒）...")
            
            # 优化：更快的静音检测和识别停止
            silence_count = 0
            max_silence = 10  # 1秒静音停止（从15改为10）
            has_speech = False
            start_time = time.time()
            final_text = ""
            last_partial = ""
            energy_history = []
            
            try:
                while True:
                    # 改进：只在超过实际超时+2秒（而不是+10秒）才停止
                    if time.time() - start_time > timeout + 2:
                        break
                    
                    # 1. 立即读取音频（不等待）
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    
                    # 2. 立即计算能量（检测语音）
                    audio_data = struct.unpack(f'{len(data)//2}h', data)
                    energy = sum(abs(x) for x in audio_data) / len(audio_data)
                    
                    # 3. 更敏感的能量历史跟踪
                    energy_history.append(energy)
                    if len(energy_history) > 30:  # 从50改为30，更快反应
                        energy_history.pop(0)
                    
                    # 4. 动态阈值（更敏感）
                    if len(energy_history) > 5:  # 从10改为5，更快适应
                        avg_energy = sum(energy_history) / len(energy_history)
                        dynamic_threshold = max(200, avg_energy * 1.1)  # 从300和1.2改为200和1.1，更敏感
                    else:
                        dynamic_threshold = 250  # 从400改为250
                    
                    is_silent = energy < dynamic_threshold
                    
                    if rec.AcceptWaveform(data):
                        result = json.loads(rec.Result())
                        text = result.get("text", "").strip()
                        if text:
                            final_text = text
                            break
                    else:
                        partial = json.loads(rec.PartialResult())
                        partial_text = partial.get("partial", "").strip()
                        
                        if partial_text and partial_text != last_partial:
                            last_partial = partial_text
                            has_speech = True
                            silence_count = 0
                        elif has_speech:
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
                    
                    if not has_speech and (time.time() - start_time) > timeout:
                        break
                        
            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()
            
            final_text = _clean_chinese_text(final_text)
            
            if final_text:
                return VoiceResult(
                    success=True,
                    text=final_text,
                    engine="vosk",
                    message="离线识别成功",
                    confidence=0.85
                )
            else:
                return VoiceResult(
                    success=False,
                    message="未检测到语音" if not has_speech else "无法识别",
                    engine="vosk"
                )
                
        except Exception as e:
            print(f"[快速语音] Vosk识别错误: {e}")
            return VoiceResult(
                success=False,
                message=f"Vosk识别错误: {str(e)}",
                engine="vosk"
            )
    
    def _recognize_with_sr_google(self, timeout: int, language: str) -> VoiceResult:
        """使用speech_recognition库 + Google API（降级方案，需网络 - 优化版）"""
        try:
            import speech_recognition as sr
            
            # 使用预初始化的识别器或创建新的
            recognizer = self.get_sr_recognizer()
            if recognizer is None:
                recognizer = sr.Recognizer()
            
            # 优化参数：更敏感，更快响应
            recognizer.energy_threshold = 300  # 从400改为300，更敏感
            recognizer.dynamic_energy_threshold = True
            recognizer.dynamic_energy_adjustment_damping = 0.15
            recognizer.pause_threshold = 0.3  # 从0.4改为0.3，更快检测结束
            recognizer.non_speaking_duration = 0.2  # 从0.3改为0.2，更快反应
            
            with sr.Microphone(sample_rate=16000) as source:
                print(f"[快速语音] Google API识别（{timeout}秒）...")          
                # 优化：大大减少噪音检测时间（从0.15秒改为0.05秒）
                try:
                    recognizer.adjust_for_ambient_noise(source, duration=0.05)
                except:
                    # 如果调整失败，继续（不影响识别）
                    pass
                
                try:
                    # 立即监听（不再等待）
                    audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=min(8, timeout))
                    
                    try:
                        text = recognizer.recognize_google(audio, language=language)
                        return VoiceResult(
                            success=True,
                            text=_clean_chinese_text(text),
                            engine="google",
                            message="识别成功",
                            confidence=0.9
                        )
                    except sr.UnknownValueError:
                        return VoiceResult(
                            success=False,
                            message="未检测到语音，请靠近麦克风说话",
                            engine="google"
                        )
                    except sr.RequestError as e:
                        print(f"[快速语音] Google API不可用: {e}")
                        return VoiceResult(
                            success=False,
                            message="Google API不可用（网络问题），请使用离线识别",
                            engine="google_error"
                        )
                
                except sr.WaitTimeoutError:
                    return VoiceResult(
                        success=False,
                        message=f"等待超时（{timeout}秒内未检测到语音）",
                        engine="timeout"
                    )
        
        except Exception as e:
            print(f"[快速语音] 识别错误: {e}")
            return VoiceResult(
                success=False,
                message=f"识别失败: {str(e)}",
                engine="error"
            )
    
    def _recognize_with_sr_local(self, timeout: int, language: str) -> VoiceResult:
        """本地麦克风录音，不调用任何云端API（无网络输出）"""
        try:
            import speech_recognition as sr
            recognizer = self.get_sr_recognizer()
            if recognizer is None:
                recognizer = sr.Recognizer()
            recognizer.pause_threshold = 0.4
            recognizer.non_speaking_duration = 0.3
            with sr.Microphone(sample_rate=16000) as source:
                print(f"[快速语音] 本地麦克风录音（{timeout}秒）...")
                try:
                    recognizer.adjust_for_ambient_noise(source, duration=0.05)
                except Exception:
                    pass
                try:
                    audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=min(8, timeout))
                    # 尝试 Vosk via speech_recognition（若已安装）
                    try:
                        text = recognizer.recognize_vosk(audio, language="zh")
                        return VoiceResult(success=True, text=_clean_chinese_text(text),
                                           engine="vosk_sr", message="离线识别成功", confidence=0.8)
                    except Exception:
                        pass
                    return VoiceResult(success=False, message="本地引擎无法识别，请安装 vosk 模型", engine="sr_local")
                except sr.WaitTimeoutError:
                    return VoiceResult(success=False, message=f"超时未检测到语音", engine="timeout")
        except Exception as e:
            return VoiceResult(success=False, message=f"录音失败: {str(e)}", engine="error")

    def _recognize_with_win32(self, timeout: int, language: str) -> VoiceResult:
        """使用Windows SAPI SpSharedRecognizer进行本地离线语音识别"""
        try:
            import win32com.client
            import win32event
            import pythoncom
            import time

            # 每个线程必须初始化COM
            pythoncom.CoInitialize()

            # 创建共享识别引擎和上下文
            reco_engine = win32com.client.Dispatch("SAPI.SpSharedRecognizer")
            reco_context = reco_engine.CreateRecoContext()

            # 使用Win32事件通知（避免复杂的COM事件sink）
            reco_context.SetNotifyWin32Event()
            h_event = reco_context.GetNotifyEventHandle()

            # 加载听写语法（通用自由文本识别）
            grammar = reco_context.CreateGrammar(0)
            grammar.DictationLoad("", 0)       # 0 = SPGS_ENABLED
            grammar.DictationSetState(1)        # 1 = SPRS_ACTIVE

            # 激活识别引擎
            reco_engine.SetRecognizerState(1)   # 1 = SPRST_ACTIVE

            print(f"[Win32 SAPI] 等待语音输入 (最多{timeout}秒)...")
            timeout_ms = timeout * 1000
            result_text = None

            wait_result = win32event.WaitForSingleObject(h_event, timeout_ms)

            if wait_result == win32event.WAIT_OBJECT_0:
                # 处理等待队列中的COM消息
                pythoncom.PumpWaitingMessages()
                events = reco_context.GetEvents(16)  # 最多取16个事件

                SPEI_RECOGNITION = 18       # 最终识别结果
                SPEI_HYPOTHESIS = 15        # 识别假设（中间结果）

                for evt in events:
                    try:
                        if evt.EventId in (SPEI_RECOGNITION, SPEI_HYPOTHESIS):
                            reco_result = evt.RecoResult
                            phrase = reco_result.PhraseInfo
                            result_text = phrase.GetText(0, -1, True).strip()
                            if result_text:
                                break
                    except Exception:
                        continue

            # 停止识别，清理资源
            try:
                reco_engine.SetRecognizerState(0)   # 0 = SPRST_INACTIVE
                grammar.DictationSetState(0)
            except Exception:
                pass

            if result_text:
                print(f"[Win32 SAPI] 识别结果: {result_text}")
                return VoiceResult(success=True, text=result_text, engine="win32_sapi")
            else:
                return VoiceResult(
                    success=False,
                    message="未检测到语音输入，请重试",
                    engine="win32_sapi"
                )

        except Exception as e:
            import traceback
            print(f"[Win32 SAPI] 错误: {traceback.format_exc()}")
            return VoiceResult(
                success=False,
                message=f"Windows语音识别错误: {str(e)}",
                engine="win32_error"
            )
    
    def get_available_engines(self) -> Dict:
        """返回可用引擎列表"""
        engines = []
        
        for engine in self.available_engines:
            if engine == "vosk":
                engines.append({
                    "type": "vosk",
                    "name": "Vosk 离线识别",
                    "description": "完全离线，无需网络（推荐）",
                    "is_primary": engine == self.primary_engine
                })
            elif engine == "windows_sapi" or engine == "speech_recognition":
                engines.append({
                    "type": "google",
                    "name": "Google Speech API",
                    "description": "在线识别，效果好（需网络）",
                    "is_primary": engine == self.primary_engine
                })
            elif engine == "win32_sapi":
                engines.append({
                    "type": "windows",
                    "name": "Windows SAPI 本地识别",
                    "description": "离线本地识别，使用Windows内置语音引擎",
                    "is_primary": engine == self.primary_engine
                })
            elif engine == "offline":
                engines.append({
                    "type": "offline",
                    "name": "离线录音",
                    "description": "仅录音，需手动处理",
                    "is_primary": engine == self.primary_engine
                })
        
        return {
            "success": True,
            "engines": engines,
            "primary": self.primary_engine
        }


# 全局实例
_recognizer_instance = None

def get_recognizer() -> FastVoiceRecognizer:
    """获取识别器单例"""
    global _recognizer_instance
    if _recognizer_instance is None:
        _recognizer_instance = FastVoiceRecognizer()
    return _recognizer_instance

def get_fast_status() -> dict:
    """返回 voice_fast 引擎状态（供 /api/voice/stt_status 使用）"""
    rec = get_recognizer()
    engine = rec.primary_engine or "none"
    available = engine not in ("none", "offline")
    label = {
        "vosk":               "Vosk 本地离线",
        "win32_sapi":         "Windows SAPI 本地",
        "windows_sapi":       "SpeechRecognition 本地",
        "speech_recognition": "SpeechRecognition",
        "offline":            "无可用引擎",
        "none":               "无可用引擎",
    }.get(engine, engine)
    return {"available": available, "engine": engine, "label": label}


# API函数
def recognize_voice(timeout: int = 5, language: str = 'zh-CN') -> Dict:
    """识别语音（API接口）"""
    recognizer = get_recognizer()
    result = recognizer.recognize(timeout, language)
    return result.to_dict()


def get_available_engines() -> Dict:
    """获取可用引擎列表"""
    recognizer = get_recognizer()
    return recognizer.get_available_engines()


# 全局停止事件（由 /api/voice/stop 端点触发）
import threading as _threading
_stream_stop_event = _threading.Event()

def request_stop_streaming():
    """请求停止当前流式识别"""
    _stream_stop_event.set()

# 流式识别（优先Vosk离线）
def recognize_streaming(timeout: int = 10):
    """流式识别 - 生成器，优先使用Vosk离线识别；支持外部停止信号"""
    _stream_stop_event.clear()   # 每次开始前先清除旧的停止信号
    recognizer_obj = get_recognizer()
    
    yield {"type": "start", "message": "开始识别..."}
    
    # 优先使用Vosk离线识别（无需网络）
    if recognizer_obj.primary_engine == "vosk":
        yield from _streaming_vosk(recognizer_obj, timeout)
    else:
        yield from _streaming_google(timeout)


def _streaming_vosk(recognizer_obj, timeout: int):
    """Vosk离线流式识别 — 小块实时输出，带 keepalive ping"""
    try:
        from vosk import KaldiRecognizer
        import pyaudio

        recognizer_obj._load_vosk_model()
        if not recognizer_obj.vosk_model:
            yield {"type": "error", "message": "Vosk模型未加载", "engine": "vosk"}
            return

        RATE = 16000
        CHUNK = 800   # 50ms — 比原来 0.1s 更小，响应更快

        rec = KaldiRecognizer(recognizer_obj.vosk_model, RATE)
        rec.SetWords(True)

        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK
        )

        print(f"[流式语音] Vosk离线识别（{timeout}秒）...")

        silence_count = 0
        max_silence = 8     # 0.4秒静音即停（更快响应）
        has_speech = False
        start_time = time.time()
        last_partial = ""
        energy_history = []
        ping_interval = 0.5  # 每 0.5 秒发一个 keepalive ping（防止 SSE 超时）
        last_ping = start_time

        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed > timeout + 3:
                    break

                # 检查外部停止信号
                if _stream_stop_event.is_set():
                    # 停止请求 → 返回已识别的文本
                    if last_partial:
                        yield {"type": "final", "text": _clean_chinese_text(last_partial), "engine": "vosk"}
                    else:
                        yield {"type": "error", "message": "已停止", "engine": "vosk"}
                    return

                data = stream.read(CHUNK, exception_on_overflow=False)

                # 能量检测
                audio_data = struct.unpack(f'{len(data)//2}h', data)
                energy = sum(abs(x) for x in audio_data) / len(audio_data)
                energy_history.append(energy)
                if len(energy_history) > 30:
                    energy_history.pop(0)

                if len(energy_history) > 5:
                    avg_energy = sum(energy_history) / len(energy_history)
                    dynamic_threshold = max(200, avg_energy * 1.15)
                else:
                    dynamic_threshold = 300

                is_silent = energy < dynamic_threshold

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = _clean_chinese_text(result.get("text", ""))
                    if text:
                        yield {"type": "final", "text": text, "engine": "vosk"}
                        return
                else:
                    partial = json.loads(rec.PartialResult())
                    partial_text = _clean_chinese_text(partial.get("partial", ""))

                    if partial_text and partial_text != last_partial:
                        last_partial = partial_text
                        has_speech = True
                        silence_count = 0
                        yield {
                            "type": "partial",
                            "text": partial_text,
                            "elapsed": round(elapsed, 1),
                        }
                    elif has_speech:
                        if is_silent or not partial_text:
                            silence_count += 1
                            if silence_count >= max_silence:
                                result = json.loads(rec.FinalResult())
                                text = _clean_chinese_text(result.get("text", ""))
                                if not text:
                                    text = _clean_chinese_text(last_partial)
                                if text:
                                    yield {"type": "final", "text": text, "engine": "vosk"}
                                else:
                                    yield {"type": "error", "message": "无法识别", "engine": "vosk"}
                                return
                        else:
                            silence_count = 0

                # 未说话超时
                if not has_speech and elapsed > timeout:
                    yield {"type": "error", "message": "未检测到语音，请重试", "engine": "timeout"}
                    return

                # Keepalive ping — 避免反向代理或浏览器关闭空闲 SSE 连接
                now = time.time()
                if now - last_ping >= ping_interval:
                    last_ping = now
                    if not has_speech:
                        yield {"type": "ping", "elapsed": round(elapsed, 1)}

        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

        # 超时后仍有识别到的文本
        if has_speech and last_partial:
            yield {"type": "final", "text": _clean_chinese_text(last_partial), "engine": "vosk"}
        else:
            yield {"type": "error", "message": "识别超时", "engine": "vosk"}
            
    except Exception as e:
        print(f"[流式语音] Vosk错误: {e}")
        import traceback
        traceback.print_exc()
        yield {"type": "error", "message": f"识别失败: {str(e)}", "engine": "vosk"}


def _streaming_google(timeout: int):
    """Google API流式识别（降级方案）"""
    try:
        import speech_recognition as sr
        import queue
        
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 400
        recognizer.dynamic_energy_threshold = True
        recognizer.dynamic_energy_adjustment_damping = 0.15
        recognizer.pause_threshold = 0.4
        recognizer.non_speaking_duration = 0.3
        
        result_queue = queue.Queue()
        is_speaking = False
        last_text = ""
        
        with sr.Microphone(sample_rate=16000) as source:
            print(f"[流式语音] Google API识别（{timeout}秒）...")
            recognizer.adjust_for_ambient_noise(source, duration=0.15)
            
            start_time = time.time()
            accumulated_audio = []
            last_recognition_time = start_time
            final_text = ""
            silence_start = None
            
            stop_listening = recognizer.listen_in_background(
                source,
                lambda recognizer, audio: result_queue.put(audio),
                phrase_time_limit=2
            )
            
            try:
                while time.time() - start_time < timeout:
                    elapsed = time.time() - start_time
                    
                    try:
                        audio_chunk = result_queue.get(timeout=0.3)
                        accumulated_audio.append(audio_chunk)
                        is_speaking = True
                        silence_start = None
                        
                        if time.time() - last_recognition_time >= 0.8:
                            if accumulated_audio:
                                try:
                                    latest_audio = accumulated_audio[-1]
                                    text = recognizer.recognize_google(latest_audio, language='zh-CN')
                                    
                                    if text and text != last_text:
                                        final_text = text
                                        last_text = text
                                        yield {
                                            "type": "partial",
                                            "text": _clean_chinese_text(text),
                                            "elapsed": round(elapsed, 1),
                                            "is_final": False
                                        }
                                except sr.UnknownValueError:
                                    pass
                                except sr.RequestError as e:
                                    print(f"[流式] Google API错误: {e}")
                                    
                                last_recognition_time = time.time()
                        
                    except queue.Empty:
                        if is_speaking:
                            if silence_start is None:
                                silence_start = time.time()
                            elif time.time() - silence_start > 1.5:
                                break
                        
                        if elapsed < 2.0:
                            yield {
                                "type": "partial",
                                "text": "🎤 正在聆听...",
                                "elapsed": round(elapsed, 1)
                            }
                        elif not is_speaking:
                            yield {
                                "type": "partial",
                                "text": f"⏱️ 请说话... {int(elapsed)}s",
                                "elapsed": round(elapsed, 1)
                            }
                
            finally:
                stop_listening(wait_for_stop=False)
            
            if accumulated_audio and final_text:
                yield {"type": "final", "text": _clean_chinese_text(final_text), "engine": "google_streaming"}
            elif not is_speaking:
                yield {"type": "error", "message": "未检测到语音，请重试", "engine": "timeout"}
            else:
                yield {"type": "error", "message": "识别失败", "engine": "error"}
                    
    except Exception as e:
        print(f"[流式语音] Google错误: {e}")
        import traceback
        traceback.print_exc()
        yield {"type": "error", "message": f"识别失败: {str(e)}", "engine": "error"}


if __name__ == "__main__":
    # 测试
    print("🎤 快速语音识别测试")
    print("="*50)
    
    recognizer = get_recognizer()
    print(f"\n主引擎: {recognizer.primary_engine}")
    print(f"可用引擎: {recognizer.available_engines}")
    
    print("\n请在5秒内说话...")
    result = recognizer.recognize(timeout=5)
    
    print(f"\n结果: {result.to_dict()}")

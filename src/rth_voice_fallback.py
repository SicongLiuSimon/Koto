"""
Koto Runtime Hook — 语音模块安全降级
PyInstaller 打包后, 动态链接库（如 portaudio.dll）可能缺失。
本 hook 在程序启动前预先 patch 导入系统, 确保语音模块
import 失败时不会崩溃，而是以"不可用"状态运行。
"""
import sys
import types
import builtins

_real_import = builtins.__import__

# 可选二进制依赖列表：缺失时返回空 stub 而非抛异常
# 注意：speech_recognition 这里不列出，它是纯 Python 包，应正常打包并导入
_OPTIONAL_MODULES = {
    'pyaudio', '_portaudio',
    'vosk',
    'sounddevice', 'soundfile', '_sounddevice',
    'audioop',
    'win32com', 'win32com.client',
    'comtypes', 'comtypes.client',
    'edge_tts',
    'pytesseract',
}

def _safe_import(name, *args, **kwargs):
    """拦截可选模块导入；找不到时返回空 stub 模块"""
    top = name.split('.')[0]
    try:
        return _real_import(name, *args, **kwargs)
    except (ImportError, OSError, ModuleNotFoundError) as exc:
        if top in _OPTIONAL_MODULES:
            # 创建 stub 模块，防止 AttributeError
            stub = types.ModuleType(name)
            stub.__file__ = None
            stub.__loader__ = None
            stub.__spec__ = None
            stub.__package__ = top
            stub.__path__ = []
            sys.modules[name] = stub
            return stub
        raise

builtins.__import__ = _safe_import

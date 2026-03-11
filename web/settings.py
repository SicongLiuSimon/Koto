"""
Koto Settings Manager
用户设置管理模块 - 支持自定义存储路径和应用配置
"""
import os
import sys
import json
from pathlib import Path

# 默认设置文件位置
# 打包模式：config/ 紧邻 Koto.exe；开发模式：config/ 在 web/ 的父级
if getattr(sys, 'frozen', False):
    PROJECT_ROOT = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SETTINGS_FILE = os.path.join(PROJECT_ROOT, "config", "user_settings.json")

# 默认设置
DEFAULT_SETTINGS = {
    "storage": {
        "workspace_dir": os.path.join(PROJECT_ROOT, "workspace"),
        "documents_dir": os.path.join(PROJECT_ROOT, "workspace", "documents"),
        "images_dir": os.path.join(PROJECT_ROOT, "workspace", "images"),
        "chats_dir": os.path.join(PROJECT_ROOT, "chats"),
    },
    "appearance": {
        "theme": "dark",  # dark, light, auto
        "language": "zh-CN",  # zh-CN, en-US
        "font_size": "medium",  # small, medium, large
        "ui_zoom": 1.0,  # UI 缩放比例 0.7~1.5
    },
    "ai": {
        "default_model": "auto",
        "auto_execute_scripts": True,
        "voice_auto_send": False,  # 语音输入后自动发送
        "stream_response": True,
        "show_thinking": False,  # 显示思考过程（推理链）
        "enable_mini_game": True,  # 启用等待时的小游戏
    },
    "proxy": {
        "enabled": True,
        "auto_detect": True,
        "manual_proxy": "",
    },
    "model_mode": "cloud",
    "local_model": "",
}


class SettingsManager:
    """设置管理器"""
    
    _instance = None
    _settings = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_settings()
        return cls._instance
    
    def _load_settings(self):
        """加载设置"""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    self._settings = json.load(f)
                # 合并默认设置（处理新增的设置项）
                self._settings = self._merge_settings(DEFAULT_SETTINGS, self._settings)
            except Exception as e:
                print(f"加载设置失败: {e}")
                self._settings = DEFAULT_SETTINGS.copy()
        else:
            self._settings = DEFAULT_SETTINGS.copy()
            self._save_settings()
    
    def _merge_settings(self, default, current):
        """合并设置，保留用户设置，添加新的默认项"""
        result = default.copy()
        for key, value in current.items():
            if key in result:
                if isinstance(value, dict) and isinstance(result[key], dict):
                    result[key] = self._merge_settings(result[key], value)
                else:
                    result[key] = value
        return result
    
    def _save_settings(self):
        """保存设置"""
        try:
            os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"保存设置失败: {e}")
            return False
    
    def get(self, category, key=None):
        """获取设置"""
        if category in self._settings:
            if key is None:
                return self._settings[category]
            return self._settings[category].get(key)
        return None
    
    def set(self, category, key, value):
        """设置单个值"""
        if category not in self._settings:
            self._settings[category] = {}
        self._settings[category][key] = value
        return self._save_settings()
    
    def update(self, category, values):
        """更新一个分类的多个值"""
        if category not in self._settings:
            self._settings[category] = {}
        self._settings[category].update(values)
        return self._save_settings()
    
    def get_all(self):
        """获取所有设置"""
        return self._settings.copy()
    
    def reset(self, category=None):
        """重置设置"""
        if category:
            if category in DEFAULT_SETTINGS:
                self._settings[category] = DEFAULT_SETTINGS[category].copy()
        else:
            self._settings = DEFAULT_SETTINGS.copy()
        return self._save_settings()
    
    def ensure_directories(self):
        """确保所有存储目录存在"""
        storage = self._settings.get('storage', {})
        for key, path in storage.items():
            if path and not os.path.exists(path):
                try:
                    os.makedirs(path, exist_ok=True)
                except Exception as e:
                    print(f"创建目录失败 {path}: {e}")
    
    # 便捷方法
    @property
    def workspace_dir(self):
        return self.get('storage', 'workspace_dir')
    
    @property
    def documents_dir(self):
        return self.get('storage', 'documents_dir')
    
    @property
    def images_dir(self):
        return self.get('storage', 'images_dir')
    
    @property
    def chats_dir(self):
        return self.get('storage', 'chats_dir')
    
    @property
    def theme(self):
        return self.get('appearance', 'theme')


# 全局设置实例
settings = SettingsManager()

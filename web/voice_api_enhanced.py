"""
增强语音 API 模块 - 集成语音识别、命令处理、快捷操作

包含:
1. 增强的语音识别 (带重试、缓存、实时反馈)
2. 语音命令处理 (12个内置命令)
3. 全局快捷键监听 (Ctrl+Shift+V)
4. 配置管理
5. 统计和调试信息
"""

import json
import logging
import os
import threading
import time
from datetime import datetime

from flask import Blueprint, Response, jsonify, request

# 创建蓝图

logger = logging.getLogger(__name__)

voice_bp = Blueprint("voice", __name__, url_prefix="/api/voice")


# 全局状态管理
class VoiceSessionManager:
    """语音会话管理器"""

    _sessions = {}
    _lock = threading.Lock()

    @classmethod
    def create_session(cls, session_id):
        """创建新的语音会话"""
        with cls._lock:
            cls._sessions[session_id] = {
                "created_at": datetime.now().isoformat(),
                "status": "idle",
                "last_result": None,
                "retry_count": 0,
                "error_log": [],
            }
            return cls._sessions[session_id]

    @classmethod
    def get_session(cls, session_id):
        """获取会话信息"""
        return cls._sessions.get(session_id)

    @classmethod
    def update_session(cls, session_id, **kwargs):
        """更新会话状态"""
        with cls._lock:
            if session_id in cls._sessions:
                cls._sessions[session_id].update(kwargs)
                return True
            return False


# ================= 增强语音识别 API =================


@voice_bp.route("/recognize-enhanced", methods=["POST"])
def recognize_enhanced():
    """
    增强的语音识别 API

    功能:
    - 自动重试 (3次)
    - MD5 缓存 (1小时TTL)
    - 实时状态反馈
    - 卷音量检测
    - 详细统计

    请求参数:
    {
        "duration": 10,           // 录音时长(秒)
        "language": "zh-CN",      // 语言代码
        "use_cache": true,        // 是否使用缓存
        "stream": false           // 是否流式返回进度
    }
    """
    try:
        data = request.json or {}
        duration = min(int(data.get("duration", 10)), 60)  # 最多60秒
        language = data.get("language", "zh-CN")
        use_cache = data.get("use_cache", True)
        stream = data.get("stream", False)

        session_id = f"voice_{int(time.time() * 1000)}"
        session = VoiceSessionManager.create_session(session_id)

        # 导入增强模块
        try:
            from voice_recognition_enhanced import (
                RecognitionStatus,
                get_enhanced_recognizer,
            )
        except ImportError:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "增强语音模块不可用，请检查依赖",
                        "fallback_url": "/api/voice/listen",
                    }
                ),
                503,
            )

        recognizer = get_enhanced_recognizer()

        if stream:
            # 流式返回进度
            def generate_progress():
                try:
                    # 定义进度回调
                    def on_status(status_name, detail=""):
                        progress = {
                            "type": "progress",
                            "status": status_name,
                            "detail": detail,
                            "timestamp": datetime.now().isoformat(),
                        }
                        yield f"data: {json.dumps(progress)}\n\n"

                    def on_result(result):
                        # 流式发送结果
                        data = {
                            "type": "result",
                            "success": result.success,
                            "text": result.text,
                            "confidence": result.confidence,
                            "retry_count": result.retry_count,
                            "duration": result.duration,
                            "source": result.source,
                        }
                        yield f"data: {json.dumps(data)}\n\n"

                    def on_error(error):
                        error_data = {
                            "type": "error",
                            "error": str(error),
                            "timestamp": datetime.now().isoformat(),
                        }
                        yield f"data: {json.dumps(error_data)}\n\n"

                    # 注册回调
                    recognizer.on_status_changed = on_status
                    recognizer.on_result = on_result
                    recognizer.on_error = on_error

                    # 执行识别
                    result = recognizer.recognize_microphone(
                        duration=duration, language=language, use_cache=use_cache
                    )

                except Exception as e:
                    error_data = {
                        "type": "error",
                        "error": str(e),
                        "timestamp": datetime.now().isoformat(),
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"

            return Response(generate_progress(), mimetype="text/event-stream")

        else:
            # 一次性返回结果
            session["status"] = "listening"
            VoiceSessionManager.update_session(session_id, status="listening")

            result = recognizer.recognize_microphone(
                duration=duration, language=language, use_cache=use_cache
            )

            VoiceSessionManager.update_session(
                session_id,
                status="completed",
                last_result=result.__dict__ if result else None,
                retry_count=result.retry_count if result else 0,
            )

            return jsonify(
                {
                    "success": result.success if result else False,
                    "text": result.text if result else "",
                    "confidence": result.confidence if result else 0.0,
                    "retry_count": result.retry_count if result else 0,
                    "duration": result.duration if result else 0,
                    "source": result.source if result else "unknown",
                    "from_cache": (
                        result.from_cache if hasattr(result, "from_cache") else False
                    ),
                    "stats": {
                        "session_id": session_id,
                        "timestamp": datetime.now().isoformat(),
                    },
                }
            )

    except Exception as e:
        import traceback

        logger.error(f"[VOICE_API] 增强识别错误: {e}")
        traceback.print_exc()

        return (
            jsonify(
                {"success": False, "error": str(e), "fallback_url": "/api/voice/listen"}
            ),
            500,
        )


@voice_bp.route("/session/<session_id>", methods=["GET"])
def get_session_info(session_id):
    """获取语音会话的详细信息"""
    try:
        session = VoiceSessionManager.get_session(session_id)

        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        return jsonify({"success": True, "session": session})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ================= 语音命令 API =================


@voice_bp.route("/commands", methods=["GET"])
def list_commands():
    """获取所有可用的语音命令"""
    try:
        try:
            from voice_interaction import get_interaction_manager
        except ImportError:
            return jsonify({"success": False, "error": "语音交互模块不可用"}), 503

        manager = get_interaction_manager()
        processor = manager.get_command_processor()

        commands_list = []
        for cmd_name, cmd in processor.commands.items():
            commands_list.append(
                {
                    "name": cmd_name,
                    "keywords": cmd.keywords,
                    "description": cmd.description,
                    "enabled": cmd.enabled,
                    "action": (
                        cmd.action.__name__
                        if hasattr(cmd.action, "__name__")
                        else str(cmd.action)
                    ),
                }
            )

        return jsonify(
            {"success": True, "commands": commands_list, "count": len(commands_list)}
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@voice_bp.route("/execute-command", methods=["POST"])
def execute_command():
    """
    执行语音命令

    请求参数:
    {
        "command": "打开文档",  // 命令文本
        "params": {}            // 可选参数
    }
    """
    try:
        data = request.json or {}
        command_text = data.get("command", "")
        params = data.get("params", {})

        if not command_text:
            return jsonify({"success": False, "error": "命令文本为空"}), 400

        try:
            from voice_interaction import get_interaction_manager
        except ImportError:
            return jsonify({"success": False, "error": "语音交互模块不可用"}), 503

        manager = get_interaction_manager()
        processor = manager.get_command_processor()

        result = processor.execute_command(command_text, **params)

        return jsonify(
            {
                "success": result.get("success", False),
                "command": result.get("command"),
                "result": result.get("result"),
                "message": result.get("message"),
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        import traceback

        logger.error(f"[VOICE_API] 命令执行错误: {e}")
        traceback.print_exc()

        return jsonify({"success": False, "error": str(e)}), 500


# ================= 快捷键配置 API =================


@voice_bp.route("/hotkey/config", methods=["GET"])
def get_hotkey_config():
    """获取快捷键配置"""
    try:
        try:
            from voice_interaction import get_interaction_manager
        except ImportError:
            return jsonify({"success": False, "error": "语音交互模块不可用"}), 503

        manager = get_interaction_manager()

        return jsonify(
            {
                "success": True,
                "config": {
                    "hotkey": manager.config.get("hotkey", "ctrl+shift+v"),
                    "enabled": (
                        manager.listener.is_running()
                        if hasattr(manager, "listener")
                        else False
                    ),
                    "language": manager.config.get("language", "zh-CN"),
                    "max_retries": manager.config.get("max_retries", 3),
                    "timeout": manager.config.get("timeout", 10),
                    "cache_enabled": manager.config.get("cache_enabled", True),
                },
            }
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@voice_bp.route("/hotkey/config", methods=["POST"])
def set_hotkey_config():
    """
    设置快捷键配置

    请求参数:
    {
        "hotkey": "ctrl+shift+v",
        "language": "zh-CN",
        "max_retries": 3,
        "timeout": 10,
        "cache_enabled": true
    }
    """
    try:
        data = request.json or {}

        try:
            from voice_interaction import get_interaction_manager
        except ImportError:
            return jsonify({"success": False, "error": "语音交互模块不可用"}), 503

        manager = get_interaction_manager()

        # 更新配置
        if "hotkey" in data:
            manager.config["hotkey"] = data["hotkey"]
        if "language" in data:
            manager.config["language"] = data["language"]
        if "max_retries" in data:
            manager.config["max_retries"] = data["max_retries"]
        if "timeout" in data:
            manager.config["timeout"] = data["timeout"]
        if "cache_enabled" in data:
            manager.config["cache_enabled"] = data["cache_enabled"]

        # 保存配置
        manager.save_config()

        return jsonify(
            {"success": True, "message": "配置已更新", "config": manager.config}
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@voice_bp.route("/hotkey/start", methods=["POST"])
def start_hotkey_listener():
    """启动全局快捷键监听"""
    try:
        try:
            from voice_interaction import get_interaction_manager
        except ImportError:
            return jsonify({"success": False, "error": "语音交互模块不可用"}), 503

        manager = get_interaction_manager()

        if not hasattr(manager, "listener"):
            # 创建监听器
            from voice_interaction import GlobalHotkeyListener

            manager.listener = GlobalHotkeyListener(
                hotkey=manager.config.get("hotkey", "ctrl+shift+v"),
                callback=manager.on_hotkey_triggered,
            )

        if not manager.listener.is_running():
            manager.listener.start()
            return jsonify(
                {
                    "success": True,
                    "message": f"快捷键监听已启动 ({manager.config.get('hotkey', 'ctrl+shift+v')})",
                }
            )
        else:
            return jsonify({"success": False, "message": "快捷键监听已在运行"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@voice_bp.route("/hotkey/stop", methods=["POST"])
def stop_hotkey_listener():
    """停止全局快捷键监听"""
    try:
        try:
            from voice_interaction import get_interaction_manager
        except ImportError:
            return jsonify({"success": False, "error": "语音交互模块不可用"}), 503

        manager = get_interaction_manager()

        if hasattr(manager, "listener") and manager.listener.is_running():
            manager.listener.stop()
            return jsonify({"success": True, "message": "快捷键监听已停止"})
        else:
            return jsonify({"success": False, "message": "快捷键监听未运行"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ================= 统计和调试 API =================


@voice_bp.route("/stats", methods=["GET"])
def get_voice_stats():
    """获取语音识别统计信息"""
    try:
        try:
            from voice_recognition_enhanced import get_enhanced_recognizer
        except ImportError:
            return jsonify({"success": False, "error": "增强语音模块不可用"}), 503

        recognizer = get_enhanced_recognizer()
        stats = recognizer.get_statistics()

        return jsonify({"success": True, "stats": stats})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@voice_bp.route("/health", methods=["GET"])
def voice_health():
    """检查语音系统健康状态"""
    try:
        health_status = {
            "enhanced_recognizer": False,
            "voice_interaction": False,
            "pyaudio": False,
            "keyboard": False,
            "errors": [],
        }

        # 检查增强识别模块
        try:
            from voice_recognition_enhanced import get_enhanced_recognizer

            get_enhanced_recognizer()
            health_status["enhanced_recognizer"] = True
        except ImportError:
            health_status["errors"].append("增强语音模块未安装")
        except Exception as e:
            health_status["errors"].append(f"增强语音模块错误: {str(e)}")

        # 检查语音交互模块
        try:
            from voice_interaction import get_interaction_manager

            get_interaction_manager()
            health_status["voice_interaction"] = True
        except ImportError:
            health_status["errors"].append("语音交互模块未安装")
        except Exception as e:
            health_status["errors"].append(f"语音交互模块错误: {str(e)}")

        # 检查 PyAudio
        try:
            import pyaudio

            health_status["pyaudio"] = True
        except ImportError:
            health_status["errors"].append("PyAudio 未安装")

        # 检查 keyboard
        try:
            import keyboard

            health_status["keyboard"] = True
        except ImportError:
            health_status["errors"].append("keyboard 库未安装")

        all_ok = all(
            [
                health_status["enhanced_recognizer"],
                health_status["voice_interaction"],
                health_status["pyaudio"],
                health_status["keyboard"],
            ]
        )

        return jsonify(
            {
                "success": True,
                "status": health_status,
                "overall": "✅ 所有组件正常" if all_ok else "⚠️ 某些组件不可用",
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# 导出蓝图供app.py使用
__all__ = ["voice_bp"]

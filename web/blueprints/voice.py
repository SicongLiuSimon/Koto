"""
Voice & Speech blueprint.

Routes (voice):
  GET  /api/voice/engines       — List available voice engines
  POST /api/voice/record        — Record audio
  POST /api/voice/recognize     — Recognize audio file
  POST /api/voice/listen        — One-click microphone recognition
  GET  /api/voice/stream        — Streaming voice recognition (SSE)
  POST /api/voice/stop          — Stop current voice recognition
  GET  /api/voice/commands      — Built-in voice command list
  GET  /api/voice/stt_status    — Current STT engine status
  POST /api/voice/gemini_stt    — Unified STT entry (also /api/voice/stt)
  POST /api/voice/stt           — Alias for gemini_stt

Routes (speech):
  POST /api/speech/transcribe-file        — Transcribe audio file
  POST /api/speech/transcribe-microphone  — Record from mic and transcribe
  POST /api/speech/extract-summary        — Extract keywords & summary from text
"""

import logging

from flask import Blueprint, Response, jsonify, request, stream_with_context

_logger = logging.getLogger("koto.routes.voice")

voice_bp = Blueprint("voice_routes", __name__)


# ── lazy imports for app-level globals ────────────────────────────────────────


def _get_client():
    """Lazy import to avoid circular dependency with app.py."""
    from web.app import client

    return client


def _get_types():
    """Lazy import to avoid circular dependency with app.py."""
    from web.app import types

    return types


# ================= 语音识别 API (新架构) =================


@voice_bp.route("/api/voice/engines", methods=["GET"])
def voice_engines():
    """获取可用语音引擎列表"""
    try:
        from web.voice_fast import get_available_engines

        result = get_available_engines()
        return jsonify(result)
    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "engines": [],
                    "message": f"获取引擎列表失败: {str(e)}",
                }
            ),
            500,
        )


@voice_bp.route("/api/voice/record", methods=["POST"])
def voice_record():
    """录制音频"""
    try:
        data = request.json or {}
        duration = data.get("duration", 5)

        from web.voice_input import record_audio

        result = record_audio(duration=int(duration))

        return jsonify(result)
    except Exception as e:
        return (
            jsonify(
                {"success": False, "message": f"录音失败: {str(e)}", "audio_file": None}
            ),
            500,
        )


@voice_bp.route("/api/voice/recognize", methods=["POST"])
def voice_recognize():
    """识别音频文件"""
    try:
        data = request.json or {}
        audio_path = data.get("audio_path")
        engine = data.get("engine", None)

        if not audio_path:
            return jsonify({"success": False, "message": "缺少音频文件路径"}), 400

        from web.voice_input import recognize_audio

        result = recognize_audio(audio_path, engine)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": f"识别失败: {str(e)}"}), 500


@voice_bp.route("/api/voice/listen", methods=["POST"])
def voice_listen():
    """一键麦克风识别（本地模式 - 优化版：立即启动）"""
    try:
        data = request.json or {}
        timeout = data.get("timeout", 5)
        language = data.get("language", "zh-CN")

        # 使用快速本地识别
        from web.voice_fast import recognize_voice

        result = recognize_voice(timeout=int(timeout), language=language)

        # 优化：设置响应头加快传输
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-cache, no-store"
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response

    except Exception as e:
        import traceback

        traceback.print_exc()
        response = jsonify(
            {
                "success": False,
                "text": "",
                "message": f"语音识别出错: {str(e)}",
                "engine": "error",
            }
        )
        response.status_code = 500
        response.headers["Cache-Control"] = "no-cache"
        return response


@voice_bp.route("/api/voice/stream")
def voice_stream():
    """流式语音识别 - Vosk 本地离线，实时返回部分/最终结果（SSE）"""
    import json as _json

    @stream_with_context
    def generate():
        try:
            from web.voice_engine import recognize_stream

            for event in recognize_stream(max_wait=8.0, max_speech=30.0):
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("final", "error"):
                    break
        except GeneratorExit:
            pass
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )


@voice_bp.route("/api/voice/stop", methods=["POST"])
def voice_stop():
    """停止当前语音识别流（通知 voice_engine 停止）"""
    try:
        from web.voice_engine import request_stop

        request_stop()
    except Exception:
        pass
    return jsonify({"success": True, "message": "已发送停止信号"})


@voice_bp.route("/api/voice/commands", methods=["GET"])
def voice_commands():
    """返回内置语音命令列表（供语音面板展示）"""
    commands = [
        {"name": "发送消息", "description": "说出消息后自动发送", "keyword": ""},
        {"name": "新对话", "description": "说'新对话'开始新聊天", "keyword": "新对话"},
        {"name": "清空输入", "description": "说'清空'清除输入框", "keyword": "清空"},
        {"name": "重新识别", "description": "再次点击麦克风重新说", "keyword": ""},
    ]
    return jsonify({"success": True, "commands": commands})


@voice_bp.route("/api/voice/stt_status", methods=["GET"])
def voice_stt_status():
    """查询当前语音引擎状态（使用新 voice_engine）。"""
    try:
        from web.voice_engine import get_status

        fast = get_status()
    except Exception:
        fast = {"available": False, "engine": "unavailable", "label": "无引擎"}

    return jsonify(
        {
            "fast": fast,
            "local": fast,  # 兼容前端旧字段
            "active": fast.get("engine", "none"),
        }
    )


@voice_bp.route("/api/voice/gemini_stt", methods=["POST"])
@voice_bp.route("/api/voice/stt", methods=["POST"])  # 统一入口别名
def voice_gemini_stt():
    """
    统一语音转文字 (STT) 入口：本地 Whisper 优先 → Gemini STT 备用。

    - 若安装了 faster-whisper 或 openai-whisper：完全本地转写，无 API 消耗
    - 否则：发送至 Gemini gemini-2.0-flash-lite 转写
    - 始终返回 JSON，绝不返回 HTML 错误页面。
    """
    try:
        data = request.get_json(silent=True) or {}
        audio_b64 = data.get("audio", "")
        mime_type = data.get("mime", "audio/webm")

        if not audio_b64:
            return (
                jsonify({"success": False, "text": "", "message": "缺少 audio 字段"}),
                400,
            )

        import base64 as _b64

        try:
            audio_bytes = _b64.b64decode(audio_b64)
        except Exception:
            return (
                jsonify(
                    {"success": False, "text": "", "message": "音频 base64 解码失败"}
                ),
                400,
            )

        if len(audio_bytes) < 300:
            return jsonify(
                {"success": False, "text": "", "message": "录音太短，请重新说话"}
            )

        _logger.debug(f"[STT] 收到音频 {len(audio_bytes)/1024:.1f}KB  MIME={mime_type}")

        # ── 优先尝试本地 Whisper ──────────────────────────────────────────────
        try:
            from web.local_stt import is_available, transcribe

            if is_available():
                ok, text, engine = transcribe(audio_bytes, mime_type)
                if ok and text:
                    return jsonify(
                        {
                            "success": True,
                            "text": text,
                            "engine": engine,
                            "message": "识别成功（本地）",
                        }
                    )
                # 本地识别出空文本 → 也直接返回（不回退，避免重复计费）
                return jsonify(
                    {
                        "success": False,
                        "text": "",
                        "engine": engine,
                        "message": "未检测到语音",
                    }
                )
        except Exception as _le:
            _logger.debug(f"[STT] 本地 STT 异常，回退 Gemini: {_le}")

        # ── 回退：Gemini STT ──────────────────────────────────────────────────
        client = _get_client()
        types = _get_types()

        if client is None:
            return (
                jsonify(
                    {
                        "success": False,
                        "text": "",
                        "message": "Gemini 客户端未初始化，请检查 API Key；"
                        "或安装 faster-whisper 使用本地识别",
                    }
                ),
                503,
            )

        stt_model = "gemini-2.0-flash-lite"
        prompt_parts = [
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            types.Part.from_text(
                text=(
                    "请将上面音频中的语音内容完整转写为文字。"
                    "只输出转写结果，不要加任何解释、标点修饰或前缀（如「转写：」等）。"
                    "如果听不清或没有语音，只输出空字符串。"
                )
            ),
        ]

        resp = client.models.generate_content(
            model=stt_model,
            contents=[types.Content(role="user", parts=prompt_parts)],
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=512),
        )

        text = (resp.text or "").strip()
        for prefix in ("转写：", "转写:", "识别：", "识别:", "文字：", "文字:"):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()

        _logger.debug(f"[STT] Gemini 识别结果: {text[:80]!r}")
        return jsonify(
            {
                "success": bool(text),
                "text": text,
                "engine": f"Gemini/{stt_model}",
                "message": "识别成功" if text else "未检测到语音内容",
            }
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify(
                {"success": False, "text": "", "message": f"STT 失败: {str(e)[:200]}"}
            ),
            500,
        )


# ================= 语音转写 API =================


@voice_bp.route("/api/speech/transcribe-file", methods=["POST"])
def speech_transcribe_file():
    """转写音频文件"""
    try:
        from web.speech_transcriber import SpeechTranscriber

        data = request.json
        audio_path = data.get("audio_path")
        language = data.get("language", "zh-CN")
        output_format = data.get("output_format", "txt")
        title = data.get("title")
        auto_summary = data.get("auto_summary", True)

        if not audio_path:
            return jsonify({"success": False, "error": "缺少audio_path参数"}), 400

        transcriber = SpeechTranscriber()
        result = transcriber.process_audio_complete(
            audio_path,
            language=language,
            output_format=output_format,
            title=title,
            auto_summary=auto_summary,
        )

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@voice_bp.route("/api/speech/transcribe-microphone", methods=["POST"])
def speech_transcribe_microphone():
    """从麦克风录音并转写"""
    try:
        from web.speech_transcriber import SpeechTranscriber

        data = request.json
        duration = data.get("duration", 30)
        language = data.get("language", "zh-CN")
        output_format = data.get("output_format", "txt")
        title = data.get("title")

        transcriber = SpeechTranscriber()

        # 录音
        mic_result = transcriber.transcribe_microphone(
            duration=duration, language=language
        )

        if not mic_result["success"]:
            return jsonify(mic_result), 400

        text = mic_result["text"]

        # 提取总结
        summary_result = transcriber.extract_keywords_and_summary(text)
        keywords = (
            summary_result.get("keywords", []) if summary_result["success"] else []
        )
        summary = summary_result.get("summary", []) if summary_result["success"] else []

        # 生成文档
        output_file = transcriber.generate_transcript_document(
            text,
            keywords=keywords,
            summary=summary,
            title=title,
            output_format=output_format,
        )

        return jsonify(
            {
                "success": True,
                "text": text,
                "keywords": keywords,
                "summary": summary,
                "output_file": output_file,
                "format": output_format,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@voice_bp.route("/api/speech/extract-summary", methods=["POST"])
def speech_extract_summary():
    """从文本提取关键词和总结"""
    try:
        from web.speech_transcriber import SpeechTranscriber

        data = request.json
        text = data.get("text")
        max_keywords = data.get("max_keywords", 10)

        if not text:
            return jsonify({"success": False, "error": "缺少text参数"}), 400

        transcriber = SpeechTranscriber()
        result = transcriber.extract_keywords_and_summary(
            text, max_keywords=max_keywords
        )

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

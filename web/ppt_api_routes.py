#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PPT 编辑 API - 支持后生成编辑功能
集成到 web/app.py 或作为独立蓝图使用
"""

import json

# google.genai 延迟到路由函数内部加载，避免启动时加载 (~4.7s)
import os
import re
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from web.ppt_generator import PPTGenerator
from web.ppt_session_manager import get_ppt_session_manager

ppt_api_bp = Blueprint("ppt_api", __name__, url_prefix="/api/ppt")


@ppt_api_bp.route("/sessions", methods=["GET"])
def list_sessions():
    """列出用户的 PPT 会话"""
    try:
        mgr = get_ppt_session_manager()
        sessions = mgr.list_sessions(limit=20)
        return jsonify({"success": True, "data": sessions})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ppt_api_bp.route("/session/<session_id>", methods=["GET"])
def get_session(session_id):
    """获取单个会话的详细数据"""
    try:
        mgr = get_ppt_session_manager()
        session = mgr.load_session(session_id)

        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        # 返回会话数据（不包含过长的上下文）
        return jsonify(
            {
                "success": True,
                "data": {
                    "session_id": session.get("session_id"),
                    "title": session.get("title"),
                    "user_input": session.get("user_input"),
                    "theme": session.get("theme"),
                    "status": session.get("status"),
                    "created_at": session.get("created_at"),
                    "ppt_data": session.get("ppt_data"),
                    "ppt_file_path": session.get("ppt_file_path"),
                },
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ppt_api_bp.route("/slide/<session_id>/<int:slide_index>", methods=["PUT"])
def update_slide(session_id, slide_index):
    """编辑单个幻灯片内容"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "缺少请求体"}), 400

        mgr = get_ppt_session_manager()

        # 获取会话
        session = mgr.load_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        # 更新幻灯片
        updated_slide = {
            "title": data.get("title"),
            "type": data.get("type"),
            "points": data.get("points", []),
            "content": data.get("content", []),
        }

        # 处理子主题（用于 overview/comparison）
        if "subsections" in data:
            updated_slide["subsections"] = data.get("subsections", [])

        success = mgr.update_slide(session_id, slide_index, updated_slide)

        if not success:
            return jsonify({"success": False, "error": "幻灯片更新失败"}), 400

        return jsonify(
            {"success": True, "message": "幻灯片已更新", "slide_index": slide_index}
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ppt_api_bp.route("/reorder/<session_id>", methods=["POST"])
def reorder_slides(session_id):
    """重排幻灯片顺序"""
    try:
        data = request.get_json()
        new_order = data.get("order", [])

        if not new_order:
            return jsonify({"success": False, "error": "缺少 order 参数"}), 400

        mgr = get_ppt_session_manager()
        success = mgr.reorder_slides(session_id, new_order)

        if not success:
            return jsonify({"success": False, "error": "重排失败"}), 400

        return jsonify({"success": True, "message": "幻灯片顺序已更新"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ppt_api_bp.route("/slide/<session_id>/<int:slide_index>", methods=["DELETE"])
def delete_slide(session_id, slide_index):
    """删除单个幻灯片"""
    try:
        mgr = get_ppt_session_manager()
        success = mgr.delete_slide(session_id, slide_index)

        if not success:
            return jsonify({"success": False, "error": "幻灯片删除失败"}), 400

        return jsonify({"success": True, "message": "幻灯片已删除"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ppt_api_bp.route("/insert/<session_id>/<int:slide_index>", methods=["POST"])
def insert_slide(session_id, slide_index):
    """在指定位置插入新幻灯片"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "缺少请求体"}), 400

        mgr = get_ppt_session_manager()

        new_slide = {
            "title": data.get("title", "新幻灯片"),
            "type": data.get("type", "detail"),
            "points": data.get("points", []),
            "content": data.get("content", []),
        }

        success = mgr.insert_slide(session_id, slide_index, new_slide)

        if not success:
            return jsonify({"success": False, "error": "幻灯片插入失败"}), 400

        return jsonify(
            {"success": True, "message": "幻灯片已插入", "slide_index": slide_index}
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@ppt_api_bp.route("/regenerate/<session_id>/<int:slide_index>", methods=["POST"])
def regenerate_slide(session_id, slide_index):
    """
    重新生成单个幻灯片内容
    使用原始的搜索上下文、研究上下文来重写该页

    POST 参数（可选）：
    {
        "prompt": "可选，覆盖自动生成的提示词",
        "style": "detail/overview/highlight/comparison"
    }
    """
    try:
        from google import genai
        from google.genai import types as genai_types

        # 获取会话数据
        mgr = get_ppt_session_manager()
        session = mgr.load_session(session_id)

        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        ppt_data = session.get("ppt_data", {})
        slides = ppt_data.get("slides", [])

        if not (0 <= slide_index < len(slides)):
            return (
                jsonify({"success": False, "error": f"幻灯片索引越界: {slide_index}"}),
                400,
            )

        # 获取要重新生成的幻灯片
        slide = slides[slide_index]
        slide_title = slide.get("title", "")
        slide_type = slide.get("type", "detail")

        # 构建重生成提示词
        request_data = request.get_json() or {}
        custom_prompt = request_data.get("prompt")

        search_context = session.get("search_context", "")
        research_context = session.get("research_context", "")
        ppt_title = ppt_data.get("title", "")

        if custom_prompt:
            regenerate_prompt = custom_prompt
        else:
            regenerate_prompt = (
                f"你是PPT内容撰写专家。请为以下幻灯片重新生成高质量内容。\n\n"
                f"PPT主题: {ppt_title}\n"
                f"幻灯片标题: {slide_title}\n"
                f"幻灯片类型: {slide_type}\n\n"
                f"要求:\n"
                f"1. {slide_type} 类型应遵循该格式规范\n"
                f"2. 每个要点 30-80 字，包含具体数据或案例\n"
                f"3. 禁止模糊表述（'显著增长' → '据 IDC 数据，增长 35%'）\n"
                f"4. 优先使用下方参考资料中的数据\n\n"
            )

            if slide_type == "detail":
                regenerate_prompt += (
                    "请生成 4-6 个要点，格式: - **关键词** — 详细解释\n"
                    "回复格式:\n"
                    "```json\n"
                    '{"points": ["...", "...", ...]}\n'
                    "```\n\n"
                )
            elif slide_type == "overview":
                regenerate_prompt += (
                    "请生成 2-4 个子主题，每个子主题 2-4 个要点\n"
                    "回复格式:\n"
                    "```json\n"
                    '{"subsections": [{"subtitle": "...", "points": ["..."]}, ...]}\n'
                    "```\n\n"
                )

            if search_context:
                regenerate_prompt += f"【参考资料】\n{search_context[:5000]}\n\n"
            if research_context:
                regenerate_prompt += f"【研究分析】\n{research_context[:5000]}\n"

        # 调用 Gemini 生成
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=regenerate_prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.5, max_output_tokens=4096
            ),
        )

        if not response.text:
            return jsonify({"success": False, "error": "生成失败，未获得响应"}), 500

        # 解析生成的内容
        import re

        json_match = re.search(r"\{.*\}", response.text, re.DOTALL)

        if not json_match:
            return jsonify({"success": False, "error": "生成内容格式错误"}), 400

        try:
            new_content = json.loads(json_match.group())
        except json.JSONDecodeError:
            return jsonify({"success": False, "error": "生成内容解析失败"}), 400

        # 更新幻灯片内容
        if "points" in new_content:
            slide["points"] = new_content["points"]
            slide["content"] = new_content["points"]

        if "subsections" in new_content:
            slide["subsections"] = new_content["subsections"]
            if slide_type == "comparison" and len(new_content["subsections"]) >= 2:
                slide["left"] = new_content["subsections"][0]
                slide["right"] = new_content["subsections"][1]

        # 保存更新
        ppt_data["slides"][slide_index] = slide
        session["ppt_data"] = ppt_data

        session_file = os.path.join(mgr.storage_dir, f"{session_id}.json")
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

        return jsonify(
            {
                "success": True,
                "message": "幻灯片已重新生成",
                "slide_index": slide_index,
                "content": new_content,
            }
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "error": f"重生成异常: {str(e)}"}), 500


@ppt_api_bp.route("/render/<session_id>", methods=["POST"])
def render_pptx(session_id):
    """
    根据当前编辑的数据重新渲染 PPTX 文件

    POST 参数（可选）：
    {
        "theme": "business/tech/creative/minimal"
    }
    """
    try:
        mgr = get_ppt_session_manager()
        session = mgr.load_session(session_id)

        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        ppt_data = session.get("ppt_data")
        if not ppt_data:
            return jsonify({"success": False, "error": "PPT 数据不存在"}), 400

        request_data = request.get_json() or {}
        theme = request_data.get("theme", session.get("theme", "business"))

        # 生成新的 PPTX 文件
        from web.ppt_generator import PPTGenerator

        ppt_title = ppt_data.get("title", "演示文稿")

        ppt_gen = PPTGenerator(theme=theme)

        # 使用原来的路径或生成新路径
        original_path = session.get("ppt_file_path", "")
        if original_path:
            output_path = original_path
        else:
            from datetime import datetime

            safe_title = re.sub(r'[\\/*?:"<>|]', "_", ppt_title)[:50]
            filename = (
                f"{safe_title}_edited_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
            )
            docs_dir = os.path.join(
                os.path.dirname(__file__), "..", "workspace", "documents"
            )
            os.makedirs(docs_dir, exist_ok=True)
            output_path = os.path.join(docs_dir, filename)

        # 渲染
        ppt_gen.generate_from_outline(
            title=ppt_title,
            outline=ppt_data.get("slides", []),
            output_path=output_path,
            subtitle=ppt_data.get("subtitle", ""),
            author="Koto AI",
        )

        # 更新会话中的文件路径
        session["ppt_file_path"] = output_path
        session["updated_at"] = datetime.now().isoformat()

        session_file = os.path.join(mgr.storage_dir, f"{session_id}.json")
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

        return jsonify(
            {"success": True, "message": "PPT 文件已重新渲染", "file_path": output_path}
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "error": f"渲染失败: {str(e)}"}), 500


@ppt_api_bp.route("/download/<session_id>", methods=["GET"])
def download_pptx(session_id):
    """下载 PPT 文件"""
    try:
        mgr = get_ppt_session_manager()
        session = mgr.load_session(session_id)

        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        ppt_file_path = session.get("ppt_file_path")
        if not ppt_file_path or not os.path.exists(ppt_file_path):
            return jsonify({"success": False, "error": "PPT 文件不存在"}), 404

        filename = os.path.basename(ppt_file_path)
        return send_file(
            ppt_file_path,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

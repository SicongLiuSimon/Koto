"""
File-editor, file-search, notebook, scan, and concepts blueprint.

Routes:
  POST /api/notebook/overview         — Generate audio overview (podcast)
  POST /api/notebook/qa               — Source-grounded Q&A
  POST /api/notebook/study_guide      — Generate study guide / briefing
  POST /api/notebook/upload           — Upload and parse file (PDF/Docx/Txt)
  POST /api/file-editor/read          — Read file contents
  POST /api/file-editor/write         — Write file contents
  POST /api/file-editor/replace       — Replace text in file
  POST /api/file-editor/smart-edit    — Smart edit (natural language instruction)
  POST /api/file-search/index         — Index a file or directory
  POST /api/file-search/search        — Search indexed files
  POST /api/file-search/find-by-content — Find files by content similarity
  GET  /api/file-search/list          — List all indexed files
  POST /api/scan/start                — Start full disk scan (background thread)
  GET  /api/scan/status               — Scan progress and statistics
  POST /api/scan/search               — Fuzzy filename search across disk
  POST /api/scan/open                 — Open file with system default program
  GET  /api/scan/stats                — Index statistics
  POST /api/concepts/extract          — Extract key concepts from a file
  POST /api/concepts/related-files    — Find related files by concepts
  GET  /api/concepts/top              — Get global top concepts
  GET  /api/concepts/stats            — Concept extraction statistics
"""

import asyncio
import logging
import os
import tempfile
import time

from flask import Blueprint, jsonify, request, send_file

_logger = logging.getLogger("koto.routes.file_editor")

file_editor_bp = Blueprint("file_editor", __name__)


# ── lazy imports ────────────────────────────────────────────


def _get_file_editor():
    from web.app import get_file_editor

    return get_file_editor()


def _get_file_indexer():
    from web.app import get_file_indexer

    return get_file_indexer()


def _get_concept_extractor():
    from web.app import get_concept_extractor

    return get_concept_extractor()


def _get_settings_manager():
    from web.app import settings_manager

    return settings_manager


# ═══════════════════════════════════════════════════
# Notebook routes
# ═══════════════════════════════════════════════════


@file_editor_bp.route("/api/notebook/overview", methods=["POST"])
def notebook_overview():
    """生成音频概览 (Podcast)"""
    data = request.json
    content = data.get("content", "")
    if not content:
        return jsonify({"success": False, "error": "内容不能为空"}), 400

    try:
        from web.audio_overview import AudioOverviewGenerator

        settings_manager = _get_settings_manager()
        generator = AudioOverviewGenerator(
            output_dir=os.path.join(settings_manager.workspace_dir, "audio_cache")
        )

        import google.genai as genai
        import requests as _requests

        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        model = client.models

        script = asyncio.run(generator.generate_script(content, model))
        if not script:
            return jsonify({"success": False, "error": "剧本生成失败"}), 500

        session_id = f"overview_{int(time.time())}"
        audio_path = asyncio.run(generator.synthesize_audio(script, session_id))

        if audio_path:
            rel_path = os.path.relpath(audio_path, settings_manager.workspace_dir)
            audio_url = f"/api/files/download?path={_requests.utils.quote(audio_path)}"

            return jsonify({"success": True, "audio_url": audio_url, "script": script})
        else:
            return jsonify({"success": False, "error": "音频合成失败"}), 500

    except Exception as e:
        _logger.error(f"Error processing audio overview: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@file_editor_bp.route("/api/notebook/qa", methods=["POST"])
def notebook_qa():
    """源文档深度问答 (Source-Grounded Q&A)"""
    data = request.json
    question = data.get("question")
    file_ids = data.get("file_ids", [])
    context_content = data.get("context", "")

    if not question or not context_content:
        return jsonify({"success": False, "error": "缺少问题或上下文"}), 400

    prompt = f"""
    Answer the user's question mostly based on the provided source context.
    
    [Source Context]
    {context_content[:30000]} 

    [User Question]
    {question}

    [Rules]
    1. You must cite your sources. When you use information from the context, append [Source] at the end of the sentence.
    2. If the answer is not in the context, state that clearly.
    3. Be precise and concise.
    """

    try:
        import google.genai as genai

        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        return jsonify({"success": True, "answer": response.text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_editor_bp.route("/api/notebook/study_guide", methods=["POST"])
def notebook_study_guide():
    """生成学习指南/简报"""
    data = request.json
    content = data.get("content", "")
    type_ = data.get("type", "summary")  # summary, quiz, timeline, faq

    prompts = {
        "summary": "Create a comprehensive briefing document summarizing the key points, key people, and timeline from the text.",
        "quiz": "Create 5 multiple-choice questions based on the text to test understanding. Include the correct answer key at the end.",
        "timeline": "Extract a chronological timeline of events mentioned in the text.",
        "faq": "Create a FAQ section based on the text, anticipating what a reader might ask.",
    }

    selected_prompt = prompts.get(type_, prompts["summary"])
    full_prompt = f"{selected_prompt}\n\n[Source Text]\n{content[:20000]}"

    try:
        import google.genai as genai

        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=full_prompt
        )
        return jsonify({"success": True, "result": response.text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_editor_bp.route("/api/notebook/upload", methods=["POST"])
def notebook_upload():
    """Upload and parse a file (PDF, Docx, or Txt).
    ---
    tags:
      - Notebook
    consumes:
      - multipart/form-data
    parameters:
      - in: formData
        name: file
        type: file
        required: true
        description: The file to upload (PDF, Docx, or Txt)
    responses:
      200:
        description: File parsed successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            filename:
              type: string
              description: Original filename
            content:
              type: string
              description: Extracted text content
            char_count:
              type: integer
              description: Number of characters in extracted content
      400:
        description: No file provided or empty filename
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: false
            error:
              type: string
      500:
        description: File parsing error
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: false
            error:
              type: string
    """
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "No selected file"}), 400

    try:
        filename = file.filename
        temp_path = os.path.join(
            tempfile.gettempdir(), f"koto_{int(time.time())}_{filename}"
        )
        file.save(temp_path)

        from web.file_parser import FileParser

        result = FileParser.parse_file(temp_path)

        try:
            os.remove(temp_path)
        except OSError:
            pass

        if result.get("success"):
            return jsonify(
                {
                    "success": True,
                    "filename": filename,
                    "content": result.get("content", ""),
                    "char_count": result.get("char_count", 0),
                }
            )
        else:
            return jsonify({"success": False, "error": result.get("error")}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════
# File editor routes
# ═══════════════════════════════════════════════════


@file_editor_bp.route("/api/file-editor/read", methods=["POST"])
def file_editor_read():
    """读取文件内容"""
    try:
        data = request.json or {}
        file_path = data.get("file_path")

        if not file_path:
            return jsonify({"error": "缺少文件路径"}), 400

        editor = _get_file_editor()
        result = editor.read_file(file_path)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/file-editor/write", methods=["POST"])
def file_editor_write():
    """写入文件内容"""
    try:
        data = request.json or {}
        file_path = data.get("file_path")
        content = data.get("content")

        if not file_path or content is None:
            return jsonify({"error": "缺少必要参数"}), 400

        editor = _get_file_editor()
        result = editor.write_file(file_path, content)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/file-editor/replace", methods=["POST"])
def file_editor_replace():
    """替换文件内容"""
    try:
        data = request.json or {}
        file_path = data.get("file_path")
        old_text = data.get("old_text")
        new_text = data.get("new_text")
        use_regex = data.get("use_regex", False)

        if not all([file_path, old_text is not None, new_text is not None]):
            return jsonify({"error": "缺少必要参数"}), 400

        editor = _get_file_editor()
        result = editor.replace_text(file_path, old_text, new_text, use_regex=use_regex)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/file-editor/smart-edit", methods=["POST"])
def file_editor_smart_edit():
    """智能编辑（理解自然语言指令）"""
    try:
        data = request.json or {}
        file_path = data.get("file_path")
        instruction = data.get("instruction")

        if not file_path or not instruction:
            return jsonify({"error": "缺少必要参数"}), 400

        editor = _get_file_editor()
        result = editor.smart_edit(file_path, instruction)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
# File search routes
# ═══════════════════════════════════════════════════


@file_editor_bp.route("/api/file-search/index", methods=["POST"])
def file_search_index():
    """索引文件或目录"""
    try:
        data = request.json or {}
        path = data.get("path")
        is_directory = data.get("is_directory", False)

        if not path:
            return jsonify({"error": "缺少路径参数"}), 400

        indexer = _get_file_indexer()

        if is_directory:
            result = indexer.index_directory(path, recursive=True)
        else:
            result = indexer.index_file(path)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/file-search/search", methods=["POST"])
def file_search_search():
    """搜索文件"""
    try:
        data = request.json or {}
        query = data.get("query")
        limit = data.get("limit", 20)
        file_types = data.get("file_types")

        if not query:
            return jsonify({"error": "缺少搜索关键词"}), 400

        indexer = _get_file_indexer()
        results = indexer.search(query, limit=limit, file_types=file_types)

        return jsonify({"success": True, "results": results, "count": len(results)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/file-search/find-by-content", methods=["POST"])
def file_search_find_by_content():
    """根据内容片段查找文件"""
    try:
        data = request.json or {}
        content_sample = data.get("content")
        min_similarity = data.get("min_similarity", 0.3)

        if not content_sample:
            return jsonify({"error": "缺少内容样本"}), 400

        indexer = _get_file_indexer()
        results = indexer.find_by_content(content_sample, min_similarity=min_similarity)

        return jsonify({"success": True, "results": results, "count": len(results)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/file-search/list", methods=["GET"])
def file_search_list():
    """列出所有已索引文件"""
    try:
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)

        indexer = _get_file_indexer()
        files = indexer.list_indexed_files(limit=limit, offset=offset)

        return jsonify({"success": True, "files": files, "count": len(files)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
# 全盘文件扫描 API  (FileScanner)
# ═══════════════════════════════════════════════════


@file_editor_bp.route("/api/scan/start", methods=["POST"])
def scan_start():
    """启动全盘文件扫描（后台线程）"""
    try:
        from web.file_scanner import FileScanner

        data = request.json or {}
        drives = data.get("drives")  # None → 自动枚举所有分区
        already = not FileScanner.start_scan(drives=drives)
        return jsonify(
            {
                "success": True,
                "already_running": already,
                "drives": drives or FileScanner.get_drives(),
                "message": (
                    "扫描已在进行中" if already else "全盘扫描已启动（后台运行）"
                ),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_editor_bp.route("/api/scan/status", methods=["GET"])
def scan_status():
    """返回扫描进度和统计"""
    try:
        from web.file_scanner import FileScanner

        return jsonify(
            {
                "success": True,
                **FileScanner.get_status(),
                "indexed_count": FileScanner.stats()["total"],
                "by_category": FileScanner.stats()["by_category"],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_editor_bp.route("/api/scan/search", methods=["POST"])
def scan_search():
    """全盘文件名模糊搜索"""
    try:
        from web.file_scanner import FileScanner

        data = request.json or {}
        query = (data.get("query") or "").strip()
        limit = int(data.get("limit", 12))
        ext_filter = data.get("ext_filter")  # ['.docx', ...] or None
        category_filter = data.get("category")  # '文档' / '图片' / ... or None
        if not query:
            return jsonify({"success": False, "error": "缺少 query 参数"}), 400
        FileScanner.ensure_loaded()
        results = FileScanner.search(
            query, limit=limit, ext_filter=ext_filter, category_filter=category_filter
        )
        return jsonify({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_editor_bp.route("/api/scan/open", methods=["POST"])
def scan_open():
    """用系统默认程序打开指定绝对路径文件"""
    try:
        from web.file_scanner import FileScanner

        data = request.json or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify({"success": False, "error": "缺少 path 参数"}), 400
        result = FileScanner.open_file(path)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_editor_bp.route("/api/scan/stats", methods=["GET"])
def scan_stats():
    """索引统计数据"""
    try:
        from web.file_scanner import FileScanner

        return jsonify({"success": True, **FileScanner.stats()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════
# 概念提取 API
# ═══════════════════════════════════════════════════


@file_editor_bp.route("/api/concepts/extract", methods=["POST"])
def concepts_extract():
    """从文件中提取关键概念"""
    try:
        data = request.json or {}
        file_path = data.get("file_path")
        content = data.get("content")  # 可选，如果已读取内容
        top_n = data.get("top_n", 10)

        if not file_path:
            return jsonify({"error": "缺少文件路径"}), 400

        extractor = _get_concept_extractor()
        result = extractor.analyze_file(file_path, content=content)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/concepts/related-files", methods=["POST"])
def concepts_related_files():
    """查找与文件相关的其他文件"""
    try:
        data = request.json or {}
        file_path = data.get("file_path")
        limit = data.get("limit", 5)

        if not file_path:
            return jsonify({"error": "缺少文件路径"}), 400

        extractor = _get_concept_extractor()
        related = extractor.find_related_files(file_path, limit=limit)

        return jsonify(
            {"success": True, "file_path": file_path, "related_files": related}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/concepts/top", methods=["GET"])
def concepts_top():
    """获取全局热门概念"""
    try:
        limit = request.args.get("limit", 20, type=int)

        extractor = _get_concept_extractor()
        concepts = extractor.get_top_concepts(limit=limit)

        return jsonify({"success": True, "concepts": concepts})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@file_editor_bp.route("/api/concepts/stats", methods=["GET"])
def concepts_stats():
    """获取概念提取统计"""
    try:
        extractor = _get_concept_extractor()
        stats = extractor.get_statistics()

        return jsonify(stats)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

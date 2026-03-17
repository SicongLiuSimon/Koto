"""
File organization, batch processing and utility routes blueprint.

Routes:
  POST   /api/batch/rename              — Batch rename files
  POST   /api/batch/convert             — Batch format conversion
  GET    /api/template/list             — List templates
  POST   /api/template/generate         — Generate document from template
  POST   /api/check/consistency         — Check document consistency
  POST   /api/compare/documents         — Compare two documents
  POST   /api/batch/submit              — Submit batch file processing job
  GET    /api/batch/jobs                — List batch jobs
  GET    /api/batch/jobs/<job_id>       — Get batch job details
  GET    /api/batch/stream/<job_id>     — Stream batch job progress
  POST   /api/organize/scan-file        — Scan and analyze a single file
  POST   /api/organize/auto-organize    — Auto-organize a file
  GET    /api/organize/list-categories  — List all categories and folders
  POST   /api/organize/search           — Search organized files
  GET    /api/organize/stats            — Get organization statistics
  POST   /api/organize/cleanup          — Cleanup duplicate folders
  GET    /api/files/download            — Download file proxy
  POST   /api/ocr/screenshot            — Screenshot and OCR
  POST   /api/ocr/clipboard             — Clipboard image OCR
  GET    /api/history/list              — List operation history
  POST   /api/history/rollback/<op_id>  — Rollback an operation
  GET    /api/history/stats             — Get history statistics
"""
import logging
import os

from flask import Blueprint, Response, jsonify, request, send_file

_logger = logging.getLogger("koto.routes.file_organize")

file_organize_bp = Blueprint("file_organize", __name__)


# ---------------------------------------------------------------------------
# Lazy helpers – break circular imports with web.app
# ---------------------------------------------------------------------------

def _get_file_organizer():
    from web.app import get_file_organizer
    return get_file_organizer()


def _get_file_analyzer():
    from web.app import get_file_analyzer
    return get_file_analyzer()


def _get_batch_ops_manager():
    from web.app import get_batch_ops_manager
    return get_batch_ops_manager()


def _get_organize_root():
    from web.app import get_organize_root
    return get_organize_root()


# ---------------------------------------------------------------------------
# Batch processing API
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/batch/rename", methods=["POST"])
def batch_rename():
    """批量重命名文件"""
    try:
        from web.batch_processor import BatchFileProcessor

        data = request.json
        directory = data.get("directory")
        pattern = data.get("pattern")

        processor = BatchFileProcessor()
        result = processor.batch_rename(directory, **pattern)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_organize_bp.route("/api/batch/convert", methods=["POST"])
def batch_convert():
    """批量格式转换"""
    try:
        from web.batch_processor import BatchFileProcessor

        data = request.json
        directory = data.get("directory")
        from_format = data.get("from_format")
        to_format = data.get("to_format")

        processor = BatchFileProcessor()
        result = processor.batch_convert(directory, from_format, to_format)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Template API
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/template/list", methods=["GET"])
def template_list():
    """获取模板列表"""
    try:
        from web.template_library import TemplateLibrary

        library = TemplateLibrary()
        templates = library.list_templates()

        return jsonify({"success": True, "templates": templates})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_organize_bp.route("/api/template/generate", methods=["POST"])
def template_generate():
    """从模板生成文档"""
    try:
        from web.template_library import TemplateLibrary

        data = request.json
        template_name = data.get("template_id") or data.get("template_name")
        variables = data.get("variables", {})
        output_dir = data.get("output_dir")
        output_file = data.get("output_file")
        if output_file and not output_dir:
            if os.path.isdir(output_file):
                output_dir = output_file
            else:
                output_dir = os.path.dirname(output_file) or None

        library = TemplateLibrary()
        result = library.generate_from_template(template_name, variables, output_dir)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Consistency check API
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/check/consistency", methods=["POST"])
def check_consistency():
    """检查文档一致性"""
    try:
        from web.consistency_checker import ConsistencyChecker

        data = request.json
        file_path = data.get("file_path")

        checker = ConsistencyChecker()
        result = checker.check_document(file_path)
        report = checker.generate_report(result)

        return jsonify({"success": True, "result": result, "report": report})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Document comparison API
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/compare/documents", methods=["POST"])
def compare_documents():
    """对比文档"""
    try:
        from web.document_comparator import DocumentComparator

        data = request.json
        file_a = data.get("file_a")
        file_b = data.get("file_b")
        output_format = data.get("output_format", "markdown")

        comparator = DocumentComparator()
        result = comparator.compare_documents(file_a, file_b, output_format)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# OCR assistant API
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/ocr/screenshot", methods=["POST"])
def ocr_screenshot():
    """截图并OCR"""
    try:
        from web.clipboard_ocr_assistant import ClipboardOCRAssistant

        data = request.json
        save_image = data.get("save_image", True)
        auto_index = data.get("auto_index", False)

        assistant = ClipboardOCRAssistant()
        result = assistant.capture_and_ocr(source="screenshot", save_image=save_image)

        if auto_index and result.get("ocr_success"):
            assistant.auto_index_to_knowledge_base(result)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_organize_bp.route("/api/ocr/clipboard", methods=["POST"])
def ocr_clipboard():
    """剪贴板图片OCR"""
    try:
        from web.clipboard_ocr_assistant import ClipboardOCRAssistant

        data = request.json
        save_image = data.get("save_image", True)
        auto_index = data.get("auto_index", False)

        assistant = ClipboardOCRAssistant()
        result = assistant.capture_and_ocr(source="clipboard", save_image=save_image)

        if auto_index and result.get("ocr_success"):
            assistant.auto_index_to_knowledge_base(result)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Operation history API
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/history/list", methods=["GET"])
def history_list():
    """获取操作历史"""
    try:
        from web.operation_history import OperationHistory

        limit = request.args.get("limit", 50, type=int)
        file_path = request.args.get("file_path")

        history = OperationHistory()
        operations = history.get_history(limit=limit, file_path=file_path)

        return jsonify({"success": True, "operations": operations})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_organize_bp.route("/api/history/rollback/<op_id>", methods=["POST"])
def history_rollback(op_id):
    """回滚操作"""
    try:
        from web.operation_history import OperationHistory

        history = OperationHistory()
        result = history.rollback(op_id)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_organize_bp.route("/api/history/stats", methods=["GET"])
def history_stats():
    """获取历史统计"""
    try:
        from web.operation_history import OperationHistory

        history = OperationHistory()
        stats = history.get_statistics()

        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# File download proxy
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/files/download", methods=["GET"])
def download_file_proxy():
    """通用的文件下载代理"""
    file_path = request.args.get("path")
    if not file_path or not os.path.exists(file_path):
        return "File not found", 404
    return send_file(file_path, as_attachment=True)


# ---------------------------------------------------------------------------
# Batch file operations (advanced)
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/batch/submit", methods=["POST"])
def batch_submit():
    """提交批量文件处理任务"""
    try:
        data = request.json or {}
        command = data.get("command", "")
        manager = _get_batch_ops_manager()

        if command:
            parsed = manager.parse_command(command)
            if not parsed.get("success"):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": parsed.get("error"),
                            "hint": parsed.get("hint"),
                        }
                    ),
                    400,
                )
            operation = parsed.get("operation")
            input_dir = parsed.get("input_dir")
            output_dir = parsed.get("output_dir")
            options = parsed.get("options", {})
        else:
            operation = data.get("operation")
            input_dir = data.get("input_dir")
            output_dir = data.get("output_dir")
            options = data.get("options", {})

        if not operation or not input_dir or not output_dir:
            return jsonify({"success": False, "error": "缺少必要参数"}), 400

        job = manager.create_job(
            name=f"batch_{operation}",
            operation=operation,
            input_dir=input_dir,
            output_dir=output_dir,
            options=options,
        )
        manager.start_job(job.job_id)
        return jsonify(
            {"success": True, "job_id": job.job_id, "job": manager.get_job(job.job_id)}
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@file_organize_bp.route("/api/batch/jobs", methods=["GET"])
def batch_list_jobs():
    """列出批量任务"""
    manager = _get_batch_ops_manager()
    return jsonify({"success": True, "jobs": manager.list_jobs()})


@file_organize_bp.route("/api/batch/jobs/<job_id>", methods=["GET"])
def batch_get_job(job_id):
    """获取单个任务详情"""
    manager = _get_batch_ops_manager()
    job = manager.get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    return jsonify({"success": True, "job": job})


@file_organize_bp.route("/api/batch/stream/<job_id>", methods=["GET"])
def batch_stream_job(job_id):
    """批量任务进度流"""
    manager = _get_batch_ops_manager()
    return Response(manager.stream_job(job_id), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# File organization API
# ---------------------------------------------------------------------------

@file_organize_bp.route("/api/organize/scan-file", methods=["POST"])
def organize_scan_file():
    """扫描和分析单个文件"""
    try:
        data = request.json
        file_path = data.get("file_path")

        if not file_path:
            return jsonify({"error": "缺少 file_path 参数"}), 400

        if not os.path.exists(file_path):
            return jsonify({"error": f"文件不存在: {file_path}"}), 404

        analyzer = _get_file_analyzer()
        analysis_result = analyzer.analyze_file(file_path)

        return jsonify(
            {
                "success": True,
                "file": os.path.basename(file_path),
                "analysis": analysis_result,
            }
        )

    except Exception as e:
        return jsonify({"error": f"分析失败: {str(e)}"}), 500


@file_organize_bp.route("/api/organize/auto-organize", methods=["POST"])
def organize_auto_organize():
    """自动组织文件（分析+移动）"""
    try:
        data = request.json
        file_path = data.get("file_path")
        auto_confirm = data.get("auto_confirm", True)

        if not file_path:
            return jsonify({"error": "缺少 file_path 参数"}), 400

        if not os.path.exists(file_path):
            return jsonify({"error": f"文件不存在: {file_path}"}), 404

        # 第一步：分析文件
        analyzer = _get_file_analyzer()
        analysis = analyzer.analyze_file(file_path)
        suggested_folder = analysis.get("suggested_folder")

        if not suggested_folder:
            return jsonify({"error": "无法确定文件分类", "analysis": analysis}), 400

        # 第二步：组织文件
        organizer = _get_file_organizer()
        org_result = organizer.organize_file(
            file_path, suggested_folder, auto_confirm=auto_confirm
        )

        if org_result.get("success"):
            return jsonify(
                {
                    "success": True,
                    "file": os.path.basename(file_path),
                    "analysis": analysis,
                    "organized": org_result,
                }
            )
        else:
            return (
                jsonify(
                    {"error": org_result.get("error", "组织失败"), "analysis": analysis}
                ),
                500,
            )

    except Exception as e:
        return jsonify({"error": f"自动组织失败: {str(e)}"}), 500


@file_organize_bp.route("/api/organize/list-categories", methods=["GET"])
def organize_list_categories():
    """列出所有分类和文件夹"""
    try:
        organizer = _get_file_organizer()
        folders = organizer.list_organized_folders()
        stats = organizer.get_categories_stats()

        return jsonify(
            {
                "success": True,
                "folders": folders,
                "stats": stats,
                "total_files": len(organizer.get_index().get("files", [])),
            }
        )

    except Exception as e:
        return jsonify({"error": f"获取分类失败: {str(e)}"}), 500


@file_organize_bp.route("/api/organize/search", methods=["POST"])
def organize_search():
    """搜索已组织的文件"""
    try:
        data = request.json
        keyword = data.get("keyword", "")

        if not keyword:
            return jsonify({"error": "缺少搜索关键词"}), 400

        organizer = _get_file_organizer()
        results = organizer.search_files(keyword)

        return jsonify(
            {
                "success": True,
                "keyword": keyword,
                "count": len(results),
                "results": results,
            }
        )

    except Exception as e:
        return jsonify({"error": f"搜索失败: {str(e)}"}), 500


@file_organize_bp.route("/api/organize/stats", methods=["GET"])
def organize_stats():
    """获取组织统计信息"""
    try:
        organizer = _get_file_organizer()
        index = organizer.get_index()
        stats = organizer.get_categories_stats()
        folders = organizer.list_organized_folders()

        return jsonify(
            {
                "success": True,
                "total_files": index.get("total_files", 0),
                "total_folders": len(folders),
                "by_industry": stats,
                "last_updated": index.get("last_updated"),
            }
        )

    except Exception as e:
        return jsonify({"error": f"获取统计失败: {str(e)}"}), 500


@file_organize_bp.route("/api/organize/cleanup", methods=["POST"])
def organize_cleanup():
    """整合清理 _organize 目录中的重复文件夹"""
    try:
        data = request.get_json(silent=True) or {}
        dry_run = data.get("dry_run", True)
        ai_rename = data.get("ai_rename", False)

        organize_root = _get_organize_root()

        try:
            from web.organize_cleanup import OrganizeCleanup
        except ImportError:
            from organize_cleanup import OrganizeCleanup

        cleanup = OrganizeCleanup(organize_root=organize_root)
        report = cleanup.run(dry_run=dry_run, ai_rename=ai_rename)

        return jsonify(
            {
                "success": True,
                "dry_run": dry_run,
                "total_folders_scanned": report.get("total_folders_scanned", 0),
                "similarity_groups": report.get("similarity_groups", 0),
                "merge_plans": report.get("merge_plans", 0),
                "merged_files": report.get("merged_files", 0),
                "deduped_files": report.get("deduped_files", 0),
                "removed_folders": report.get("removed_folders", 0),
                "empty_cleaned": report.get("empty_cleaned", 0),
                "ai_renames": report.get("ai_renames", 0),
                "log": report.get("log", [])[-50:],  # 最近50条日志
            }
        )

    except Exception as e:
        return jsonify({"error": f"整合清理失败: {str(e)}"}), 500

"""
Workspace and browser automation blueprint.

Routes:
  GET    /api/workspace/<path:filepath>  — Serve a file from the workspace
  GET    /api/workspace                  — List files in the workspace root
  POST   /api/open-file                  — Open a file with the native OS handler
  GET    /api/browse                     — Browse folders on the local filesystem
  POST   /api/browser/open               — Open a URL via browser automation
  POST   /api/browser/search             — Google search via browser automation
  POST   /api/browser/screenshot         — Take a browser screenshot
  POST   /api/open-workspace             — Open the workspace folder in the OS file manager
"""
import logging
import os
import subprocess
import sys
import time

from flask import Blueprint, jsonify, request, send_from_directory

from web.shared import WORKSPACE_DIR, PROJECT_ROOT, get_workspace_root

_logger = logging.getLogger("koto.routes.workspace")

workspace_bp = Blueprint("workspace", __name__)


# ─── Workspace file routes ───────────────────────────────────────────────────


@workspace_bp.route("/api/workspace/<path:filepath>")
def get_workspace_file(filepath):
    """获取 workspace 中的文件，支持子目录"""
    _logger.debug(f"[API] Serving workspace file: {filepath}")
    full_path = os.path.join(WORKSPACE_DIR, filepath)

    # 安全检查：确保请求的路径在 WORKSPACE_DIR 下
    try:
        resolved_path = os.path.abspath(full_path)
        resolved_workspace = os.path.abspath(WORKSPACE_DIR)
        if not resolved_path.startswith(resolved_workspace):
            _logger.debug(
                f"[API] Security violation: {resolved_path} not under {resolved_workspace}"
            )
            return jsonify({"error": "Access denied"}), 403

        if not os.path.exists(resolved_path):
            _logger.debug(f"[API] File not found: {resolved_path}")
            return jsonify({"error": "File not found"}), 404

        _logger.debug(f"[API] Serving: {resolved_path}")
        return send_from_directory(WORKSPACE_DIR, filepath)
    except Exception as e:
        _logger.debug(f"[API] Error serving {filepath}: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": "Server error", "detail": str(e)}), 500


@workspace_bp.route("/api/workspace", methods=["GET"])
def list_workspace_files():
    files = os.listdir(WORKSPACE_DIR)
    return jsonify({"files": files})


@workspace_bp.route("/api/open-file", methods=["POST"])
def open_file_native():
    """用系统默认程序打开文件（不经过浏览器）"""
    try:
        data = request.get_json()
        filepath = data.get("filepath", "")
        if not filepath:
            return jsonify({"success": False, "error": "No filepath provided"}), 400

        full_path = os.path.join(WORKSPACE_DIR, filepath)
        resolved_path = os.path.abspath(full_path)
        resolved_workspace = os.path.abspath(WORKSPACE_DIR)

        if not resolved_path.startswith(resolved_workspace):
            return jsonify({"success": False, "error": "Access denied"}), 403

        if not os.path.exists(resolved_path):
            return jsonify({"success": False, "error": "File not found"}), 404

        _logger.debug(f"[API] Opening file natively: {resolved_path}")
        if sys.platform == "win32":
            os.startfile(resolved_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", resolved_path])
        else:
            subprocess.Popen(["xdg-open", resolved_path])

        return jsonify({"success": True, "path": resolved_path})
    except Exception as e:
        _logger.debug(f"[API] Error opening file: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/api/open-workspace", methods=["POST"])
def open_workspace():
    """打开 workspace 文件夹"""
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                f'explorer "{WORKSPACE_DIR}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", WORKSPACE_DIR])
        else:
            subprocess.Popen(["xdg-open", WORKSPACE_DIR])
        return jsonify({"success": True, "path": WORKSPACE_DIR})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ─── Folder browsing ─────────────────────────────────────────────────────────


@workspace_bp.route("/api/browse", methods=["GET"])
def browse_folders():
    path = request.args.get("path", "C:\\")

    try:
        if not os.path.exists(path):
            return jsonify({"error": "路径不存在", "folders": [], "parent": None})

        if not os.path.isdir(path):
            return jsonify({"error": "不是文件夹", "folders": [], "parent": None})

        folders = []
        try:
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if os.path.isdir(item_path):
                    folders.append({"name": item, "path": item_path})
        except PermissionError:
            return jsonify({"error": "没有权限访问", "folders": [], "parent": None})

        folders.sort(key=lambda x: x["name"].lower())

        # Get parent path
        parent = os.path.dirname(path)
        if parent == path:  # Root drive
            parent = None

        return jsonify({"folders": folders, "parent": parent, "current": path})
    except Exception as e:
        return jsonify({"error": str(e), "folders": [], "parent": None})


# ─── Browser automation ──────────────────────────────────────────────────────


@workspace_bp.route("/api/browser/open", methods=["POST"])
def browser_open():
    """打开 URL"""
    from browser_automation import get_browser_automation

    url = request.json.get("url", "")
    browser = get_browser_automation()
    success = browser.open_url(url)

    return jsonify({"success": success})


@workspace_bp.route("/api/browser/search", methods=["POST"])
def browser_search():
    """Google 搜索"""
    from browser_automation import get_browser_automation

    query = request.json.get("query", "")
    browser = get_browser_automation()
    results = browser.search_google(query)

    return jsonify({"results": results})


@workspace_bp.route("/api/browser/screenshot", methods=["POST"])
def browser_screenshot():
    """截图"""
    from browser_automation import get_browser_automation

    filename = request.json.get("filename", f"screenshot_{int(time.time())}.png")
    file_path = os.path.join(WORKSPACE_DIR, "images", filename)

    browser = get_browser_automation()
    success = browser.take_screenshot(file_path)

    return jsonify({"success": success, "path": file_path})

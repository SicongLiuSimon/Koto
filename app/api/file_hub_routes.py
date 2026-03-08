# -*- coding: utf-8 -*-
"""
FileHub REST API — /api/files
==============================
统一文件 Hub Blueprint，聚合 FileRegistry + FileWatcher + FileToolsPlugin 的 HTTP 接口。

端点（基础）：
  GET  /api/files/search          搜索文件（?q=&category=&limit=）
  POST /api/files/register        手动注册文件
  GET  /api/files/stats           文件库统计
  GET  /api/files/recent          最近收录（?days=7&category=&limit=20）
  GET  /api/files/duplicates      重复文件（按哈希）
  POST /api/files/scan-dir        立即扫描一个目录
  GET  /api/files/<file_id>       查询单个文件记录
  DELETE /api/files/<file_id>     从文件库移除记录（不删除磁盘文件）

端点（文件操作）：
  POST /api/files/rename          重命名文件
  POST /api/files/move            移动文件
  POST /api/files/copy            复制文件
  DELETE /api/files/disk          删除磁盘文件（送入回收站或永久删除）
  POST /api/files/compress        打包成 zip
  POST /api/files/extract         解压档案

端点（目录/磁盘）：
  GET  /api/files/list-dir        列出目录内容
  GET  /api/files/tree            目录树
  GET  /api/files/disk-usage      磁盘占用分析
  GET  /api/files/large-files     大文件查询
  GET  /api/files/old-files       旧文件查询

端点（批量操作）：
  POST /api/files/batch-rename    批量重命名
  POST /api/files/batch-move      批量移动
  POST /api/files/cleanup-dups    清理重复文件

端点（标签/收藏）：
  GET  /api/files/tags            所有标签统计
  GET  /api/files/<file_id>/tags  查询文件标签
  POST /api/files/<file_id>/tags  添加标签
  DELETE /api/files/<file_id>/tags/<tag>  移除标签
  GET  /api/files/by-tag          按标签查询文件（?tag=）
  GET  /api/files/favorites       收藏列表
  POST /api/files/favorites       加入收藏
  DELETE /api/files/favorites     取消收藏

端点（智能/日志）：
  POST /api/files/summarize       LLM 文件摘要
  GET  /api/files/op-log          操作日志
  POST /api/files/undo            撤销上一次操作
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

file_hub_bp = Blueprint("file_hub", __name__)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

@file_hub_bp.route("/pick-folder", methods=["GET"])
def pick_folder():
    """弹出系统原生「选择文件夹」对话框，返回所选路径。
    仅适用于本地运行环境（tkinter 依赖显示上下文）。
    """
    result = {"path": None}
    error_holder = {}

    def _run():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(parent=root, title="选择目录")
            root.destroy()
            result["path"] = selected or None
        except Exception as exc:
            error_holder["error"] = str(exc)

    # tkinter 必须在主线程或至少独立线程中运行，不能在 Flask worker 线程里直接调用
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=60)  # 最多等 60 s（用户可能慢慢选）

    if "error" in error_holder:
        return jsonify({"ok": False, "error": error_holder["error"]}), 500
    if not result["path"]:
        return jsonify({"ok": False, "cancelled": True})
    return jsonify({"ok": True, "path": result["path"]})

def _reg():
    from app.core.file.file_registry import get_file_registry
    return get_file_registry()


def _watcher():
    from app.core.file.file_watcher import get_file_watcher
    return get_file_watcher()


# ── 端点 ─────────────────────────────────────────────────────────────────────

@file_hub_bp.route("/search", methods=["GET"])
def search_files():
    """
    搜索文件。
    Query: q=关键词（可选）, category=文档|图片|..., limit=50
    q 或 category 至少提供一个；都不提供时返回最近文件。
    """
    q = (request.args.get("q") or "").strip()
    category = request.args.get("category") or None
    limit = min(max(1, int(request.args.get("limit", 50))), 200)

    if not q and not category:
        # 无条件时返回最近文件
        entries = _reg().list_recent(days=30, limit=limit)
        return jsonify({
            "query": "",
            "total": len(entries),
            "results": [e.to_dict(include_preview=False) for e in entries],
        })

    entries = _reg().search(q or "", category=category, limit=limit)
    return jsonify({
        "query": q,
        "total": len(entries),
        "results": [e.to_dict(include_preview=False) for e in entries],
    })


@file_hub_bp.route("/register", methods=["POST"])
def register_file():
    """
    手动注册文件。
    Body JSON: { "path": "绝对路径", "source": "manual", "session_id": "", "goal_id": "" }
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "缺少 path 字段"}), 400

    p = Path(path)
    if not p.exists() or not p.is_file():
        return jsonify({"error": f"文件不存在或不是有效文件: {path}"}), 404

    entry = _reg().register(
        path,
        source=data.get("source", "manual"),
        session_id=data.get("session_id"),
        goal_id=data.get("goal_id"),
        extract_content=True,
    )
    if not entry:
        return jsonify({"error": "注册失败"}), 500

    return jsonify({"status": "ok", "file": entry.to_dict(include_preview=False)}), 201


@file_hub_bp.route("/stats", methods=["GET"])
def file_stats():
    """返回文件库统计：总数、按类别分组、各 source 分布。"""
    stats = _reg().stats()

    # 补充 source 分布
    import sqlite3
    conn = _reg()._conn
    source_rows = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM koto_file_registry GROUP BY source"
    ).fetchall()
    by_source = {r["source"]: r["cnt"] for r in source_rows}

    return jsonify({**stats, "by_source": by_source})


@file_hub_bp.route("/recent", methods=["GET"])
def recent_files():
    """
    最近收录文件。
    Query: days=7, category=, limit=20
    """
    days = min(max(1, int(request.args.get("days", 7))), 365)
    category = request.args.get("category") or None
    limit = min(max(1, int(request.args.get("limit", 20))), 100)

    entries = _reg().list_recent(days=days, category=category, limit=limit)
    return jsonify({
        "days": days,
        "total": len(entries),
        "files": [e.to_dict() for e in entries],
    })


@file_hub_bp.route("/duplicates", methods=["GET"])
def duplicate_files():
    """返回内容相同（hash 相同）的文件组。"""
    groups = _reg().get_duplicates()
    return jsonify({
        "total_groups": len(groups),
        "groups": [
            [e.to_dict(include_preview=False) for e in grp]
            for grp in groups
        ],
    })


@file_hub_bp.route("/scan-dir", methods=["POST"])
def scan_directory():
    """
    立即同步扫描一个目录并注册所有文件。
    Body JSON: { "directory": "绝对路径" }
    """
    data = request.get_json(silent=True) or {}
    directory = (data.get("directory") or "").strip()
    if not directory:
        return jsonify({"error": "缺少 directory 字段"}), 400

    p = Path(directory)
    if not p.is_dir():
        return jsonify({"error": f"目录不存在: {directory}"}), 404

    count = _watcher().scan_once(directory)
    return jsonify({
        "status": "ok",
        "directory": directory,
        "registered": count,
    })


@file_hub_bp.route("/<file_id>", methods=["GET"])
def get_file(file_id: str):
    """查询单个文件记录（含 content_preview）。"""
    entry = _reg().get_by_id(file_id)
    if not entry:
        return jsonify({"error": "未找到该文件记录"}), 404
    return jsonify(entry.to_dict(include_preview=True))


@file_hub_bp.route("/<file_id>", methods=["DELETE"])
def remove_file(file_id: str):
    """从文件库移除记录（不删除磁盘文件）。"""
    entry = _reg().get_by_id(file_id)
    if not entry:
        return jsonify({"error": "未找到该文件记录"}), 404

    deleted = _reg().delete(entry.path)
    if deleted:
        return jsonify({"status": "ok", "removed_path": entry.path})
    return jsonify({"error": "删除失败"}), 500


# ── 文件操作端点 ──────────────────────────────────────────────────────────────

def _tools():
    from app.core.file.file_tools import FileToolsPlugin
    return FileToolsPlugin()


@file_hub_bp.route("/rename", methods=["POST"])
def rename_file():
    """
    重命名文件。
    Body JSON: { "path": "绝对路径", "new_name": "新文件名" }
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    if not path or not new_name:
        return jsonify({"error": "缺少 path 或 new_name 字段"}), 400
    result = _tools().rename_file(path, new_name)
    ok = result.startswith("✅")
    return jsonify({"status": "ok" if ok else "error", "message": result}), 200 if ok else 400


@file_hub_bp.route("/move", methods=["POST"])
def move_file():
    """
    移动文件。
    Body JSON: { "source_path": "...", "dest_dir": "...", "new_name": "" }
    """
    data = request.get_json(silent=True) or {}
    source_path = (data.get("source_path") or "").strip()
    dest_dir = (data.get("dest_dir") or "").strip()
    if not source_path or not dest_dir:
        return jsonify({"error": "缺少 source_path 或 dest_dir 字段"}), 400
    result = _tools().move_file(source_path, dest_dir, data.get("new_name") or "")
    ok = result.startswith("✅")
    return jsonify({"status": "ok" if ok else "error", "message": result}), 200 if ok else 400


@file_hub_bp.route("/copy", methods=["POST"])
def copy_file():
    """
    复制文件。
    Body JSON: { "source_path": "...", "dest_dir": "...", "new_name": "" }
    """
    data = request.get_json(silent=True) or {}
    source_path = (data.get("source_path") or "").strip()
    dest_dir = (data.get("dest_dir") or "").strip()
    if not source_path or not dest_dir:
        return jsonify({"error": "缺少 source_path 或 dest_dir 字段"}), 400
    result = _tools().copy_file(source_path, dest_dir, data.get("new_name") or "")
    ok = result.startswith("✅")
    return jsonify({"status": "ok" if ok else "error", "message": result}), 200 if ok else 400


@file_hub_bp.route("/disk", methods=["DELETE"])
def delete_file_disk():
    """
    删除磁盘文件。
    Body JSON: { "path": "绝对路径", "use_trash": true }
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "缺少 path 字段"}), 400
    use_trash = bool(data.get("use_trash", True))
    result = _tools().delete_file(path, use_trash=use_trash)
    ok = result.startswith("✅")
    return jsonify({"status": "ok" if ok else "error", "message": result}), 200 if ok else 400


@file_hub_bp.route("/compress", methods=["POST"])
def compress_files():
    """
    打包成 zip。
    Body JSON: { "sources": ["路径1", "路径2"], "output_path": "输出路径.zip" }
    """
    data = request.get_json(silent=True) or {}
    sources = data.get("sources") or []
    output_path = (data.get("output_path") or "").strip()
    if not sources or not output_path:
        return jsonify({"error": "缺少 sources 或 output_path 字段"}), 400
    result = _tools().compress_files(sources, output_path)
    ok = result.startswith("✅")
    return jsonify({"status": "ok" if ok else "error", "message": result}), 200 if ok else 400


@file_hub_bp.route("/extract", methods=["POST"])
def extract_archive():
    """
    解压档案。
    Body JSON: { "archive_path": "...", "dest_dir": "" }
    """
    data = request.get_json(silent=True) or {}
    archive_path = (data.get("archive_path") or "").strip()
    if not archive_path:
        return jsonify({"error": "缺少 archive_path 字段"}), 400
    result = _tools().extract_archive(archive_path, data.get("dest_dir") or "")
    ok = result.startswith("✅")
    return jsonify({"status": "ok" if ok else "error", "message": result}), 200 if ok else 400


# ── 直接浏览目录（返回结构化文件列表，无需注册） ─────────────────────────────

@file_hub_bp.route("/browse", methods=["GET"])
def browse_directory():
    """
    直接浏览文件系统目录，返回结构化文件列表。
    Query:
      path      = 目录路径（必填）
      recursive = false | true  （递归扫描，默认 false）
      q         = 文件名关键词过滤（可选）
      limit     = 最多返回条数（默认 200，最大 1000）
    """
    import os
    import mimetypes

    path = (request.args.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "缺少 path 参数"}), 400

    p = Path(path)
    if not p.exists():
        return jsonify({"ok": False, "error": f"路径不存在: {path}"}), 404
    if not p.is_dir():
        return jsonify({"ok": False, "error": f"不是目录: {path}"}), 400

    recursive = request.args.get("recursive", "false").lower() == "true"
    q = (request.args.get("q") or "").strip().lower()
    limit = min(max(1, int(request.args.get("limit", 200))), 1000)

    # 分类规则
    _EXT_MAP = {
        ".pdf": "文档", ".doc": "文档", ".docx": "文档", ".txt": "文档",
        ".md": "文档", ".xls": "文档", ".xlsx": "文档", ".ppt": "文档",
        ".pptx": "文档", ".odt": "文档", ".rtf": "文档", ".csv": "文档",
        ".jpg": "图片", ".jpeg": "图片", ".png": "图片", ".gif": "图片",
        ".bmp": "图片", ".svg": "图片", ".webp": "图片", ".ico": "图片",
        ".mp4": "视频", ".avi": "视频", ".mov": "视频", ".mkv": "视频",
        ".wmv": "视频", ".flv": "视频", ".webm": "视频",
        ".mp3": "音频", ".wav": "音频", ".flac": "音频", ".aac": "音频",
        ".ogg": "音频", ".m4a": "音频",
        ".py": "代码", ".js": "代码", ".ts": "代码", ".java": "代码",
        ".c": "代码", ".cpp": "代码", ".cs": "代码", ".go": "代码",
        ".rs": "代码", ".html": "代码", ".css": "代码", ".json": "代码",
        ".xml": "代码", ".sh": "代码", ".bat": "代码", ".ps1": "代码",
        ".zip": "压缩包", ".rar": "压缩包", ".7z": "压缩包", ".tar": "压缩包",
        ".gz": "压缩包", ".bz2": "压缩包",
    }

    files = []
    try:
        if recursive:
            walker = os.walk(p)
        else:
            # Non-recursive: just list direct children
            walker = [(str(p), [d.name for d in p.iterdir() if d.is_dir()], [f.name for f in p.iterdir() if f.is_file()])]

        for dirpath, _dirs, filenames in walker:
            for fname in filenames:
                if q and q not in fname.lower():
                    continue
                fpath = Path(dirpath) / fname
                try:
                    stat = fpath.stat()
                    ext = fpath.suffix.lower()
                    cat = _EXT_MAP.get(ext, "其他")
                    files.append({
                        "name": fname,
                        "path": str(fpath),
                        "category": cat,
                        "size_bytes": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
                except OSError:
                    continue
                if len(files) >= limit:
                    break
            if len(files) >= limit:
                break
    except PermissionError as exc:
        return jsonify({"ok": False, "error": f"无权限访问: {exc}"}), 403

    # Sort by mtime desc
    files.sort(key=lambda f: f["mtime"], reverse=True)

    return jsonify({
        "ok": True,
        "path": path,
        "recursive": recursive,
        "total": len(files),
        "files": files,
    })


# ── 目录 / 磁盘端点 ───────────────────────────────────────────────────────────

@file_hub_bp.route("/list-dir", methods=["GET"])
def list_directory():
    """
    列出目录内容。
    Query: path=, show_hidden=false, filter_ext=, sort_by=name
    """
    path = (request.args.get("path") or "").strip()
    if not path:
        return jsonify({"error": "缺少 path 参数"}), 400
    show_hidden = request.args.get("show_hidden", "false").lower() == "true"
    filter_ext = request.args.get("filter_ext") or ""
    sort_by = request.args.get("sort_by") or "name"
    result = _tools().list_directory(path, show_hidden=show_hidden, filter_ext=filter_ext, sort_by=sort_by)
    return jsonify({"path": path, "result": result})


@file_hub_bp.route("/tree", methods=["GET"])
def directory_tree():
    """
    目录树。
    Query: path=, max_depth=3
    """
    path = (request.args.get("path") or "").strip()
    if not path:
        return jsonify({"error": "缺少 path 参数"}), 400
    max_depth = min(max(1, int(request.args.get("max_depth", 3))), 6)
    result = _tools().directory_tree(path, max_depth=max_depth)
    return jsonify({"path": path, "tree": result})


@file_hub_bp.route("/disk-usage", methods=["GET"])
def disk_usage():
    """
    磁盘占用分析。
    Query: path=, top_n=10
    """
    path = (request.args.get("path") or "").strip()
    if not path:
        return jsonify({"error": "缺少 path 参数"}), 400
    top_n = min(max(1, int(request.args.get("top_n", 10))), 50)
    result = _tools().get_disk_usage(path, top_n=top_n)
    return jsonify({"path": path, "result": result})


@file_hub_bp.route("/large-files", methods=["GET"])
def large_files():
    """
    大文件查询。
    Query: path=（可选）, min_size_mb=10, limit=20
    """
    path = request.args.get("path") or ""
    min_size_mb = float(request.args.get("min_size_mb", 10))
    limit = min(max(1, int(request.args.get("limit", 20))), 100)
    result = _tools().find_large_files(path=path, min_size_mb=min_size_mb, limit=limit)
    return jsonify({"result": result})


@file_hub_bp.route("/old-files", methods=["GET"])
def old_files():
    """
    旧文件查询。
    Query: days_old=180, limit=20
    """
    days_old = min(max(1, int(request.args.get("days_old", 180))), 3650)
    limit = min(max(1, int(request.args.get("limit", 20))), 100)
    result = _tools().find_old_files(days_old=days_old, limit=limit)
    return jsonify({"result": result})


# ── 批量操作端点 ──────────────────────────────────────────────────────────────

@file_hub_bp.route("/batch-rename", methods=["POST"])
def batch_rename():
    """
    批量重命名。
    Body JSON: { "directory": "...", "pattern": "...", "replacement": "...",
                 "file_filter": "", "dry_run": true }
    """
    data = request.get_json(silent=True) or {}
    directory = (data.get("directory") or "").strip()
    pattern = (data.get("pattern") or "").strip()
    replacement = data.get("replacement", "")
    if not directory or not pattern:
        return jsonify({"error": "缺少 directory 或 pattern 字段"}), 400
    result = _tools().batch_rename(
        directory=directory,
        pattern=pattern,
        replacement=replacement,
        file_filter=data.get("file_filter") or "",
        dry_run=bool(data.get("dry_run", True)),
    )
    return jsonify({"result": result})


@file_hub_bp.route("/batch-move", methods=["POST"])
def batch_move():
    """
    批量移动。
    Body JSON: { "source_dir": "...", "dest_dir": "...", "category": "",
                 "file_filter": "", "dry_run": true }
    """
    data = request.get_json(silent=True) or {}
    source_dir = (data.get("source_dir") or "").strip()
    dest_dir = (data.get("dest_dir") or "").strip()
    if not source_dir or not dest_dir:
        return jsonify({"error": "缺少 source_dir 或 dest_dir 字段"}), 400
    result = _tools().batch_move(
        source_dir=source_dir,
        dest_dir=dest_dir,
        category=data.get("category") or "",
        file_filter=data.get("file_filter") or "",
        dry_run=bool(data.get("dry_run", True)),
    )
    return jsonify({"result": result})


@file_hub_bp.route("/cleanup-dups", methods=["POST"])
def cleanup_duplicates():
    """
    清理重复文件。
    Body JSON: { "keep_strategy": "newest", "dry_run": true }
    """
    data = request.get_json(silent=True) or {}
    result = _tools().cleanup_duplicates(
        keep_strategy=data.get("keep_strategy") or "newest",
        dry_run=bool(data.get("dry_run", True)),
    )
    return jsonify({"result": result})


# ── 标签端点 ──────────────────────────────────────────────────────────────────

@file_hub_bp.route("/tags", methods=["GET"])
def list_all_tags():
    """列出所有标签及使用次数。"""
    tags = _reg().list_all_tags()
    return jsonify({"total": len(tags), "tags": tags})


@file_hub_bp.route("/by-tag", methods=["GET"])
def files_by_tag():
    """
    按标签查询文件。
    Query: tag=标签名, limit=50
    """
    tag = (request.args.get("tag") or "").strip()
    if not tag:
        return jsonify({"error": "缺少 tag 参数"}), 400
    limit = min(max(1, int(request.args.get("limit", 50))), 200)
    paths = _reg().list_by_tag(tag, limit=limit)
    return jsonify({"tag": tag, "total": len(paths), "paths": paths})


@file_hub_bp.route("/<file_id>/tags", methods=["GET"])
def get_file_tags(file_id: str):
    """查询文件的所有标签。"""
    entry = _reg().get_by_id(file_id)
    if not entry:
        return jsonify({"error": "未找到该文件记录"}), 404
    tags = _reg().get_tags(entry.path)
    return jsonify({"file_id": file_id, "path": entry.path, "tags": tags})


@file_hub_bp.route("/<file_id>/tags", methods=["POST"])
def add_file_tag(file_id: str):
    """
    添加标签。
    Body JSON: { "tag": "标签名" }
    """
    entry = _reg().get_by_id(file_id)
    if not entry:
        return jsonify({"error": "未找到该文件记录"}), 404
    data = request.get_json(silent=True) or {}
    tag = (data.get("tag") or "").strip()
    if not tag:
        return jsonify({"error": "缺少 tag 字段"}), 400
    ok = _reg().add_tag(entry.path, tag)
    if ok:
        return jsonify({"status": "ok", "tag": tag})
    return jsonify({"error": "添加失败"}), 500


@file_hub_bp.route("/<file_id>/tags/<tag>", methods=["DELETE"])
def remove_file_tag(file_id: str, tag: str):
    """移除文件的某个标签。"""
    entry = _reg().get_by_id(file_id)
    if not entry:
        return jsonify({"error": "未找到该文件记录"}), 404
    ok = _reg().remove_tag(entry.path, tag)
    if ok:
        return jsonify({"status": "ok", "removed_tag": tag})
    return jsonify({"error": f"标签 '{tag}' 不存在"}), 404


# ── 收藏端点 ──────────────────────────────────────────────────────────────────

@file_hub_bp.route("/favorites", methods=["GET"])
def list_favorites():
    """列出所有收藏的文件路径。"""
    paths = _reg().list_favorites()
    return jsonify({"total": len(paths), "favorites": paths})


@file_hub_bp.route("/favorites", methods=["POST"])
def add_favorite():
    """
    加入收藏。
    Body JSON: { "path": "绝对路径" } 或 { "file_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        file_id = (data.get("file_id") or "").strip()
        if file_id:
            entry = _reg().get_by_id(file_id)
            path = entry.path if entry else ""
    if not path:
        return jsonify({"error": "缺少 path 或 file_id 字段"}), 400
    ok = _reg().add_favorite(path)
    if ok:
        return jsonify({"status": "ok", "path": path}), 201
    return jsonify({"error": "操作失败"}), 500


@file_hub_bp.route("/favorites", methods=["DELETE"])
def remove_favorite():
    """
    取消收藏。
    Body JSON: { "path": "绝对路径" }
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "缺少 path 字段"}), 400
    ok = _reg().remove_favorite(path)
    if ok:
        return jsonify({"status": "ok"})
    return jsonify({"error": "该文件不在收藏夹中"}), 404


# ── 智能 / 日志端点 ───────────────────────────────────────────────────────────

@file_hub_bp.route("/summarize", methods=["POST"])
def summarize_file():
    """
    LLM 文件摘要。
    Body JSON: { "path": "...", "focus": "" }
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "缺少 path 字段"}), 400
    result = _tools().summarize_file(path, focus=data.get("focus") or "")
    ok = not result.startswith("错误") and not result.startswith("LLM 摘要失败")
    return jsonify({"status": "ok" if ok else "error", "summary": result})


@file_hub_bp.route("/op-log", methods=["GET"])
def op_log():
    """
    操作日志。
    Query: limit=20
    """
    limit = min(max(1, int(request.args.get("limit", 20))), 200)
    logs = _reg().get_op_log(limit=limit)
    return jsonify({"total": len(logs), "ops": logs})


@file_hub_bp.route("/undo", methods=["POST"])
def undo_last_op():
    """撤销上一次文件操作。"""
    result = _tools().undo_last_op()
    ok = result.startswith("✅")
    return jsonify({"status": "ok" if ok else "info", "message": result})


# -*- coding: utf-8 -*-
"""
FileToolsPlugin — Agent 文件能力插件
======================================
向 ToolRegistry 注册以下文件操作工具：
  基础操作：find_file, read_file_snippet, list_recent_files, organize_file
            rename_file, move_file, copy_file, delete_file
  目录分析：list_directory, directory_tree, get_disk_usage, find_large_files,
            find_old_files
  批量操作：batch_rename, batch_move, cleanup_duplicates
  压缩归档：compress_files, extract_archive
  标签收藏：manage_tag, manage_favorite
  智能摘要：summarize_file
  撤  销  ：undo_last_op

注册方式::
    from app.core.file.file_tools import register_file_tools
    register_file_tools(tool_registry_instance)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import zipfile
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)


class FileToolsPlugin(AgentPlugin):
    """Koto 文件能力 AgentPlugin。"""

    @property
    def name(self) -> str:
        return "FileTools"

    @property
    def description(self) -> str:
        return "文件搜索、读取与整理工具"

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "find_file",
                "func": self.find_file,
                "description": (
                    "在本机或工作区中搜索文件，支持文件名和内容全文搜索。"
                    "返回匹配文件列表（路径、类别、大小、摘要）。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "搜索关键词，支持中英文、文件名或内容片段",
                        },
                        "category": {
                            "type": "STRING",
                            "description": (
                                "可选。文件类别过滤：文档 / 图片 / 视频 / 音频 / 代码 / 压缩包 / 其他"
                            ),
                        },
                        "limit": {
                            "type": "INTEGER",
                            "description": "最多返回条数，默认 10",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "read_file_snippet",
                "func": self.read_file_snippet,
                "description": (
                    "读取一个本地文件的文本内容（前 max_chars 字符）。"
                    "支持 txt / md / pdf / docx / xlsx / csv / json / py 等格式。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "文件的绝对路径",
                        },
                        "max_chars": {
                            "type": "INTEGER",
                            "description": "最多读取字符数，默认 3000",
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "list_recent_files",
                "func": self.list_recent_files,
                "description": (
                    "列出最近 N 天内 Koto 收录的文件，可按类别过滤。"
                    "返回文件列表（路径、类别、索引时间）。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "days": {
                            "type": "INTEGER",
                            "description": "时间范围（天），默认 7",
                        },
                        "category": {
                            "type": "STRING",
                            "description": "可选类别过滤",
                        },
                        "limit": {
                            "type": "INTEGER",
                            "description": "最多返回条数，默认 20",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "organize_file",
                "func": self.organize_file,
                "description": (
                    "将指定文件自动整理到合适的目录下，并注册到文件库。"
                    "如果文件已经在正确位置，也会确保其被收录到索引中。"
                    "返回整理结果（最终路径、操作说明）。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "source_path": {
                            "type": "STRING",
                            "description": "待整理文件的绝对路径",
                        },
                        "category_hint": {
                            "type": "STRING",
                            "description": "可选，告知 Koto 该文件属于哪个大类，帮助自动归档",
                        },
                    },
                    "required": ["source_path"],
                },
            },
            # ── 基础文件操作 ─────────────────────────────────────────────────
            {
                "name": "rename_file",
                "func": self.rename_file,
                "description": "将文件重命名（仅改名，不改位置）。会自动更新 Koto 文件库记录，并写入操作日志供撤销。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {"type": "STRING", "description": "文件的绝对路径"},
                        "new_name": {"type": "STRING", "description": "新的文件名（含扩展名，如 报告_v2.docx）"},
                    },
                    "required": ["path", "new_name"],
                },
            },
            {
                "name": "move_file",
                "func": self.move_file,
                "description": "将文件移动到另一个目录。目标目录不存在时自动创建。会更新文件库记录。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "source_path": {"type": "STRING", "description": "源文件的绝对路径"},
                        "dest_dir": {"type": "STRING", "description": "目标目录的绝对路径"},
                        "new_name": {"type": "STRING", "description": "可选，移动后重命名文件"},
                    },
                    "required": ["source_path", "dest_dir"],
                },
            },
            {
                "name": "copy_file",
                "func": self.copy_file,
                "description": "复制文件到另一个目录，并将副本注册到 Koto 文件库。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "source_path": {"type": "STRING", "description": "源文件的绝对路径"},
                        "dest_dir": {"type": "STRING", "description": "目标目录的绝对路径"},
                        "new_name": {"type": "STRING", "description": "可选，副本的文件名"},
                    },
                    "required": ["source_path", "dest_dir"],
                },
            },
            {
                "name": "delete_file",
                "func": self.delete_file,
                "description": (
                    "删除文件。默认使用系统回收站（安全），设 use_trash=false 则永久删除。"
                    "操作记录写入日志，可用 undo_last_op 撤销（仅回收站模式有效）。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {"type": "STRING", "description": "要删除的文件绝对路径"},
                        "use_trash": {"type": "BOOLEAN", "description": "是否送入回收站，默认 true"},
                    },
                    "required": ["path"],
                },
            },
            # ── 目录浏览与磁盘分析 ───────────────────────────────────────────
            {
                "name": "list_directory",
                "func": self.list_directory,
                "description": "列出指定目录下的文件和子目录，可按类型或名称过滤、排序。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {"type": "STRING", "description": "目录的绝对路径"},
                        "show_hidden": {"type": "BOOLEAN", "description": "是否显示隐藏文件，默认 false"},
                        "filter_ext": {"type": "STRING", "description": "可选，仅显示该扩展名的文件，如 .pdf"},
                        "sort_by": {"type": "STRING", "description": "排序字段：name（默认）/ size / mtime"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "directory_tree",
                "func": self.directory_tree,
                "description": "以树状结构展示目录及其子目录（文件夹层级）。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {"type": "STRING", "description": "要展示的根目录路径"},
                        "max_depth": {"type": "INTEGER", "description": "最大展开层数，默认 3"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "get_disk_usage",
                "func": self.get_disk_usage,
                "description": "分析指定目录的磁盘占用，列出各子目录大小排行（Top N）。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {"type": "STRING", "description": "要分析的目录绝对路径"},
                        "top_n": {"type": "INTEGER", "description": "返回占用最大的前 N 个子目录，默认 10"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "find_large_files",
                "func": self.find_large_files,
                "description": "在指定目录（或 Koto 文件库）中查找大文件。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {"type": "STRING", "description": "可选，扫描的目录路径；省略则查 Koto 文件库"},
                        "min_size_mb": {"type": "NUMBER", "description": "最小文件大小（MB），默认 10"},
                        "limit": {"type": "INTEGER", "description": "最多返回条数，默认 20"},
                    },
                    "required": [],
                },
            },
            {
                "name": "find_old_files",
                "func": self.find_old_files,
                "description": "在 Koto 文件库中查找长期未修改的旧文件，辅助清理决策。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "days_old": {"type": "INTEGER", "description": "超过多少天未修改视为旧文件，默认 180"},
                        "limit": {"type": "INTEGER", "description": "最多返回条数，默认 20"},
                    },
                    "required": [],
                },
            },
            # ── 批量操作 ─────────────────────────────────────────────────────
            {
                "name": "batch_rename",
                "func": self.batch_rename,
                "description": (
                    "批量重命名目录内的文件。支持正则替换模式，"
                    "或按序号/日期前缀规则统一改名。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "directory": {"type": "STRING", "description": "目标目录绝对路径"},
                        "pattern": {"type": "STRING", "description": "正则匹配模式（作用于文件名）"},
                        "replacement": {"type": "STRING", "description": "替换后的文件名模板，支持 \\1 等反向引用"},
                        "file_filter": {"type": "STRING", "description": "可选，只处理该扩展名的文件，如 .jpg"},
                        "dry_run": {"type": "BOOLEAN", "description": "是否预演（true = 只显示变更不执行），默认 true"},
                    },
                    "required": ["directory", "pattern", "replacement"],
                },
            },
            {
                "name": "batch_move",
                "func": self.batch_move,
                "description": "批量将一个目录下符合条件的文件（按类别或扩展名）移动到目标目录。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "source_dir": {"type": "STRING", "description": "来源目录"},
                        "dest_dir": {"type": "STRING", "description": "目标目录"},
                        "category": {"type": "STRING", "description": "可选，文件类别过滤（文档/图片/视频/音频/代码/压缩包）"},
                        "file_filter": {"type": "STRING", "description": "可选，扩展名过滤，如 .pdf"},
                        "dry_run": {"type": "BOOLEAN", "description": "预演模式，默认 true"},
                    },
                    "required": ["source_dir", "dest_dir"],
                },
            },
            {
                "name": "cleanup_duplicates",
                "func": self.cleanup_duplicates,
                "description": (
                    "清理 Koto 文件库中内容相同（MD5 相同）的重复文件，"
                    "每组保留一个副本，其余送入回收站。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "keep_strategy": {
                            "type": "STRING",
                            "description": "保留策略：newest（默认，保留最新修改）/ oldest / shortest_path",
                        },
                        "dry_run": {"type": "BOOLEAN", "description": "预演模式，默认 true"},
                    },
                    "required": [],
                },
            },
            # ── 压缩 / 解压 ──────────────────────────────────────────────────
            {
                "name": "compress_files",
                "func": self.compress_files,
                "description": "将多个文件或整个目录打包成 zip 压缩档。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "sources": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "要压缩的文件或目录路径列表",
                        },
                        "output_path": {"type": "STRING", "description": "输出 zip 文件的绝对路径（含文件名）"},
                    },
                    "required": ["sources", "output_path"],
                },
            },
            {
                "name": "extract_archive",
                "func": self.extract_archive,
                "description": "解压 zip / tar / tar.gz / tar.bz2 档案到目标目录，并注册释放的文件到 Koto 文件库。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "archive_path": {"type": "STRING", "description": "压缩档绝对路径"},
                        "dest_dir": {"type": "STRING", "description": "解压目标目录（省略则解压到档案所在目录）"},
                    },
                    "required": ["archive_path"],
                },
            },
            # ── 标签 / 收藏 ──────────────────────────────────────────────────
            {
                "name": "manage_tag",
                "func": self.manage_tag,
                "description": (
                    "管理文件标签：添加、移除、清除或列出标签。"
                    "action 取值：add / remove / list / clear / list_all（查看所有标签统计）。"
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {"type": "STRING", "description": "操作：add / remove / list / clear / list_all / files_by_tag"},
                        "path": {"type": "STRING", "description": "文件路径（list_all / files_by_tag 时可省略）"},
                        "tag": {"type": "STRING", "description": "标签名称（action=files_by_tag 时作为查询条件）"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "manage_favorite",
                "func": self.manage_favorite,
                "description": "管理收藏夹：添加文件、移除文件、列出全部收藏。action 取值：add / remove / list。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {"type": "STRING", "description": "操作：add / remove / list"},
                        "path": {"type": "STRING", "description": "文件路径（list 时可省略）"},
                    },
                    "required": ["action"],
                },
            },
            # ── 智能功能 ─────────────────────────────────────────────────────
            {
                "name": "summarize_file",
                "func": self.summarize_file,
                "description": "使用 LLM 对文件内容生成智能摘要，支持文档、代码、表格等格式。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {"type": "STRING", "description": "文件绝对路径"},
                        "focus": {"type": "STRING", "description": "可选，告诉 LLM 重点关注哪方面（如关键结论）"},
                    },
                    "required": ["path"],
                },
            },
            # ── 撤销 ─────────────────────────────────────────────────────────
            {
                "name": "undo_last_op",
                "func": self.undo_last_op,
                "description": "撤销上一次文件操作（rename / move / copy / delete-to-trash）。",
                "parameters": {"type": "OBJECT", "properties": {}, "required": []},
            },
        ]

    # ── 工具实现 ──────────────────────────────────────────────────────────────

    def find_file(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> str:
        """统一文件搜索：先查 FileRegistry，再用系统 FileScanner 补充。"""
        limit = min(max(1, int(limit)), 50)
        from app.core.file.file_registry import get_file_registry

        reg = get_file_registry()
        entries = reg.search(query, category=category or None, limit=limit)

        results = []
        for e in entries:
            results.append({
                "path": e.path,
                "name": e.name,
                "category": e.category,
                "size_kb": round(e.size_bytes / 1024, 1),
                "snippet": e.snippet,
            })

        # 如果 FileRegistry 结果不足，再尝试调用 FileScanner 补充
        if len(results) < limit:
            try:
                extra = self._scanner_fallback(query, limit - len(results))
                seen_paths = {r["path"] for r in results}
                for item in extra:
                    if item["path"] not in seen_paths:
                        results.append(item)
            except Exception as e:
                logger.debug(f"[FileTools] scanner fallback 失败: {e}")

        if not results:
            return f"未找到与 '{query}' 相关的文件。"

        lines = [f"共找到 {len(results)} 个相关文件：\n"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. [{r['category']}] {r['name']}  ({r['size_kb']} KB)\n"
                f"   路径: {r['path']}\n"
                + (f"   摘要: {r['snippet']}\n" if r.get("snippet") else "")
            )
        return "\n".join(lines)

    def read_file_snippet(self, path: str, max_chars: int = 3000) -> str:
        """读取本地文件内容片段。"""
        max_chars = min(max(100, int(max_chars)), 10000)
        p = Path(path)
        if not p.exists():
            return f"错误：文件不存在 → {path}"
        if not p.is_file():
            return f"错误：路径不是文件 → {path}"
        if p.stat().st_size > 50 * 1024 * 1024:
            return f"错误：文件过大（>{50} MB），请使用专用工具处理"

        # 先尝试从 FileRegistry 缓存的 content_preview 读取（快速）
        try:
            from app.core.file.file_registry import get_file_registry, _extract_text_preview
            reg = get_file_registry()
            entry = reg.get_by_path(str(p))
            if entry and entry.content_preview:
                preview = entry.content_preview[:max_chars]
                suffix = "…（已截断）" if len(entry.content_preview) > max_chars else ""
                return preview + suffix
        except Exception:
            pass

        # 直接提取
        try:
            from app.core.file.file_registry import _extract_text_preview
            content = _extract_text_preview(str(p), max_chars=max_chars)
            if content:
                suffix = "…（已截断）" if len(content) >= max_chars else ""
                return content + suffix
            return f"（无法提取文本内容，文件类型：{p.suffix}）"
        except Exception as e:
            return f"读取失败：{e}"

    def list_recent_files(
        self,
        days: int = 7,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """列出近期收录文件。"""
        days = min(max(1, int(days)), 365)
        limit = min(max(1, int(limit)), 100)
        from app.core.file.file_registry import get_file_registry

        reg = get_file_registry()
        entries = reg.list_recent(days=days, category=category or None, limit=limit)

        if not entries:
            return f"最近 {days} 天内没有新收录的文件。"

        lines = [f"最近 {days} 天新收录文件（{len(entries)} 个）：\n"]
        for i, e in enumerate(entries, 1):
            lines.append(
                f"{i}. [{e.category}] {e.name}  ({round(e.size_bytes / 1024, 1)} KB)\n"
                f"   路径: {e.path}\n"
                f"   收录时间: {e.indexed_at[:16]}\n"
            )
        return "\n".join(lines)

    def organize_file(self, source_path: str, category_hint: str = "") -> str:
        """将文件整理到合适目录，并注册到 FileRegistry。"""
        p = Path(source_path)
        if not p.exists():
            return f"错误：文件不存在 → {source_path}"
        if not p.is_file():
            return f"错误：不是有效文件 → {source_path}"

        # 先确保文件在 FileRegistry 中
        from app.core.file.file_registry import get_file_registry
        reg = get_file_registry()
        entry = reg.register(str(p), source="manual", extract_content=True)

        if not entry:
            return f"注册失败：{source_path}"

        # 尝试调用 FileOrganizer
        try:
            import importlib
            organizer_mod = importlib.import_module("web.file_organizer")
            FileOrganizer = getattr(organizer_mod, "FileOrganizer", None)
            if FileOrganizer:
                organizer = FileOrganizer()
                result = organizer.organize_file(source_path, category_hint=category_hint)
                final_path = result.get("final_path", source_path) if isinstance(result, dict) else source_path
                # 更新注册表中的路径（如果被移动）
                if final_path != source_path and Path(final_path).exists():
                    reg.delete(source_path)
                    reg.register(final_path, source="organizer", extract_content=False)
                return (
                    f"整理完成：{p.name}\n"
                    f"  类别: {entry.category}\n"
                    f"  最终路径: {final_path}\n"
                    + (f"  操作: {result.get('action', '')}" if isinstance(result, dict) else "")
                )
        except Exception as e:
            logger.debug(f"[FileTools] FileOrganizer 不可用，仅完成注册: {e}")

        return (
            f"已将文件收录到 Koto 文件库：\n"
            f"  名称: {entry.name}\n"
            f"  类别: {entry.category}\n"
            f"  路径: {entry.path}\n"
            f"  大小: {round(entry.size_bytes / 1024, 1)} KB"
        )

    # ── 基础文件操作实现 ──────────────────────────────────────────────────────

    def rename_file(self, path: str, new_name: str) -> str:
        """重命名文件，保持在原目录。"""
        p = Path(path)
        if not p.exists():
            return f"错误：文件不存在 → {path}"
        if not p.is_file():
            return f"错误：不是文件 → {path}"
        new_name = new_name.strip()
        if not new_name or any(c in new_name for c in r'\/:*?"<>|'):
            return "错误：新文件名含非法字符或为空"
        new_path = p.parent / new_name
        if new_path.exists():
            return f"错误：目标文件已存在 → {new_path}"
        try:
            p.rename(new_path)
        except Exception as e:
            return f"重命名失败：{e}"
        try:
            from app.core.file.file_registry import get_file_registry
            reg = get_file_registry()
            if not reg.update_path(path, str(new_path)):
                reg.register(str(new_path), source="rename")
            reg.log_op("rename", path, str(new_path))
        except Exception as e:
            logger.debug(f"[FileTools] registry sync after rename failed: {e}")
        return f"✅ 重命名成功：{p.name}  →  {new_name}\n   路径：{new_path}"

    def move_file(self, source_path: str, dest_dir: str, new_name: str = "") -> str:
        """移动文件到另一个目录。"""
        src = Path(source_path)
        if not src.exists():
            return f"错误：源文件不存在 → {source_path}"
        if not src.is_file():
            return f"错误：不是文件 → {source_path}"
        dest_d = Path(dest_dir)
        dest_d.mkdir(parents=True, exist_ok=True)
        fname = new_name.strip() if new_name else src.name
        if any(c in fname for c in r'\/:*?"<>|'):
            return "错误：文件名含非法字符"
        new_path = dest_d / fname
        if new_path.exists():
            return f"错误：目标文件已存在 → {new_path}"
        try:
            shutil.move(str(src), str(new_path))
        except Exception as e:
            return f"移动失败：{e}"
        try:
            from app.core.file.file_registry import get_file_registry
            reg = get_file_registry()
            if not reg.update_path(str(src), str(new_path)):
                reg.register(str(new_path), source="move")
            reg.log_op("move", str(src), str(new_path))
        except Exception as e:
            logger.debug(f"[FileTools] registry sync after move failed: {e}")
        return f"✅ 移动成功：{src.name}\n   目标：{new_path}"

    def copy_file(self, source_path: str, dest_dir: str, new_name: str = "") -> str:
        """复制文件到另一个目录。"""
        src = Path(source_path)
        if not src.exists():
            return f"错误：源文件不存在 → {source_path}"
        if not src.is_file():
            return f"错误：不是文件 → {source_path}"
        dest_d = Path(dest_dir)
        dest_d.mkdir(parents=True, exist_ok=True)
        fname = new_name.strip() if new_name else src.name
        if any(c in fname for c in r'\/:*?"<>|'):
            return "错误：文件名含非法字符"
        new_path = dest_d / fname
        if new_path.exists():
            return f"错误：目标文件已存在 → {new_path}"
        try:
            shutil.copy2(str(src), str(new_path))
        except Exception as e:
            return f"复制失败：{e}"
        try:
            from app.core.file.file_registry import get_file_registry
            reg = get_file_registry()
            reg.register(str(new_path), source="copy")
            reg.log_op("copy", str(src), str(new_path))
        except Exception as e:
            logger.debug(f"[FileTools] registry sync after copy failed: {e}")
        return f"✅ 复制成功：{src.name}  →  {new_path}"

    def delete_file(self, path: str, use_trash: bool = True) -> str:
        """删除文件，默认使用系统回收站。"""
        p = Path(path)
        if not p.exists():
            return f"错误：文件不存在 → {path}"
        if not p.is_file():
            return f"错误：不是文件 → {path}"
        try:
            if use_trash:
                try:
                    import send2trash
                    send2trash.send2trash(str(p))
                except ImportError:
                    # 降级：永久删除但给出提示
                    p.unlink()
                    use_trash = False
            else:
                p.unlink()
        except Exception as e:
            return f"删除失败：{e}"
        try:
            from app.core.file.file_registry import get_file_registry
            reg = get_file_registry()
            reg.delete(path)
            reg.log_op("delete", path, meta={"trash": use_trash})
        except Exception as e:
            logger.debug(f"[FileTools] registry sync after delete failed: {e}")
        mode = "已送入回收站" if use_trash else "已永久删除"
        return f"✅ {mode}：{p.name}"

    # ── 目录浏览 / 磁盘分析 ───────────────────────────────────────────────────

    def list_directory(
        self,
        path: str,
        show_hidden: bool = False,
        filter_ext: str = "",
        sort_by: str = "name",
    ) -> str:
        """列出目录内容。"""
        d = Path(path)
        if not d.exists():
            return f"错误：路径不存在 → {path}"
        if not d.is_dir():
            return f"错误：不是目录 → {path}"
        items = []
        for child in d.iterdir():
            if not show_hidden and child.name.startswith("."):
                continue
            if filter_ext and child.is_file() and child.suffix.lower() != filter_ext.lower():
                continue
            try:
                stat = child.stat()
                items.append({
                    "name": child.name,
                    "type": "目录" if child.is_dir() else "文件",
                    "size_bytes": stat.st_size if child.is_file() else 0,
                    "mtime": stat.st_mtime,
                })
            except Exception:
                continue
        if sort_by == "size":
            items.sort(key=lambda x: x["size_bytes"], reverse=True)
        elif sort_by == "mtime":
            items.sort(key=lambda x: x["mtime"], reverse=True)
        else:
            items.sort(key=lambda x: (x["type"] == "文件", x["name"].lower()))
        if not items:
            return f"目录为空：{path}"
        import datetime
        lines = [f"📁 {path}  （共 {len(items)} 项）\n"]
        for it in items:
            icon = "📁" if it["type"] == "目录" else "📄"
            sz = f"  {round(it['size_bytes']/1024,1)} KB" if it["type"] == "文件" else ""
            mt = datetime.datetime.fromtimestamp(it["mtime"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  {icon} {it['name']}{sz}  [{mt}]")
        return "\n".join(lines)

    def directory_tree(self, path: str, max_depth: int = 3) -> str:
        """以树状结构展示目录。"""
        root = Path(path)
        if not root.exists() or not root.is_dir():
            return f"错误：目录不存在 → {path}"
        max_depth = min(max(1, int(max_depth)), 6)
        lines: List[str] = [str(root)]

        def _walk(directory: Path, prefix: str, depth: int):
            if depth > max_depth:
                return
            try:
                children = sorted(directory.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
            except PermissionError:
                return
            for i, child in enumerate(children):
                connector = "└── " if i == len(children) - 1 else "├── "
                lines.append(f"{prefix}{connector}{child.name}" + ("/" if child.is_dir() else ""))
                if child.is_dir():
                    extension = "    " if i == len(children) - 1 else "│   "
                    _walk(child, prefix + extension, depth + 1)

        _walk(root, "", 1)
        return "\n".join(lines)

    def get_disk_usage(self, path: str, top_n: int = 10) -> str:
        """分析目录磁盘占用，列出 Top N 子目录（按大小降序）。"""
        root = Path(path)
        if not root.exists() or not root.is_dir():
            return f"错误：目录不存在 → {path}"
        top_n = min(max(1, int(top_n)), 50)
        # 计算总大小
        def _dir_size(d: Path) -> int:
            total = 0
            try:
                for p in d.rglob("*"):
                    if p.is_file():
                        try:
                            total += p.stat().st_size
                        except Exception:
                            pass
            except Exception:
                pass
            return total

        total_bytes = _dir_size(root)
        sub_sizes = []
        try:
            for child in root.iterdir():
                if child.is_dir():
                    sz = _dir_size(child)
                    sub_sizes.append((child.name, sz))
                elif child.is_file():
                    try:
                        sub_sizes.append((child.name + " (文件)", child.stat().st_size))
                    except Exception:
                        pass
        except PermissionError:
            return f"错误：没有权限访问 {path}"
        sub_sizes.sort(key=lambda x: x[1], reverse=True)

        def _fmt(b: int) -> str:
            if b >= 1024 ** 3:
                return f"{b/1024**3:.2f} GB"
            if b >= 1024 ** 2:
                return f"{b/1024**2:.2f} MB"
            return f"{b/1024:.1f} KB"

        lines = [f"📊 磁盘占用分析：{path}", f"   总大小：{_fmt(total_bytes)}\n"]
        for i, (name, sz) in enumerate(sub_sizes[:top_n], 1):
            pct = f"{sz/total_bytes*100:.1f}%" if total_bytes else "  -"
            lines.append(f"  {i:2}. {name:<40} {_fmt(sz):>10}  {pct}")
        return "\n".join(lines)

    def find_large_files(
        self,
        path: str = "",
        min_size_mb: float = 10.0,
        limit: int = 20,
    ) -> str:
        """查找大文件。"""
        min_bytes = int(float(min_size_mb) * 1024 * 1024)
        limit = min(max(1, int(limit)), 100)

        if path:
            root = Path(path)
            if not root.exists() or not root.is_dir():
                return f"错误：目录不存在 → {path}"
            results = []
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        sz = p.stat().st_size
                        if sz >= min_bytes:
                            results.append((str(p), sz))
                    except Exception:
                        pass
            results.sort(key=lambda x: x[1], reverse=True)
            results = results[:limit]
        else:
            from app.core.file.file_registry import get_file_registry
            entries = get_file_registry().list_large_files(min_bytes=min_bytes, limit=limit)
            results = [(e.path, e.size_bytes) for e in entries]

        if not results:
            return f"未找到大于 {min_size_mb} MB 的文件。"

        def _fmt(b: int) -> str:
            if b >= 1024 ** 3:
                return f"{b/1024**3:.2f} GB"
            return f"{b/1024**2:.2f} MB"

        lines = [f"🔍 大文件列表（>{min_size_mb} MB，共 {len(results)} 个）：\n"]
        for i, (fpath, sz) in enumerate(results, 1):
            lines.append(f"  {i:2}. {_fmt(sz):>10}  {fpath}")
        return "\n".join(lines)

    def find_old_files(self, days_old: int = 180, limit: int = 20) -> str:
        """在 Koto 文件库中查找长期未修改的旧文件。"""
        import datetime
        days_old = min(max(1, int(days_old)), 3650)
        limit = min(max(1, int(limit)), 100)
        from app.core.file.file_registry import get_file_registry
        entries = get_file_registry().list_old_files(days_old=days_old, limit=limit)
        if not entries:
            return f"Koto 文件库中没有超过 {days_old} 天未修改的文件记录。"
        lines = [f"🕰️ 超过 {days_old} 天未修改的文件（{len(entries)} 个）：\n"]
        for i, e in enumerate(entries, 1):
            mt = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d")
            lines.append(
                f"  {i:2}. [{e.category}] {e.name}  ({round(e.size_bytes/1024,1)} KB，最后修改 {mt})\n"
                f"      {e.path}"
            )
        return "\n".join(lines)

    # ── 批量操作 ──────────────────────────────────────────────────────────────

    def batch_rename(
        self,
        directory: str,
        pattern: str,
        replacement: str,
        file_filter: str = "",
        dry_run: bool = True,
    ) -> str:
        """批量重命名。"""
        d = Path(directory)
        if not d.is_dir():
            return f"错误：目录不存在 → {directory}"
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"错误：正则表达式无效 → {e}"
        candidates = []
        for child in sorted(d.iterdir()):
            if not child.is_file():
                continue
            if file_filter and child.suffix.lower() != file_filter.lower():
                continue
            new_name = regex.sub(replacement, child.name)
            if new_name != child.name:
                candidates.append((child, new_name))

        if not candidates:
            return "没有符合条件的文件需要重命名。"

        lines = [f"{'[预演] ' if dry_run else ''}批量重命名 {len(candidates)} 个文件：\n"]
        errors = []
        for src, new_name in candidates:
            new_path = src.parent / new_name
            lines.append(f"  {src.name}  →  {new_name}")
            if not dry_run:
                if new_path.exists():
                    errors.append(f"  ⚠️ 跳过（目标已存在）: {new_name}")
                    continue
                try:
                    src.rename(new_path)
                    from app.core.file.file_registry import get_file_registry
                    reg = get_file_registry()
                    if not reg.update_path(str(src), str(new_path)):
                        reg.register(str(new_path), source="rename")
                    reg.log_op("rename", str(src), str(new_path))
                except Exception as e:
                    errors.append(f"  ❌ 失败 {src.name}: {e}")
        if errors:
            lines += errors
        if dry_run:
            lines.append("\n⚠️ 这是预演结果，未实际执行。设 dry_run=false 执行重命名。")
        else:
            lines.append("\n✅ 重命名完成")
        return "\n".join(lines)

    def batch_move(
        self,
        source_dir: str,
        dest_dir: str,
        category: str = "",
        file_filter: str = "",
        dry_run: bool = True,
    ) -> str:
        """批量移动文件。"""
        src_d = Path(source_dir)
        if not src_d.is_dir():
            return f"错误：来源目录不存在 → {source_dir}"
        if not dry_run:
            Path(dest_dir).mkdir(parents=True, exist_ok=True)

        _EXT_CAT: Dict[str, str] = {}
        _CAT_EXTS = {
            "文档": {".doc", ".docx", ".pdf", ".txt", ".md", ".ppt", ".pptx", ".xls", ".xlsx", ".csv"},
            "图片": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic"},
            "视频": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"},
            "音频": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
            "代码": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs"},
            "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"},
        }
        for cat, exts in _CAT_EXTS.items():
            for e in exts:
                _EXT_CAT[e] = cat

        candidates = []
        for child in src_d.iterdir():
            if not child.is_file():
                continue
            if file_filter and child.suffix.lower() != file_filter.lower():
                continue
            if category and _EXT_CAT.get(child.suffix.lower(), "其他") != category:
                continue
            candidates.append(child)

        if not candidates:
            return "没有符合条件的文件。"

        lines = [f"{'[预演] ' if dry_run else ''}批量移动 {len(candidates)} 个文件 → {dest_dir}\n"]
        errors = []
        for src in sorted(candidates):
            new_path = Path(dest_dir) / src.name
            lines.append(f"  {src.name}")
            if not dry_run:
                if new_path.exists():
                    errors.append(f"  ⚠️ 跳过（目标已存在）: {src.name}")
                    continue
                try:
                    shutil.move(str(src), str(new_path))
                    from app.core.file.file_registry import get_file_registry
                    reg = get_file_registry()
                    if not reg.update_path(str(src), str(new_path)):
                        reg.register(str(new_path), source="move")
                    reg.log_op("move", str(src), str(new_path))
                except Exception as e:
                    errors.append(f"  ❌ 失败 {src.name}: {e}")
        if errors:
            lines += errors
        if dry_run:
            lines.append("\n⚠️ 预演结果，设 dry_run=false 执行实际移动。")
        else:
            lines.append("\n✅ 批量移动完成")
        return "\n".join(lines)

    def cleanup_duplicates(
        self,
        keep_strategy: str = "newest",
        dry_run: bool = True,
    ) -> str:
        """清理重复文件。"""
        from app.core.file.file_registry import get_file_registry
        reg = get_file_registry()
        groups = reg.get_duplicates()
        if not groups:
            return "Koto 文件库中没有检测到重复文件。"

        keep_strategy = keep_strategy.strip().lower()
        lines = [f"{'[预演] ' if dry_run else ''}重复文件清理（保留策略：{keep_strategy}）\n"]
        total_to_remove = 0
        total_freed = 0

        for grp in groups:
            if keep_strategy == "oldest":
                grp.sort(key=lambda e: e.mtime)
            elif keep_strategy == "shortest_path":
                grp.sort(key=lambda e: len(e.path))
            else:  # newest（默认）
                grp.sort(key=lambda e: e.mtime, reverse=True)
            keeper = grp[0]
            to_remove = grp[1:]
            lines.append(f"  ✅ 保留: {keeper.path}")
            for e in to_remove:
                lines.append(f"  🗑️ 删除: {e.path}  ({round(e.size_bytes/1024,1)} KB)")
                total_to_remove += 1
                total_freed += e.size_bytes
                if not dry_run:
                    try:
                        p = Path(e.path)
                        if p.exists():
                            try:
                                import send2trash
                                send2trash.send2trash(str(p))
                            except ImportError:
                                p.unlink()
                        reg.delete(e.path)
                        reg.log_op("delete", e.path, meta={"reason": "duplicate", "trash": True})
                    except Exception as ex:
                        lines.append(f"    ❌ 删除失败: {ex}")

        def _fmt(b: int) -> str:
            return f"{b/1024**2:.2f} MB" if b >= 1024 ** 2 else f"{b/1024:.1f} KB"

        lines.append(
            f"\n合计：{len(groups)} 组重复，{total_to_remove} 个副本，"
            f"可释放 {_fmt(total_freed)}"
        )
        if dry_run:
            lines.append("⚠️ 预演结果，设 dry_run=false 执行实际清理。")
        return "\n".join(lines)

    # ── 压缩 / 解压 ───────────────────────────────────────────────────────────

    def compress_files(self, sources: List[str], output_path: str) -> str:
        """打包成 zip 档。"""
        out = Path(output_path)
        if out.exists():
            return f"错误：输出文件已存在 → {output_path}"
        out.parent.mkdir(parents=True, exist_ok=True)
        added = 0
        try:
            with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
                for src in sources:
                    p = Path(src)
                    if not p.exists():
                        continue
                    if p.is_file():
                        zf.write(str(p), p.name)
                        added += 1
                    elif p.is_dir():
                        for child in p.rglob("*"):
                            if child.is_file():
                                zf.write(str(child), str(child.relative_to(p.parent)))
                                added += 1
        except Exception as e:
            return f"压缩失败：{e}"
        if added == 0:
            try:
                out.unlink()
            except Exception:
                pass
            return "错误：没有有效文件可以压缩"
        size_kb = round(out.stat().st_size / 1024, 1)
        try:
            from app.core.file.file_registry import get_file_registry
            reg = get_file_registry()
            reg.register(str(out), source="manual")
            reg.log_op("compress", str(sources), str(out))
        except Exception:
            pass
        return f"✅ 已打包 {added} 个文件 → {output_path}  ({size_kb} KB)"

    def extract_archive(self, archive_path: str, dest_dir: str = "") -> str:
        """解压档案文件。"""
        arc = Path(archive_path)
        if not arc.exists():
            return f"错误：文件不存在 → {archive_path}"
        dest = Path(dest_dir) if dest_dir else arc.parent / arc.stem
        dest.mkdir(parents=True, exist_ok=True)
        ext = arc.suffix.lower()
        try:
            if ext == ".zip":
                with zipfile.ZipFile(str(arc), "r") as zf:
                    zf.extractall(str(dest))
            elif ext in {".tar", ".gz", ".bz2", ".xz", ".tgz"}:
                with tarfile.open(str(arc), "r:*") as tf:
                    tf.extractall(str(dest))
            else:
                return f"不支持的压缩格式：{ext}（支持 zip / tar / tar.gz / tar.bz2）"
        except Exception as e:
            return f"解压失败：{e}"
        # 注册释放的文件
        registered = 0
        try:
            from app.core.file.file_registry import get_file_registry
            reg = get_file_registry()
            for child in dest.rglob("*"):
                if child.is_file():
                    reg.register(str(child), source="extract", extract_content=False)
                    registered += 1
            reg.log_op("extract", str(arc), str(dest))
        except Exception:
            pass
        return f"✅ 解压完成 → {dest}\n   共释放 {registered} 个文件"

    # ── 标签 / 收藏 ───────────────────────────────────────────────────────────

    def manage_tag(
        self,
        action: str,
        path: str = "",
        tag: str = "",
    ) -> str:
        """管理文件标签。"""
        from app.core.file.file_registry import get_file_registry
        reg = get_file_registry()
        action = action.strip().lower()

        if action == "list_all":
            tags = reg.list_all_tags()
            if not tags:
                return "当前没有任何标签。"
            lines = [f"所有标签（共 {len(tags)} 个）："]
            for t in tags:
                lines.append(f"  [{t['count']}] {t['tag']}")
            return "\n".join(lines)

        if action == "files_by_tag":
            if not tag:
                return "错误：请提供 tag 参数"
            paths = reg.list_by_tag(tag)
            if not paths:
                return f"没有标记了 '{tag}' 的文件。"
            lines = [f"标签 '{tag}' 的文件（{len(paths)} 个）："]
            for i, p in enumerate(paths, 1):
                lines.append(f"  {i}. {p}")
            return "\n".join(lines)

        if not path:
            return "错误：请提供 path 参数"

        if action == "list":
            tags = reg.get_tags(path)
            if not tags:
                return f"文件没有标签：{path}"
            return f"文件标签：{', '.join(tags)}\n路径：{path}"

        if action == "add":
            if not tag:
                return "错误：请提供 tag 参数"
            ok = reg.add_tag(path, tag)
            return f"✅ 已添加标签 '{tag}' → {Path(path).name}" if ok else "操作失败"

        if action == "remove":
            if not tag:
                return "错误：请提供 tag 参数"
            ok = reg.remove_tag(path, tag)
            return f"✅ 已移除标签 '{tag}'" if ok else f"标签 '{tag}' 不存在"

        if action == "clear":
            n = reg.clear_tags(path)
            return f"✅ 已清除 {n} 个标签"

        return f"未知操作：{action}（支持 add / remove / list / clear / list_all / files_by_tag）"

    def manage_favorite(self, action: str, path: str = "") -> str:
        """管理收藏夹。"""
        from app.core.file.file_registry import get_file_registry
        reg = get_file_registry()
        action = action.strip().lower()

        if action == "list":
            favs = reg.list_favorites()
            if not favs:
                return "收藏夹为空。"
            lines = [f"⭐ 收藏夹（{len(favs)} 个文件）："]
            for i, p in enumerate(favs, 1):
                name = Path(p).name
                lines.append(f"  {i}. {name}\n      {p}")
            return "\n".join(lines)

        if not path:
            return "错误：请提供 path 参数"

        if action == "add":
            ok = reg.add_favorite(path)
            return f"⭐ 已加入收藏：{Path(path).name}" if ok else "操作失败"

        if action == "remove":
            ok = reg.remove_favorite(path)
            return f"✅ 已取消收藏：{Path(path).name}" if ok else "该文件不在收藏夹中"

        return f"未知操作：{action}（支持 add / remove / list）"

    # ── 智能摘要 ──────────────────────────────────────────────────────────────

    def summarize_file(self, path: str, focus: str = "") -> str:
        """使用 LLM 对文件内容生成摘要。"""
        p = Path(path)
        if not p.exists():
            return f"错误：文件不存在 → {path}"
        if not p.is_file():
            return f"错误：不是文件 → {path}"
        # 提取文本内容
        try:
            from app.core.file.file_registry import _extract_text_preview
            content = _extract_text_preview(str(p), max_chars=6000)
        except Exception as e:
            return f"内容提取失败：{e}"
        if not content or not content.strip():
            return f"无法提取文本内容（文件类型：{p.suffix}），无法生成摘要。"
        # 调用 LLM
        focus_hint = f"\n请重点关注：{focus}" if focus else ""
        prompt = (
            f"请对以下文件内容生成一段简洁的中文摘要（3-5 句话），涵盖主要信息点。{focus_hint}\n\n"
            f"文件名：{p.name}\n\n"
            f"内容：\n{content}"
        )
        try:
            from app.core.llm.gemini import GeminiProvider
            llm = GeminiProvider()
            resp = llm.generate_content(
                prompt=prompt,
                model="gemini-2.5-flash",
                system_instruction="你是一个专业的文件摘要助手，输出简洁精准的中文摘要。",
            )
            text = ""
            if isinstance(resp, dict):
                text = (
                    resp.get("text") or
                    resp.get("content") or
                    resp.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                )
            if not text:
                text = str(resp)
            return f"📄 {p.name}\n\n{text.strip()}"
        except Exception as e:
            return f"LLM 摘要失败（{e}），以下为原始内容片段：\n\n{content[:500]}"

    # ── 撤销 ──────────────────────────────────────────────────────────────────

    def undo_last_op(self) -> str:
        """撤销上一次文件操作。"""
        from app.core.file.file_registry import get_file_registry
        reg = get_file_registry()
        op = reg.pop_last_undoable_op()
        if not op:
            return "没有可撤销的操作记录。"
        op_type = op.get("op_type", "")
        src = op.get("src_path", "")
        dst = op.get("dst_path", "")
        meta = op.get("meta", {})

        try:
            if op_type == "rename" and src and dst:
                p = Path(dst)
                if p.exists():
                    p.rename(Path(src))
                    reg.update_path(dst, src)
                    return f"✅ 已撤销重命名：{Path(dst).name}  →  {Path(src).name}"
                return f"撤销失败：文件不存在 → {dst}"

            elif op_type == "move" and src and dst:
                p = Path(dst)
                if p.exists():
                    shutil.move(str(p), src)
                    reg.update_path(dst, src)
                    return f"✅ 已撤销移动：{Path(dst).name} 已还原到 {Path(src).parent}"
                return f"撤销失败：文件不存在 → {dst}"

            elif op_type == "copy" and dst:
                p = Path(dst)
                if p.exists():
                    p.unlink()
                    reg.delete(dst)
                    return f"✅ 已撤销复制：副本 {Path(dst).name} 已删除"
                return f"撤销失败：副本不存在 → {dst}"

            elif op_type == "delete" and src:
                if meta.get("trash"):
                    return (
                        f"文件 '{Path(src).name}' 已送入系统回收站，"
                        "请手动从回收站还原（右键→还原）。"
                    )
                return f"⚠️ 该文件已永久删除，无法自动撤销：{src}"

            else:
                return f"不支持撤销的操作类型：{op_type}"
        except Exception as e:
            return f"撤销失败：{e}"

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _scanner_fallback(query: str, limit: int) -> List[Dict[str, Any]]:
        """尝试通过 FileScanner 补充搜索结果。"""
        import importlib
        scanner_mod = importlib.import_module("web.file_scanner")
        FileScanner = getattr(scanner_mod, "FileScanner", None)
        if not FileScanner:
            return []
        scanner = FileScanner()
        raw = scanner.search_files(query, limit=limit)
        results = []
        for item in (raw if isinstance(raw, list) else []):
            path = item.get("path") or item.get("file_path") or ""
            if path:
                results.append({
                    "path": path,
                    "name": Path(path).name,
                    "category": item.get("category", "其他"),
                    "size_kb": item.get("size_kb", 0),
                    "snippet": item.get("snippet", ""),
                })
        return results


# ============================================================================
# 便捷注册函数
# ============================================================================

def register_file_tools(registry: Any):
    """
    将 FileToolsPlugin 注册到 ToolRegistry。

    用法::
        from app.core.file.file_tools import register_file_tools
        register_file_tools(tool_registry_instance)
    """
    plugin = FileToolsPlugin()
    registry.register_plugin(plugin)
    logger.info("[FileTools] ✅ 文件工具已注册到 ToolRegistry（23 个工具）")

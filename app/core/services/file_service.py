#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件管理服务 - 提供完整的本地文件操作能力
包含: 读写、编辑、复制/移动/删除、目录管理、元数据查询、智能文件查找
"""

import os
import re
import fnmatch
import shutil
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class FileService:
    """本地文件管理服务"""

    # 不允许写入/删除的系统保护路径（Windows）
    _PROTECTED_DIRS = {
        "windows", "system32", "syswow64", "program files",
        "program files (x86)", "system volume information",
    }

    def __init__(self, workspace_dir: str = None, backup_enabled: bool = True):
        """
        Args:
            workspace_dir: 工作目录（默认为 workspace/）
            backup_enabled: 是否自动备份（修改前创建 .bak）
        """
        if workspace_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            workspace_dir = os.path.join(project_root, "workspace")

        self.workspace_dir = Path(workspace_dir)
        self.backup_enabled = backup_enabled
        self.backup_dir = self.workspace_dir / "_backups"

        if backup_enabled:
            try:
                self.backup_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Could not create backup directory: {e}")

    def is_safe_path(self, file_path: str) -> bool:
        """路径安全检查：拒绝系统保护目录下的写操作目标"""
        try:
            parts = Path(file_path).resolve().parts
            lower_parts = {p.lower() for p in parts}
            return not (lower_parts & self._PROTECTED_DIRS)
        except Exception:
            return False

    # ────────────────────────────────────────────────────────────
    # 基础读写
    # ────────────────────────────────────────────────────────────

    def read_file(self, file_path: str, max_chars: int = 0) -> Dict[str, Any]:
        """读取文件内容。max_chars=0 表示不限制。"""
        try:
            path = Path(file_path)
            if not path.exists():
                return {"success": False, "error": f"文件不存在: {file_path}"}
            if not path.is_file():
                return {"success": False, "error": "路径不是文件"}

            for encoding in ["utf-8", "gbk", "gb2312", "utf-16", "latin-1"]:
                try:
                    content = path.read_text(encoding=encoding)
                    if max_chars and len(content) > max_chars:
                        content = content[:max_chars] + f"\n...[已截断，共 {len(content)} 字符]"
                    lines = content.splitlines()
                    return {
                        "success": True,
                        "content": content,
                        "lines": len(lines),
                        "encoding": encoding,
                        "size": path.stat().st_size,
                        "path": str(path.resolve()),
                    }
                except UnicodeDecodeError:
                    continue

            return {"success": False, "error": "无法解码文件（不支持的编码）"}
        except Exception as e:
            return {"success": False, "error": f"读取失败: {str(e)}"}

    def _create_backup(self, path: Path) -> Optional[str]:
        """创建备份文件，返回备份路径"""
        if not self.backup_enabled:
            return None
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{path.stem}_{timestamp}{path.suffix}.bak"
            backup_path = self.backup_dir / backup_name
            shutil.copy2(path, backup_path)
            return str(backup_path)
        except Exception as e:
            logger.warning(f"备份失败: {e}")
            return None

    def write_file(self, file_path: str, content: str, encoding: str = "utf-8", create_backup: bool = True) -> Dict[str, Any]:
        """写入文件（覆盖）"""
        try:
            if not self.is_safe_path(file_path):
                return {"success": False, "error": "拒绝写入系统保护目录"}

            path = Path(file_path)
            backup_path = None
            if path.exists() and create_backup:
                backup_path = self._create_backup(path)

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)

            return {
                "success": True,
                "path": str(path.resolve()),
                "backup": backup_path,
                "size": path.stat().st_size,
            }
        except Exception as e:
            return {"success": False, "error": f"写入失败: {str(e)}"}

    def append_text(self, file_path: str, text: str, newline_before: bool = True) -> Dict[str, Any]:
        """在文件末尾追加文本"""
        try:
            path = Path(file_path)
            if not self.is_safe_path(file_path):
                return {"success": False, "error": "拒绝写入系统保护目录"}
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                if newline_before and path.stat().st_size > 0 if path.exists() else False:
                    f.write("\n")
                f.write(text)
            return {"success": True, "path": str(path.resolve()), "size": path.stat().st_size}
        except Exception as e:
            return {"success": False, "error": f"追加失败: {str(e)}"}

    def replace_text(self, file_path: str, old_text: str, new_text: str) -> Dict[str, Any]:
        """替换文件中的文本"""
        res = self.read_file(file_path)
        if not res["success"]:
            return res
        content = res["content"]
        encoding = res["encoding"]
        if old_text not in content:
            return {"success": False, "error": "未找到要替换的文本"}
        count = content.count(old_text)
        new_content = content.replace(old_text, new_text)
        result = self.write_file(file_path, new_content, encoding=encoding)
        if result["success"]:
            result["replacements"] = count
        return result

    def insert_line(self, file_path: str, line_number: int, text: str, mode: str = "after") -> Dict[str, Any]:
        """
        在指定行号插入文本。
        mode: 'after'（行后）或 'before'（行前）
        line_number 从 1 开始。
        """
        res = self.read_file(file_path)
        if not res["success"]:
            return res
        lines = res["content"].splitlines(keepends=True)
        encoding = res["encoding"]
        idx = line_number - 1
        if idx < 0 or idx > len(lines):
            return {"success": False, "error": f"行号 {line_number} 超出范围（共 {len(lines)} 行）"}
        insert_text = text if text.endswith("\n") else text + "\n"
        if mode == "before":
            lines.insert(idx, insert_text)
        else:
            lines.insert(idx + 1, insert_text)
        return self.write_file(file_path, "".join(lines), encoding=encoding)

    def delete_lines(self, file_path: str, start_line: int, end_line: int = None) -> Dict[str, Any]:
        """
        删除指定行范围（包含两端）。
        end_line 默认等于 start_line（只删一行）。
        """
        res = self.read_file(file_path)
        if not res["success"]:
            return res
        lines = res["content"].splitlines(keepends=True)
        encoding = res["encoding"]
        if end_line is None:
            end_line = start_line
        s, e = start_line - 1, end_line - 1
        if s < 0 or e >= len(lines) or s > e:
            return {"success": False, "error": f"行号范围 {start_line}-{end_line} 无效（共 {len(lines)} 行）"}
        del lines[s:e + 1]
        result = self.write_file(file_path, "".join(lines), encoding=encoding)
        if result["success"]:
            result["deleted_lines"] = e - s + 1
        return result

    def patch_file(self, file_path: str, patches: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        批量替换：一次读写完成多处文本替换，比多次调用 replace_text 更高效。

        Args:
            file_path: 目标文件路径
            patches: 替换列表，每项为 {"old": "...", "new": "..."}

        Returns:
            success, total_replacements, not_found（未匹配的 old_text 列表）
        """
        res = self.read_file(file_path)
        if not res["success"]:
            return res
        content = res["content"]
        encoding = res["encoding"]

        total = 0
        not_found: List[str] = []
        for patch in patches:
            old_text = patch.get("old", "")
            new_text = patch.get("new", "")
            if not old_text:
                continue
            if old_text not in content:
                not_found.append(old_text[:80])
                continue
            count = content.count(old_text)
            content = content.replace(old_text, new_text)
            total += count

        if total == 0 and not_found:
            return {"success": False, "error": f"所有替换目标均未找到: {not_found}"}

        result = self.write_file(file_path, content, encoding=encoding)
        if result["success"]:
            result["total_replacements"] = total
            result["not_found"] = not_found
        return result

    # ────────────────────────────────────────────────────────────
    # 文件系统操作
    # ────────────────────────────────────────────────────────────

    def delete_file(self, file_path: str) -> Dict[str, Any]:
        """删除文件（删前自动备份）"""
        try:
            if not self.is_safe_path(file_path):
                return {"success": False, "error": "拒绝删除系统保护目录中的文件"}
            path = Path(file_path)
            if not path.exists():
                return {"success": False, "error": f"文件不存在: {file_path}"}
            if not path.is_file():
                return {"success": False, "error": "路径不是文件，请用 delete_directory 删除目录"}
            backup_path = self._create_backup(path)
            path.unlink()
            return {
                "success": True,
                "deleted": str(path.resolve()),
                "backup": backup_path,
                "message": f"已删除: {path.name}",
            }
        except Exception as e:
            return {"success": False, "error": f"删除失败: {str(e)}"}

    def copy_file(self, source: str, destination: str, overwrite: bool = False) -> Dict[str, Any]:
        """复制文件"""
        try:
            if not self.is_safe_path(destination):
                return {"success": False, "error": "目标路径在系统保护目录中"}
            src = Path(source)
            dst = Path(destination)
            if not src.exists():
                return {"success": False, "error": f"源文件不存在: {source}"}
            if not src.is_file():
                return {"success": False, "error": "源路径不是文件"}
            if dst.is_dir():
                dst = dst / src.name
            if dst.exists() and not overwrite:
                return {"success": False, "error": f"目标文件已存在: {dst}（使用 overwrite=true 强制覆盖）"}
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return {
                "success": True,
                "source": str(src.resolve()),
                "destination": str(dst.resolve()),
                "size": dst.stat().st_size,
                "message": f"已复制: {src.name} → {dst}",
            }
        except Exception as e:
            return {"success": False, "error": f"复制失败: {str(e)}"}

    def move_file(self, source: str, destination: str, overwrite: bool = False) -> Dict[str, Any]:
        """移动文件（跨盘符使用 copy+delete）"""
        try:
            if not self.is_safe_path(source):
                return {"success": False, "error": "源路径在系统保护目录中"}
            if not self.is_safe_path(destination):
                return {"success": False, "error": "目标路径在系统保护目录中"}
            src = Path(source)
            dst = Path(destination)
            if not src.exists():
                return {"success": False, "error": f"源文件不存在: {source}"}
            if dst.is_dir():
                dst = dst / src.name
            if dst.exists() and not overwrite:
                return {"success": False, "error": f"目标文件已存在: {dst}"}
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return {
                "success": True,
                "source": str(src),
                "destination": str(dst.resolve()),
                "message": f"已移动: {src.name} → {dst}",
            }
        except Exception as e:
            return {"success": False, "error": f"移动失败: {str(e)}"}

    def rename_file(self, file_path: str, new_name: str) -> Dict[str, Any]:
        """重命名文件（不改变目录）"""
        try:
            if not self.is_safe_path(file_path):
                return {"success": False, "error": "路径在系统保护目录中"}
            path = Path(file_path)
            if not path.exists():
                return {"success": False, "error": f"文件不存在: {file_path}"}
            # 禁止路径分隔符（防止隐式移动）
            if "/" in new_name or "\\" in new_name:
                return {"success": False, "error": "新名称不能包含路径分隔符，请用 move_file 移动文件"}
            new_path = path.parent / new_name
            if new_path.exists():
                return {"success": False, "error": f"目标名称已存在: {new_name}"}
            path.rename(new_path)
            return {
                "success": True,
                "old_path": str(path.resolve()),
                "new_path": str(new_path.resolve()),
                "message": f"已重命名: {path.name} → {new_name}",
            }
        except Exception as e:
            return {"success": False, "error": f"重命名失败: {str(e)}"}

    def create_directory(self, dir_path: str) -> Dict[str, Any]:
        """创建目录（含所有父级）"""
        try:
            if not self.is_safe_path(dir_path):
                return {"success": False, "error": "路径在系统保护目录中"}
            path = Path(dir_path)
            path.mkdir(parents=True, exist_ok=True)
            return {
                "success": True,
                "path": str(path.resolve()),
                "message": f"目录已创建: {path}",
            }
        except Exception as e:
            return {"success": False, "error": f"创建目录失败: {str(e)}"}

    def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """获取文件/目录的详细元数据"""
        try:
            path = Path(file_path)
            if not path.exists():
                return {"success": False, "error": f"路径不存在: {file_path}"}
            stat = path.stat()
            info = {
                "success": True,
                "path": str(path.resolve()),
                "name": path.name,
                "suffix": path.suffix.lower(),
                "is_file": path.is_file(),
                "is_dir": path.is_dir(),
                "size": stat.st_size,
                "size_human": self._human_size(stat.st_size),
                "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "parent": str(path.parent.resolve()),
            }
            if path.is_dir():
                try:
                    children = list(path.iterdir())
                    info["children_count"] = len(children)
                    info["files_count"] = sum(1 for c in children if c.is_file())
                    info["dirs_count"] = sum(1 for c in children if c.is_dir())
                except PermissionError:
                    info["children_count"] = -1
            return info
        except Exception as e:
            return {"success": False, "error": f"获取文件信息失败: {str(e)}"}

    def list_directory(self, directory: str, recursive: bool = False,
                       max_items: int = 100, include_dirs: bool = True) -> Dict[str, Any]:
        """列出目录内容，包含完整路径、大小、修改时间等元数据"""
        try:
            path = Path(directory)
            if not path.exists():
                return {"success": False, "error": f"目录不存在: {directory}"}
            if not path.is_dir():
                return {"success": False, "error": "路径不是目录"}

            items = []
            iterator = path.rglob("*") if recursive else path.iterdir()
            count = 0
            for p in iterator:
                if not include_dirs and p.is_dir():
                    continue
                try:
                    stat = p.stat()
                    items.append({
                        "name": p.name,
                        "path": str(p.resolve()),
                        "type": "dir" if p.is_dir() else "file",
                        "size": stat.st_size if p.is_file() else None,
                        "size_human": self._human_size(stat.st_size) if p.is_file() else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "suffix": p.suffix.lower() if p.is_file() else None,
                    })
                    count += 1
                    if count >= max_items:
                        break
                except (PermissionError, OSError):
                    continue

            return {
                "success": True,
                "directory": str(path.resolve()),
                "items": items,
                "count": count,
                "truncated": count >= max_items,
            }
        except Exception as e:
            return {"success": False, "error": f"列目录失败: {str(e)}"}

    # ────────────────────────────────────────────────────────────
    # 智能文件查找
    # ────────────────────────────────────────────────────────────

    def search_files(
        self,
        query: str,
        search_dir: str = None,
        file_type: str = None,
        max_results: int = 20,
        search_content: bool = False,
    ) -> Dict[str, Any]:
        """
        智能本地文件查找。

        优先使用 FileScanner 全盘索引（如已扫描），
        否则在 search_dir（默认用户主目录）递归 glob 查找。

        Args:
            query: 文件名关键词（支持多词、模糊匹配）
            search_dir: 搜索根目录（None = 用户主目录 + 桌面）
            file_type: 过滤扩展名，如 'pdf', 'docx', '.xlsx'（可选）
            max_results: 最大结果数
            search_content: 是否同时搜索文件内容（仅对小文本文件）
        """
        try:
            results = []

            # --- 尝试使用 FileScanner 索引 ---
            try:
                from web.file_scanner import FileScanner
                if FileScanner.is_indexed():
                    scanner_results = FileScanner.search(
                        query=query,
                        limit=max_results,
                        category=None,
                    )
                    for r in scanner_results:
                        entry = {
                            "name": r.get("name", ""),
                            "path": r.get("path", ""),
                            "size_human": r.get("size_human", ""),
                            "modified": r.get("mtime_human", ""),
                            "category": r.get("category", ""),
                            "source": "index",
                        }
                        # 过滤文件类型
                        if file_type:
                            ext = file_type if file_type.startswith(".") else f".{file_type}"
                            if not r.get("path", "").lower().endswith(ext.lower()):
                                continue
                        results.append(entry)
                    if results:
                        return {
                            "success": True,
                            "query": query,
                            "results": results[:max_results],
                            "count": len(results[:max_results]),
                            "source": "全盘索引",
                        }
            except ImportError:
                pass

            # --- 回退：在指定目录递归搜索 ---
            search_roots = []
            if search_dir:
                search_roots = [Path(search_dir)]
            else:
                home = Path.home()
                search_roots = [
                    home / "Desktop",
                    home / "Documents",
                    home / "Downloads",
                    home,
                ]
                # 也搜索 workspace
                search_roots.append(self.workspace_dir)

            keywords = [k.lower() for k in query.split() if k]
            ext_filter = None
            if file_type:
                ext_filter = file_type if file_type.startswith(".") else f".{file_type}"

            skips = {
                "node_modules", ".git", "__pycache__", ".venv", "venv",
                "site-packages", "AppData", "$Recycle.Bin",
            }

            seen = set()
            for root in search_roots:
                if not root.exists():
                    continue
                for p in root.rglob("*"):
                    # 跳过系统目录
                    if any(s in p.parts for s in skips):
                        continue
                    if not p.is_file():
                        continue
                    if str(p) in seen:
                        continue

                    name_lower = p.name.lower()

                    # 扩展名过滤
                    if ext_filter and not name_lower.endswith(ext_filter.lower()):
                        continue

                    # 关键词匹配（所有词都要包含）
                    if not all(kw in name_lower for kw in keywords):
                        # 尝试模糊匹配
                        if not any(fnmatch.fnmatch(name_lower, f"*{kw}*") for kw in keywords):
                            # 内容搜索
                            if search_content and p.suffix.lower() in {".txt", ".md", ".py", ".js", ".csv"}:
                                try:
                                    text = p.read_text(encoding="utf-8", errors="ignore")
                                    if not any(kw in text.lower() for kw in keywords):
                                        continue
                                except Exception:
                                    continue
                            else:
                                continue

                    try:
                        stat = p.stat()
                        seen.add(str(p))
                        results.append({
                            "name": p.name,
                            "path": str(p.resolve()),
                            "size_human": self._human_size(stat.st_size),
                            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            "suffix": p.suffix.lower(),
                            "source": "scan",
                        })
                    except (PermissionError, OSError):
                        continue

                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break

            return {
                "success": True,
                "query": query,
                "results": results,
                "count": len(results),
                "source": "目录扫描",
            }
        except Exception as e:
            return {"success": False, "error": f"文件查找失败: {str(e)}"}

    def restore_backup(self, backup_file: str, restore_to: str = None) -> Dict[str, Any]:
        """从备份文件恢复（restore_to 为空则恢复到原路径）"""
        try:
            backup_path = Path(backup_file)
            if not backup_path.exists():
                return {"success": False, "error": f"备份文件不存在: {backup_file}"}
            if restore_to:
                dest = Path(restore_to)
            else:
                # 备份格式: stem_YYYYMMDD_HHMMSS.suffix.bak → stem.suffix
                name = backup_path.stem  # e.g. report_20260304_120000.txt
                # 去除时间戳后缀
                name_cleaned = re.sub(r"_\d{8}_\d{6}$", "", Path(name).stem)
                ext = Path(name).suffix  # original extension
                dest = backup_path.parent.parent / (name_cleaned + ext)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, dest)
            return {
                "success": True,
                "restored_to": str(dest.resolve()),
                "from_backup": str(backup_path.resolve()),
                "message": f"已从备份恢复: {dest.name}",
            }
        except Exception as e:
            return {"success": False, "error": f"恢复失败: {str(e)}"}

    def list_backups(self) -> Dict[str, Any]:
        """列出所有备份文件"""
        try:
            if not self.backup_dir.exists():
                return {"success": True, "backups": [], "count": 0}
            backups = sorted(self.backup_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
            items = []
            for b in backups:
                stat = b.stat()
                items.append({
                    "name": b.name,
                    "path": str(b.resolve()),
                    "size_human": self._human_size(stat.st_size),
                    "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
            return {"success": True, "backups": items, "count": len(items)}
        except Exception as e:
            return {"success": False, "error": f"列备份失败: {str(e)}"}

    # ────────────────────────────────────────────────────────────
    # 工具方法
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _human_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    # 兼容旧接口
    def list_files(self, directory: str, recursive: bool = False, max_files: int = 50) -> Dict[str, Any]:
        """[兼容旧接口] 列出目录文件名"""
        result = self.list_directory(directory, recursive=recursive, max_items=max_files, include_dirs=False)
        if result["success"]:
            result["files"] = [item["name"] for item in result.get("items", [])]
        return result

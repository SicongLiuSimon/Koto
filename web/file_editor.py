#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件编辑器 - 提供类似 Copilot 的本地文件编辑能力
支持：读取、替换、插入、删除、智能编辑
"""

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class FileEditor:
    """本地文件编辑器"""

    def __init__(self, workspace_dir: str = None, backup_enabled: bool = True):
        """
        Args:
            workspace_dir: 工作目录（默认为 workspace/）
            backup_enabled: 是否自动备份（修改前创建 .bak）
        """
        if workspace_dir is None:
            workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "workspace"
            )
        self.workspace_dir = Path(workspace_dir)
        self.backup_enabled = backup_enabled
        self.backup_dir = self.workspace_dir / "_backups"
        if backup_enabled:
            self.backup_dir.mkdir(parents=True, exist_ok=True)

    def is_safe_path(self, file_path: str) -> bool:
        """检查路径安全性（防止越界访问）"""
        try:
            path = Path(file_path).resolve()
            # 允许访问 workspace 和常见用户目录
            allowed_roots = [
                self.workspace_dir.resolve(),
                Path.home().resolve(),
                Path("C:/Users").resolve(),
                Path("D:/").resolve(),
            ]
            return any(path.is_relative_to(root) for root in allowed_roots)
        except (OSError, ValueError) as e:
            logger.debug("Path validation failed: %s", e)
            return False

    def read_file(self, file_path: str) -> Dict[str, Any]:
        """
        读取文件内容

        Returns:
            {
                "success": bool,
                "content": str,
                "lines": int,
                "encoding": str,
                "error": str
            }
        """
        try:
            if not self.is_safe_path(file_path):
                return {"success": False, "error": "路径访问被拒绝（安全限制）"}

            path = Path(file_path)
            if not path.exists():
                return {"success": False, "error": f"文件不存在: {file_path}"}

            if not path.is_file():
                return {"success": False, "error": "不是文件"}

            # 尝试多种编码
            for encoding in ["utf-8", "gbk", "gb2312", "utf-16"]:
                try:
                    content = path.read_text(encoding=encoding)
                    lines = content.splitlines()
                    return {
                        "success": True,
                        "content": content,
                        "lines": len(lines),
                        "line_list": lines,
                        "encoding": encoding,
                        "size": path.stat().st_size,
                        "path": str(path.resolve()),
                    }
                except UnicodeDecodeError:
                    continue

            return {"success": False, "error": "无法解码文件（不支持的编码）"}

        except Exception as e:
            return {"success": False, "error": f"读取失败: {str(e)}"}

    def create_backup(self, file_path: str) -> Optional[str]:
        """创建备份文件"""
        if not self.backup_enabled:
            return None

        try:
            path = Path(file_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{path.stem}_{timestamp}{path.suffix}.bak"
            backup_path = self.backup_dir / backup_name

            shutil.copy2(path, backup_path)
            return str(backup_path)
        except Exception as e:
            logger.info(f"[FileEditor] 备份失败: {e}")
            return None

    def write_file(
        self,
        file_path: str,
        content: str,
        encoding: str = "utf-8",
        create_backup: bool = True,
    ) -> Dict[str, Any]:
        """
        写入文件内容

        Args:
            file_path: 文件路径
            content: 新内容
            encoding: 编码格式
            create_backup: 是否先备份

        Returns:
            {"success": bool, "backup": str, "error": str}
        """
        try:
            if not self.is_safe_path(file_path):
                return {"success": False, "error": "路径访问被拒绝"}

            path = Path(file_path)
            backup_path = None

            # 如果文件存在，先备份
            if path.exists() and create_backup:
                backup_path = self.create_backup(file_path)

            # 确保目录存在
            path.parent.mkdir(parents=True, exist_ok=True)

            # 写入内容
            path.write_text(content, encoding=encoding)

            return {
                "success": True,
                "path": str(path.resolve()),
                "backup": backup_path,
                "size": path.stat().st_size,
            }

        except Exception as e:
            return {"success": False, "error": f"写入失败: {str(e)}"}

    def replace_text(
        self,
        file_path: str,
        old_text: str,
        new_text: str,
        use_regex: bool = False,
        max_replacements: int = -1,
    ) -> Dict[str, Any]:
        """
        替换文件中的文本

        Args:
            file_path: 文件路径
            old_text: 要替换的文本（或正则表达式）
            new_text: 新文本
            use_regex: 是否使用正则表达式
            max_replacements: 最大替换次数（-1=全部）

        Returns:
            {"success": bool, "replacements": int, "preview": str}
        """
        # 读取文件
        read_result = self.read_file(file_path)
        if not read_result["success"]:
            return read_result

        content = read_result["content"]
        encoding = read_result["encoding"]

        # 执行替换
        if use_regex:
            if max_replacements == -1:
                new_content = re.sub(old_text, new_text, content)
            else:
                new_content = re.sub(
                    old_text, new_text, content, count=max_replacements
                )
            # 计算替换次数
            replacements = len(re.findall(old_text, content))
        else:
            if max_replacements == -1:
                new_content = content.replace(old_text, new_text)
                replacements = content.count(old_text)
            else:
                parts = content.split(old_text, max_replacements)
                new_content = new_text.join(parts)
                replacements = len(parts) - 1

        if replacements == 0:
            return {"success": False, "error": "未找到要替换的文本", "replacements": 0}

        # 写入文件
        write_result = self.write_file(file_path, new_content, encoding=encoding)

        if write_result["success"]:
            # 生成预览（显示前3处变化）
            preview_lines = []
            lines_old = content.splitlines()
            lines_new = new_content.splitlines()

            for i, (old_line, new_line) in enumerate(zip(lines_old, lines_new), 1):
                if old_line != new_line:
                    preview_lines.append(f"L{i}: {old_line[:50]} → {new_line[:50]}")
                    if len(preview_lines) >= 3:
                        break

            return {
                "success": True,
                "replacements": replacements,
                "preview": "\n".join(preview_lines),
                "backup": write_result.get("backup"),
            }

        return write_result

    def insert_line(
        self, file_path: str, line_number: int, text: str, mode: str = "after"
    ) -> Dict[str, Any]:
        """
        在指定行插入文本

        Args:
            file_path: 文件路径
            line_number: 行号（1-based）
            text: 要插入的文本
            mode: "before" 或 "after"（在指定行之前/之后插入）
        """
        read_result = self.read_file(file_path)
        if not read_result["success"]:
            return read_result

        lines = read_result["line_list"]
        encoding = read_result["encoding"]

        # 行号校验
        if line_number < 1 or line_number > len(lines) + 1:
            return {
                "success": False,
                "error": f"行号越界: {line_number}（文件共 {len(lines)} 行）",
            }

        # 插入文本
        insert_pos = line_number - 1 if mode == "before" else line_number
        lines.insert(insert_pos, text)

        new_content = "\n".join(lines)
        write_result = self.write_file(file_path, new_content, encoding=encoding)

        if write_result["success"]:
            return {
                "success": True,
                "message": f"已在第 {line_number} 行{mode == 'before' and '之前' or '之后'}插入内容",
                "backup": write_result.get("backup"),
            }

        return write_result

    def delete_lines(
        self, file_path: str, start_line: int, end_line: int = None
    ) -> Dict[str, Any]:
        """
        删除指定行

        Args:
            file_path: 文件路径
            start_line: 起始行号（1-based）
            end_line: 结束行号（None=仅删除单行）
        """
        read_result = self.read_file(file_path)
        if not read_result["success"]:
            return read_result

        lines = read_result["line_list"]
        encoding = read_result["encoding"]

        if end_line is None:
            end_line = start_line

        # 行号校验
        if start_line < 1 or end_line > len(lines) or start_line > end_line:
            return {"success": False, "error": f"行号无效: {start_line}-{end_line}"}

        # 删除行（转为 0-based index）
        deleted_lines = lines[start_line - 1 : end_line]
        del lines[start_line - 1 : end_line]

        new_content = "\n".join(lines)
        write_result = self.write_file(file_path, new_content, encoding=encoding)

        if write_result["success"]:
            return {
                "success": True,
                "message": f"已删除第 {start_line}-{end_line} 行（共 {end_line - start_line + 1} 行）",
                "deleted_content": "\n".join(deleted_lines),
                "backup": write_result.get("backup"),
            }

        return write_result

    def append_text(
        self, file_path: str, text: str, newline_before: bool = True
    ) -> Dict[str, Any]:
        """在文件末尾追加内容"""
        read_result = self.read_file(file_path)
        if not read_result["success"]:
            return read_result

        content = read_result["content"]
        encoding = read_result["encoding"]

        separator = "\n" if newline_before else ""
        new_content = content + separator + text

        write_result = self.write_file(file_path, new_content, encoding=encoding)

        if write_result["success"]:
            return {
                "success": True,
                "message": "已在文件末尾追加内容",
                "backup": write_result.get("backup"),
            }

        return write_result

    def smart_edit(
        self, file_path: str, instruction: str, ai_model=None
    ) -> Dict[str, Any]:
        """
        智能编辑 - 使用 AI 理解用户意图并执行编辑

        Args:
            file_path: 文件路径
            instruction: 用户指令（如"把所有的 TODO 改成 DONE"）
            ai_model: AI 模型实例（用于理解指令）

        Returns:
            {"success": bool, "operation": str, "result": dict}
        """
        # 读取文件
        read_result = self.read_file(file_path)
        if not read_result["success"]:
            return read_result

        content = read_result["content"]

        # 分析用户指令
        instruction_lower = instruction.lower()

        # 模式 1: 替换文本
        replace_patterns = [
            r"(?:把|将|替换)\s*[\"\'](.*?)[\"\']?\s*(?:改成|换成|替换成|改为)\s*[\"\'](.*?)[\"\']?",
            r"replace\s+[\"\'](.*?)[\"\']?\s+(?:with|to)\s+[\"\'](.*?)[\"\']?",
        ]

        for pattern in replace_patterns:
            match = re.search(pattern, instruction, re.IGNORECASE)
            if match:
                old_text = match.group(1)
                new_text = match.group(2)
                return {
                    "success": True,
                    "operation": "replace",
                    "result": self.replace_text(file_path, old_text, new_text),
                }

        # 模式 2: 删除行
        delete_patterns = [
            r"删除\s*第?\s*(\d+)\s*(?:到|至|-)\s*(\d+)\s*行",
            r"delete\s+lines?\s+(\d+)\s*(?:to|-)\s*(\d+)",
        ]

        for pattern in delete_patterns:
            match = re.search(pattern, instruction, re.IGNORECASE)
            if match:
                start = int(match.group(1))
                end = int(match.group(2))
                return {
                    "success": True,
                    "operation": "delete_lines",
                    "result": self.delete_lines(file_path, start, end),
                }

        # 模式 3: 插入内容
        insert_patterns = [
            r"在第?\s*(\d+)\s*行(?:之?前|前面)插入\s*[\"\'](.*?)[\"\']?",
            r"在第?\s*(\d+)\s*行(?:之?后|后面)插入\s*[\"\'](.*?)[\"\']?",
        ]

        for pattern in insert_patterns:
            match = re.search(pattern, instruction, re.IGNORECASE)
            if match:
                line_num = int(match.group(1))
                text = match.group(2)
                mode = "before" if "前" in instruction else "after"
                return {
                    "success": True,
                    "operation": "insert_line",
                    "result": self.insert_line(file_path, line_num, text, mode),
                }

        return {
            "success": False,
            "error": "无法理解编辑指令，请使用更明确的表达",
            "hint": "支持的指令：\n  - 把 'A' 改成 'B'\n  - 删除第 5-10 行\n  - 在第 3 行之后插入 'xxx'",
        }


def test_file_editor():
    """测试文件编辑器"""
    editor = FileEditor()
    test_file = editor.workspace_dir / "test_edit.txt"

    # 创建测试文件
    test_content = """Line 1: Hello
Line 2: World
Line 3: TODO: Fix this
Line 4: Another line
Line 5: End"""

    test_file.write_text(test_content, encoding="utf-8")
    logger.info(f"✅ 创建测试文件: {test_file}")

    # 测试 1: 读取
    logger.info("\n=== 测试读取 ===")
    result = editor.read_file(str(test_file))
    logger.info(f"成功: {result['success']}, 行数: {result.get('lines')}")

    # 测试 2: 替换
    logger.info("\n=== 测试替换 ===")
    result = editor.replace_text(str(test_file), "TODO", "DONE")
    logger.info(f"成功: {result['success']}, 替换次数: {result.get('replacements')}")
    logger.info(f"预览: {result.get('preview')}")

    # 测试 3: 插入
    logger.info("\n=== 测试插入 ===")
    result = editor.insert_line(
        str(test_file), 3, "Line 2.5: Inserted line", mode="after"
    )
    logger.info(f"成功: {result['success']}, 消息: {result.get('message')}")

    # 测试 4: 智能编辑
    logger.info("\n=== 测试智能编辑 ===")
    result = editor.smart_edit(str(test_file), "把 'World' 改成 '世界'")
    logger.info(
        f"操作: {result.get('operation')}, 结果: {result.get('result', {}).get('replacements')} 次替换"
    )

    # 显示最终内容
    logger.info(f"\n=== 最终内容 ===")
    final = editor.read_file(str(test_file))
    logger.info(final["content"])


if __name__ == "__main__":
    test_file_editor()

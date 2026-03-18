#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件批量处理器 - 批量重命名、格式转换、内容清洗
"""

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class BatchFileProcessor:
    """文件批量处理器"""

    def __init__(self, workspace_dir: str = None):
        if workspace_dir is None:
            workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "workspace"
            )
        self.workspace_dir = workspace_dir

    def batch_rename(
        self,
        directory: str,
        pattern: str = None,
        replacement: str = None,
        prefix: str = None,
        suffix: str = None,
        numbering: bool = False,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        批量重命名文件

        Args:
            directory: 目标目录
            pattern: 正则匹配模式（用于替换）
            replacement: 替换文本
            prefix: 添加前缀
            suffix: 添加后缀（扩展名前）
            numbering: 是否添加序号
            dry_run: 仅预览，不实际执行
        """
        if not os.path.exists(directory):
            return {"success": False, "error": "目录不存在"}

        files = [
            f
            for f in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, f))
        ]
        renamed = []
        errors = []

        for idx, old_name in enumerate(files, 1):
            old_path = os.path.join(directory, old_name)
            name, ext = os.path.splitext(old_name)

            new_name = name

            # 正则替换
            if pattern and replacement is not None:
                new_name = re.sub(pattern, replacement, new_name)

            # 添加前缀
            if prefix:
                new_name = prefix + new_name

            # 添加后缀
            if suffix:
                new_name = new_name + suffix

            # 添加序号
            if numbering:
                new_name = f"{new_name}_{idx:03d}"

            new_name = new_name + ext
            new_path = os.path.join(directory, new_name)

            if old_name != new_name:
                renamed.append({"old": old_name, "new": new_name, "path": old_path})

                if not dry_run:
                    try:
                        # 检查目标文件是否存在
                        if os.path.exists(new_path):
                            errors.append(f"{old_name} -> {new_name}: 目标文件已存在")
                        else:
                            os.rename(old_path, new_path)
                    except Exception as e:
                        errors.append(f"{old_name} -> {new_name}: {str(e)}")

        return {
            "success": True,
            "dry_run": dry_run,
            "total_files": len(files),
            "renamed_count": len(renamed),
            "error_count": len(errors),
            "renamed": renamed,
            "errors": errors,
        }

    def batch_convert(
        self,
        directory: str,
        source_ext: str,
        target_ext: str,
        output_dir: str = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        批量格式转换

        支持：
        - .txt <-> .md
        - .docx -> .txt
        - .md -> .docx
        """
        if not os.path.exists(directory):
            return {"success": False, "error": "目录不存在"}

        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        files = [
            f
            for f in os.listdir(directory)
            if f.endswith(source_ext) and os.path.isfile(os.path.join(directory, f))
        ]

        converted = []
        errors = []

        for file in files:
            input_path = os.path.join(directory, file)
            base_name = os.path.splitext(file)[0]
            output_file = base_name + target_ext
            output_path = os.path.join(output_dir or directory, output_file)

            if dry_run:
                converted.append(
                    {"source": file, "target": output_file, "output_path": output_path}
                )
                continue

            try:
                # 执行转换
                result = self._convert_file(
                    input_path, output_path, source_ext, target_ext
                )
                if result["success"]:
                    converted.append(
                        {
                            "source": file,
                            "target": output_file,
                            "output_path": output_path,
                        }
                    )
                else:
                    errors.append(f"{file}: {result.get('error')}")
            except Exception as e:
                errors.append(f"{file}: {str(e)}")

        return {
            "success": True,
            "dry_run": dry_run,
            "total_files": len(files),
            "converted_count": len(converted),
            "error_count": len(errors),
            "converted": converted,
            "errors": errors,
        }

    def _convert_file(
        self, input_path: str, output_path: str, source_ext: str, target_ext: str
    ) -> Dict[str, Any]:
        """执行单个文件转换"""
        try:
            # txt/md 互转
            if source_ext in [".txt", ".md"] and target_ext in [".txt", ".md"]:
                shutil.copy2(input_path, output_path)
                return {"success": True}

            # docx -> txt
            elif source_ext == ".docx" and target_ext == ".txt":
                from docx import Document

                doc = Document(input_path)
                text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(text)
                return {"success": True}

            # md -> docx
            elif source_ext == ".md" and target_ext == ".docx":
                with open(input_path, "r", encoding="utf-8") as f:
                    text = f.read()
                from web.document_generator import save_docx

                save_docx(
                    text,
                    output_dir=os.path.dirname(output_path),
                    filename=os.path.basename(output_path),
                )
                return {"success": True}

            else:
                return {
                    "success": False,
                    "error": f"不支持的转换: {source_ext} -> {target_ext}",
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def clean_duplicates(
        self, directory: str, by_content: bool = True, dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        清理重复文件

        Args:
            directory: 目标目录
            by_content: True=按内容哈希, False=按文件名
            dry_run: 仅预览
        """
        if not os.path.exists(directory):
            return {"success": False, "error": "目录不存在"}

        files = [
            f
            for f in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, f))
        ]

        seen = {}
        duplicates = []

        for file in files:
            file_path = os.path.join(directory, file)

            if by_content:
                # 按内容哈希
                import hashlib

                hasher = hashlib.md5()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hasher.update(chunk)
                key = hasher.hexdigest()
            else:
                # 按文件名
                key = file

            if key in seen:
                duplicates.append(
                    {"file": file, "duplicate_of": seen[key], "path": file_path}
                )

                if not dry_run:
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.info(f"删除失败 {file}: {e}")
            else:
                seen[key] = file

        return {
            "success": True,
            "dry_run": dry_run,
            "total_files": len(files),
            "duplicates_found": len(duplicates),
            "duplicates": duplicates,
        }

    def clean_text_content(
        self,
        file_path: str,
        output_path: str = None,
        remove_blank_lines: bool = True,
        remove_extra_spaces: bool = True,
        fix_punctuation: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        清洗文本内容

        Args:
            file_path: 输入文件
            output_path: 输出文件（None=覆盖原文件）
            remove_blank_lines: 移除多余空行
            remove_extra_spaces: 移除多余空格
            fix_punctuation: 修正标点符号
            dry_run: 仅预览
        """
        if not os.path.exists(file_path):
            return {"success": False, "error": "文件不存在"}

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in [".txt", ".md"]:
            return {"success": False, "error": "仅支持 .txt 和 .md 文件"}

        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        original_lines = len(text.split("\n"))
        original_size = len(text)

        # 清洗处理
        if remove_blank_lines:
            lines = text.split("\n")
            cleaned = []
            prev_blank = False
            for line in lines:
                is_blank = not line.strip()
                if is_blank and prev_blank:
                    continue
                cleaned.append(line)
                prev_blank = is_blank
            text = "\n".join(cleaned)

        if remove_extra_spaces:
            # 移除行首行尾空格
            lines = text.split("\n")
            text = "\n".join(line.strip() for line in lines)
            # 移除多余空格（保留单个）
            text = re.sub(r" +", " ", text)

        if fix_punctuation:
            # 中文标点后面不加空格
            text = re.sub(r"([，。！？；：])(\s+)", r"\1", text)
            # 英文标点后加空格
            text = re.sub(r"([,\.!?;:])([^\s])", r"\1 \2", text)

        cleaned_lines = len(text.split("\n"))
        cleaned_size = len(text)

        if not dry_run:
            output = output_path or file_path
            with open(output, "w", encoding="utf-8") as f:
                f.write(text)

        return {
            "success": True,
            "dry_run": dry_run,
            "input_file": file_path,
            "output_file": output_path or file_path,
            "original_lines": original_lines,
            "cleaned_lines": cleaned_lines,
            "lines_removed": original_lines - cleaned_lines,
            "original_size": original_size,
            "cleaned_size": cleaned_size,
            "size_reduction": original_size - cleaned_size,
        }


if __name__ == "__main__":
    processor = BatchFileProcessor()

    logger.info("=" * 60)
    logger.info("批量文件处理测试")
    logger.info("=" * 60)

    test_dir = os.path.join(processor.workspace_dir, "documents")

    # 测试批量重命名（预览）
    logger.info("\n1. 批量重命名预览（添加前缀'TEST_'）...")
    result = processor.batch_rename(test_dir, prefix="TEST_", dry_run=True)
    logger.info(f"   将重命名 {result['renamed_count']} 个文件")
    if result["renamed"][:3]:
        for r in result["renamed"][:3]:
            logger.info(f"   - {r['old']} -> {r['new']}")

    # 测试重复文件检测
    logger.info("\n2. 重复文件检测...")
    result = processor.clean_duplicates(test_dir, by_content=True, dry_run=True)
    logger.info(f"   找到 {result['duplicates_found']} 个重复文件")

    logger.info("\n✅ 批量处理器就绪")

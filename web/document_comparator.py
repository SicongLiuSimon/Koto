#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文档对比与总结器 - 离线版本差异分析、变更摘要
"""

import difflib
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


class DocumentComparator:
    """文档对比与总结器"""

    def __init__(self):
        self.supported_formats = [".txt", ".md", ".docx"]

    def compare_documents(
        self, file_a: str, file_b: str, output_format: str = "markdown"
    ) -> Dict[str, Any]:
        """
        对比两个文档的差异

        Args:
            file_a: 原始文档路径
            file_b: 修改后文档路径
            output_format: 输出格式 (markdown/html/text)

        Returns:
            对比结果字典
        """
        if not os.path.exists(file_a) or not os.path.exists(file_b):
            return {"success": False, "error": "文件不存在"}

        # 读取文档内容
        text_a = self._read_file(file_a)
        text_b = self._read_file(file_b)

        if not text_a or not text_b:
            return {"success": False, "error": "无法读取文件内容"}

        # 执行对比
        lines_a = text_a.splitlines()
        lines_b = text_b.splitlines()

        # 使用 difflib 计算差异
        diff = list(difflib.unified_diff(lines_a, lines_b, lineterm=""))

        # 分析变更
        changes = self._analyze_changes(lines_a, lines_b)

        # 生成摘要
        summary = self._generate_summary(changes)

        # 格式化输出
        if output_format == "markdown":
            diff_output = self._format_diff_markdown(lines_a, lines_b)
        elif output_format == "html":
            diff_output = self._format_diff_html(lines_a, lines_b)
        else:
            diff_output = "\n".join(diff)

        return {
            "success": True,
            "file_a": file_a,
            "file_b": file_b,
            "changes": changes,
            "summary": summary,
            "diff": diff_output,
            "timestamp": datetime.now().isoformat(),
        }

    def compare_versions(self, file_paths: List[str]) -> Dict[str, Any]:
        """
        对比多个版本的文档

        Args:
            file_paths: 文档路径列表（按时间顺序）

        Returns:
            版本对比结果
        """
        if len(file_paths) < 2:
            return {"success": False, "error": "至少需要两个文件"}

        versions = []
        for i in range(len(file_paths) - 1):
            result = self.compare_documents(file_paths[i], file_paths[i + 1])
            if result["success"]:
                versions.append(
                    {
                        "from": os.path.basename(file_paths[i]),
                        "to": os.path.basename(file_paths[i + 1]),
                        "summary": result["summary"],
                    }
                )

        return {
            "success": True,
            "total_versions": len(file_paths),
            "comparisons": len(versions),
            "versions": versions,
        }

    def _analyze_changes(
        self, lines_a: List[str], lines_b: List[str]
    ) -> Dict[str, Any]:
        """分析文档变更"""
        matcher = difflib.SequenceMatcher(None, lines_a, lines_b)

        additions = []
        deletions = []
        modifications = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "insert":
                additions.extend(lines_b[j1:j2])
            elif tag == "delete":
                deletions.extend(lines_a[i1:i2])
            elif tag == "replace":
                modifications.append({"old": lines_a[i1:i2], "new": lines_b[j1:j2]})

        # 计算相似度
        similarity = matcher.ratio()

        # 字符级统计
        text_a = "\n".join(lines_a)
        text_b = "\n".join(lines_b)

        char_diff = len(text_b) - len(text_a)
        line_diff = len(lines_b) - len(lines_a)

        return {
            "additions": {"count": len(additions), "lines": additions},
            "deletions": {"count": len(deletions), "lines": deletions},
            "modifications": {"count": len(modifications), "details": modifications},
            "similarity": round(similarity * 100, 2),
            "char_diff": char_diff,
            "line_diff": line_diff,
        }

    def _generate_summary(self, changes: Dict[str, Any]) -> str:
        """生成变更摘要"""
        lines = []

        similarity = changes["similarity"]
        if similarity >= 95:
            lines.append("✅ 文档变化很小")
        elif similarity >= 80:
            lines.append("📝 文档有适度修改")
        elif similarity >= 50:
            lines.append("⚠️ 文档有较大变化")
        else:
            lines.append("🔄 文档被大幅改写")

        lines.append(f"- 相似度: {similarity}%")

        if changes["additions"]["count"] > 0:
            lines.append(f"- 新增: {changes['additions']['count']} 行")

        if changes["deletions"]["count"] > 0:
            lines.append(f"- 删除: {changes['deletions']['count']} 行")

        if changes["modifications"]["count"] > 0:
            lines.append(f"- 修改: {changes['modifications']['count']} 处")

        if changes["char_diff"] > 0:
            lines.append(f"- 内容增加: +{changes['char_diff']} 字符")
        elif changes["char_diff"] < 0:
            lines.append(f"- 内容减少: {changes['char_diff']} 字符")

        return "\n".join(lines)

    def _format_diff_markdown(self, lines_a: List[str], lines_b: List[str]) -> str:
        """格式化为 Markdown 差异视图"""
        matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
        output = []

        output.append("# 文档对比\n")

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                # 相同内容，显示上下文（前后各2行）
                if i2 - i1 > 4:
                    # 只显示开始和结束部分
                    for line in lines_a[i1 : i1 + 2]:
                        output.append(f"  {line}")
                    output.append(f"  ... ({i2-i1-4} 行相同内容省略) ...")
                    for line in lines_a[i2 - 2 : i2]:
                        output.append(f"  {line}")
                else:
                    for line in lines_a[i1:i2]:
                        output.append(f"  {line}")

            elif tag == "delete":
                output.append("\n**删除:**")
                for line in lines_a[i1:i2]:
                    output.append(f"- ~~{line}~~")

            elif tag == "insert":
                output.append("\n**新增:**")
                for line in lines_b[j1:j2]:
                    output.append(f"+ **{line}**")

            elif tag == "replace":
                output.append("\n**修改:**")
                output.append("原文:")
                for line in lines_a[i1:i2]:
                    output.append(f"- ~~{line}~~")
                output.append("改为:")
                for line in lines_b[j1:j2]:
                    output.append(f"+ **{line}**")

        return "\n".join(output)

    def _format_diff_html(self, lines_a: List[str], lines_b: List[str]) -> str:
        """格式化为 HTML 差异视图"""
        differ = difflib.HtmlDiff()
        return differ.make_file(
            lines_a, lines_b, fromdesc="原始版本", todesc="修改版本"
        )

    def _read_file(self, file_path: str) -> str:
        """读取文件内容"""
        ext = os.path.splitext(file_path)[1].lower()

        try:
            if ext in [".txt", ".md"]:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()

            elif ext == ".docx":
                from docx import Document

                doc = Document(file_path)
                return "\n".join([p.text for p in doc.paragraphs])

            else:
                return ""
        except Exception as e:
            logger.info(f"读取文件失败: {e}")
            return ""

    def generate_change_log(self, comparisons: List[Dict[str, Any]], output_file: str):
        """生成变更日志文件"""
        lines = []
        lines.append("# 文档变更日志")
        lines.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        for i, comp in enumerate(comparisons, 1):
            lines.append(f"## 版本 {i}: {comp['file_a']} → {comp['file_b']}")
            lines.append(f"\n{comp['summary']}\n")

            if comp["changes"]["additions"]["count"] > 0:
                lines.append("### 新增内容")
                for line in comp["changes"]["additions"]["lines"][:10]:  # 限制显示数量
                    lines.append(f"+ {line}")
                if comp["changes"]["additions"]["count"] > 10:
                    lines.append(
                        f"... 还有 {comp['changes']['additions']['count'] - 10} 行"
                    )
                lines.append("")

            if comp["changes"]["deletions"]["count"] > 0:
                lines.append("### 删除内容")
                for line in comp["changes"]["deletions"]["lines"][:10]:
                    lines.append(f"- {line}")
                if comp["changes"]["deletions"]["count"] > 10:
                    lines.append(
                        f"... 还有 {comp['changes']['deletions']['count'] - 10} 行"
                    )
                lines.append("")

        # 保存文件
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return output_file


if __name__ == "__main__":
    comparator = DocumentComparator()

    logger.info("=" * 60)
    logger.info("文档对比与总结器测试")
    logger.info("=" * 60)

    # 创建测试文档
    text_v1 = """# 产品介绍

这是我们的新产品。

## 功能特性

- 功能A
- 功能B
- 功能C

## 技术规格

性能优异。
"""

    text_v2 = """# 产品介绍

这是我们的新一代产品。

## 核心功能

- 功能A（增强版）
- 功能B
- 功能C
- 功能D（新增）

## 技术规格

性能优异，超越前代。

## 应用场景

适用于多种场景。
"""

    # 创建测试文件
    file_v1 = "test_doc_v1.md"
    file_v2 = "test_doc_v2.md"

    with open(file_v1, "w", encoding="utf-8") as f:
        f.write(text_v1)

    with open(file_v2, "w", encoding="utf-8") as f:
        f.write(text_v2)

    # 执行对比
    result = comparator.compare_documents(file_v1, file_v2, output_format="markdown")

    if result["success"]:
        logger.info("\n变更摘要:")
        logger.info(result["summary"])

        logger.info("\n详细差异:")
        logger.info(result["diff"])

    # 清理测试文件
    os.remove(file_v1)
    os.remove(file_v2)

    logger.info("\n✅ 文档对比器就绪")

"""
FileConverterPlugin — 文件格式转换 Agent 工具

注册两个 AI 可调用工具:
  convert_file       : 将指定文件转换为目标格式
  list_conversions   : 查询某格式支持的转换目标列表
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin


class FileConverterPlugin(AgentPlugin):

    @property
    def name(self) -> str:
        return "FileConverter"

    @property
    def description(self) -> str:
        return "Convert files between formats (PDF, DOCX, TXT, MD, XLSX, CSV, PPTX, images…)."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "convert_file",
                "func": self.convert_file,
                "description": (
                    "Convert a file from one format to another. "
                    "Supports: DOCX/DOC↔PDF/TXT/MD, PDF↔DOCX/TXT, "
                    "XLSX/XLS↔CSV, PPTX→TXT/PDF, image formats (PNG/JPG/WEBP/BMP). "
                    "Returns the output file path on success."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "Absolute path to the source file to convert.",
                        },
                        "target_format": {
                            "type": "STRING",
                            "description": (
                                "Target format. Accepts extensions like 'pdf', 'docx', 'txt', "
                                "'md', 'xlsx', 'csv', 'png', 'jpg', or aliases like "
                                "'word', 'excel', 'markdown'."
                            ),
                        },
                        "output_path": {
                            "type": "STRING",
                            "description": (
                                "Optional full output file path. "
                                "If omitted, the converted file is saved next to the source."
                            ),
                        },
                    },
                    "required": ["file_path", "target_format"],
                },
            },
            {
                "name": "list_conversions",
                "func": self.list_conversions,
                "description": (
                    "List the formats that a given file extension can be converted to. "
                    "Useful before calling convert_file."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_ext": {
                            "type": "STRING",
                            "description": "File extension to query, e.g. '.pdf' or 'pdf'.",
                        },
                    },
                    "required": ["file_ext"],
                },
            },
        ]

    # ── tool implementations ──────────────────────────────────────────────────

    @staticmethod
    def convert_file(
        file_path: str,
        target_format: str,
        output_path: str = "",
    ) -> str:
        """Convert file_path to target_format and return a human-readable result."""
        from web.file_converter import convert

        result = convert(
            source_path=file_path,
            target_format=target_format,
            output_path=output_path or None,
        )

        lines = [result["message"]]
        if result.get("warning"):
            lines.append(result["warning"])
        if result.get("success"):
            lines.append(f"📁 输出文件: {result['output_path']}")
        else:
            lines.append(f"错误详情: {result.get('error', '')}")
        return "\n".join(lines)

    @staticmethod
    def list_conversions(file_ext: str) -> str:
        """Return the supported target formats for the given file extension."""
        from web.file_converter import get_supported_conversions

        ext = file_ext.strip().lower()
        if not ext.startswith("."):
            ext = f".{ext}"

        matrix = get_supported_conversions()
        targets = matrix.get(ext)
        if targets is None:
            supported = ", ".join(sorted(matrix.keys()))
            return (
                f"不支持的格式: {ext}\n"
                f"支持的来源格式: {supported}"
            )
        return (
            f"{ext} 可转换为: {', '.join(targets)}\n"
            f"调用 convert_file 并传入 target_format 即可完成转换。"
        )

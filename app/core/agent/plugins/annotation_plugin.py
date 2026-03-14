# -*- coding: utf-8 -*-
"""
Koto  ─  AnnotationPlugin
===========================
将现有的文档标注能力（document_batch_annotator_v2 / DocumentFeedbackSystem）
封装为 ToolRegistry 插件，使 UnifiedAgent 可以通过工具调用触发标注功能。

提供两个工具：
  annotate_document       规则引擎批量标注（无需 API Key，速度快）
  read_docx_paragraphs    读取 .docx 文档段落（后续标注的准备步骤）

这样 Skill 的 executor_tools 字段就可以声明 ["annotate_document", "read_docx_paragraphs"]，
让 UnifiedAgent 在执行标注类 Skill 时只暴露这两个工具，减少无关 context 干扰。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)


class AnnotationPlugin(AgentPlugin):
    """文档标注工具插件。"""

    @property
    def name(self) -> str:
        return "Annotation"

    @property
    def description(self) -> str:
        return "Document annotation tools: batch annotation via rule engine and DOCX reading."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "annotate_document",
                "func": self.annotate_document,
                "description": (
                    "对本地 Word (.docx) 文档执行批量标注/修改，生成 _revised 副本。"
                    "适用于翻译润色、学术批注、商务文稿规范化等场景。"
                    "file_path: 文档绝对路径；requirement: 用户标注需求描述。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Word 文档的绝对路径（.docx）",
                        },
                        "requirement": {
                            "type": "string",
                            "description": "标注需求，例如「优化翻译腔，使语言更自然」",
                            "default": "优化表达，使语言更自然流畅",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "read_docx_paragraphs",
                "func": self.read_docx_paragraphs,
                "description": (
                    "读取 .docx 文档的段落内容，返回段落列表。"
                    "用于在标注前了解文档结构和内容。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Word 文档的绝对路径（.docx）",
                        },
                        "max_paragraphs": {
                            "type": "integer",
                            "description": "最多返回多少段落（默认 50）",
                            "default": 50,
                        },
                    },
                    "required": ["file_path"],
                },
            },
        ]

    # ── Tool implementations ──────────────────────────────────────────────────

    def annotate_document(
        self,
        file_path: str,
        requirement: str = "优化表达，使语言更自然流畅",
    ) -> str:
        """
        调用 ImprovedBatchAnnotator 对文档执行批量标注。
        收集流式进度事件并返回摘要文本，供 agent 继续推理。
        """
        import os

        if not os.path.isabs(file_path):
            return f"错误：file_path 必须是绝对路径，当前值: {file_path!r}"

        # Sandbox: only allow access to files within known safe directories
        resolved = os.path.realpath(file_path)
        safe_dirs = [
            os.path.realpath(os.path.join(os.getcwd(), "workspace")),
            os.path.realpath(os.path.join(os.getcwd(), "uploads")),
            os.path.realpath(os.path.join(os.getcwd(), "dist")),
        ]
        if not any(resolved.startswith(d + os.sep) for d in safe_dirs):
            return f"错误：文件路径不在允许的目录范围内: {file_path}"

        if not os.path.exists(file_path):
            return f"错误：文件不存在: {file_path}"

        if not file_path.lower().endswith(".docx"):
            return f"错误：当前只支持 .docx 格式，收到: {file_path}"

        try:
            from web.document_batch_annotator_v2 import annotate_large_document

            events = []
            output_file = None
            total_edits = 0

            # 消费 SSE 生成器，收集关键事件
            for raw_sse in annotate_large_document(
                file_path=file_path,
                user_requirement=requirement,
            ):
                line = raw_sse.strip()
                if not line.startswith("data:"):
                    continue
                payload_str = line[5:].strip()
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue

                evt_type = payload.get("type", "")
                if evt_type == "complete":
                    output_file = payload.get("output_file")
                    total_edits = payload.get("total_edits", 0)
                elif evt_type == "error":
                    return f"标注失败: {payload.get('message', '未知错误')}"
                elif evt_type == "progress" and payload.get("progress", 0) % 20 == 0:
                    events.append(payload.get("message", ""))

            if output_file:
                return (
                    f"✅ 文档标注完成\n"
                    f"- 原文件: {os.path.basename(file_path)}\n"
                    f"- 修订副本: {os.path.basename(output_file)}\n"
                    f"- 修改处数: {total_edits}\n"
                    f"- 副本路径: {output_file}"
                )
            return f"标注流程结束但未生成输出文件（可能 0 处修改）。进度: {events}"

        except ImportError as e:
            return f"标注模块不可用: {e}"
        except Exception as e:
            logger.exception("[AnnotationPlugin] annotate_document error")
            return f"标注过程异常: {e}"

    def read_docx_paragraphs(
        self,
        file_path: str,
        max_paragraphs: int = 50,
    ) -> str:
        """读取 .docx 段落内容，返回格式化文本。"""
        import os

        if not os.path.exists(file_path):
            return f"错误：文件不存在: {file_path}"

        try:
            from web.document_reader import DocumentReader

            reader = DocumentReader()
            result = reader.read_document(file_path)

            if not result.get("success"):
                return f"读取失败: {result.get('error', '未知错误')}"

            paragraphs = result.get("paragraphs", [])[:max_paragraphs]
            total = result.get("total_paragraphs", len(paragraphs))

            lines = [f"【文档：{os.path.basename(file_path)}，共 {total} 段，显示前 {len(paragraphs)} 段】\n"]
            for i, p in enumerate(paragraphs, 1):
                text = p.get("text", "").strip()
                if text:
                    lines.append(f"[{i}] {text}")

            return "\n".join(lines)

        except ImportError:
            # 降级：用 python-docx 直接读
            try:
                from docx import Document  # type: ignore

                doc = Document(file_path)
                paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()][:max_paragraphs]
                lines = [f"【{os.path.basename(file_path)}，显示 {len(paras)} 段】\n"]
                lines += [f"[{i}] {t}" for i, t in enumerate(paras, 1)]
                return "\n".join(lines)
            except Exception as e2:
                return f"读取文档失败: {e2}"
        except Exception as e:
            logger.exception("[AnnotationPlugin] read_docx_paragraphs error")
            return f"读取异常: {e}"

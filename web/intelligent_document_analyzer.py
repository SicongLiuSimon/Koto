#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
智能文档分析引擎 - Intelligent Document Analyzer
能够理解用户意图、分析文档结构、分解任务、生成高质量回复
"""

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from docx import Document
from docx.shared import RGBColor


class IntelligentDocumentAnalyzer:
    """智能文档分析引擎"""

    # 文档类型检测
    ACADEMIC_KEYWORDS = [
        "摘要",
        "abstract",
        "引言",
        "结论",
        "参考文献",
        "关键词",
        "论文",
        "研究",
    ]
    REPORT_KEYWORDS = ["报告", "总结", "分析", "结果", "建议", "结论"]
    ARTICLE_KEYWORDS = ["标题", "目录", "章节", "段落"]

    # 任务类型分解
    TASK_PATTERNS = {
        "write_abstract": {
            "keywords": [
                "写.*摘要",
                "生成.*摘要",
                "摘要.*写",
                "摘要.*生成",
                "abstract",
            ],
            "requirements": [
                "研究背景",
                "研究方法",
                "研究结果",
                "研究结论",
                "300.*400字",
                "300-400",
            ],
            "output_type": "generate",  # 生成新内容，不修改原文
        },
        "revise_intro": {
            "keywords": [
                "改.*引言",
                "改进.*引言",
                "重写.*引言",
                "优化.*引言",
                "引言.*改",
                "引言.*不符合",
            ],
            "requirements": ["架构", "主体", "文章", "对应", "符合"],
            "output_type": "generate",  # 生成新内容
        },
        "revise_conclusion": {
            "keywords": [
                "改.*结论",
                "改进.*结论",
                "重写.*结论",
                "优化.*结论",
                "结论.*不满意",
                "overcap",
            ],
            "requirements": ["整篇", "全文", "总结", "概括"],
            "output_type": "generate",  # 生成新内容
        },
        "general_revision": {
            "keywords": ["改", "改进", "优化", "修改", "润色", "提升"],
            "requirements": [],
            "output_type": "generate",  # 默认为生成新内容
        },
        "analysis": {
            "keywords": ["分析", "总结", "梳理", "概述", "要点"],
            "requirements": [],
            "output_type": "analysis",  # 返回分析结果
        },
    }

    def __init__(self, llm_client):
        """
        初始化文档分析引擎

        Args:
            llm_client: LLM客户端（KotoBrain或Gemini client）
        """
        self.llm_client = llm_client

    def analyze_request(
        self, user_input: str, document_structure: Dict
    ) -> Dict[str, Any]:
        """
        分析用户请求，理解意图并分解任务

        Args:
            user_input: 用户输入
            document_structure: 文档结构（来自DocumentReader）

        Returns:
            {
                'tasks': [{'type': str, 'description': str, 'target_sections': [str]}],
                'document_type': 'academic' | 'report' | 'article' | 'general',
                'is_multi_task': bool,
                'confidence': float
            }
        """
        result = {
            "tasks": [],
            "document_type": "general",
            "is_multi_task": False,
            "confidence": 0.5,
        }

        # 检测文档类型
        doc_content = " ".join(
            [p.get("text", "") for p in document_structure.get("paragraphs", [])]
        )
        result["document_type"] = self._detect_document_type(doc_content)

        # 分解任务
        user_lower = user_input.lower()
        detected_tasks = []

        for task_name, pattern in self.TASK_PATTERNS.items():
            # 检查关键词匹配
            keyword_match = any(re.search(kw, user_lower) for kw in pattern["keywords"])
            if keyword_match:
                # 检查需求匹配
                req_match_count = sum(
                    1 for req in pattern["requirements"] if re.search(req, user_lower)
                )
                confidence = 0.7 + (0.1 * min(req_match_count, 3))

                detected_tasks.append(
                    {
                        "type": task_name,
                        "description": self._get_task_description(
                            task_name, user_input
                        ),
                        "confidence": confidence,
                        "target_sections": self._identify_target_sections(
                            task_name, document_structure
                        ),
                    }
                )

        # 如果没有检测到任何任务，默认为general_revision
        if not detected_tasks:
            detected_tasks.append(
                {
                    "type": "analysis",
                    "description": "分析并改进文档",
                    "confidence": 0.5,
                    "target_sections": [],
                }
            )

        result["tasks"] = detected_tasks
        result["is_multi_task"] = len(detected_tasks) > 1
        result["confidence"] = max(t["confidence"] for t in detected_tasks)

        return result

    def _detect_document_type(self, content: str) -> str:
        """检测文档类型"""
        content_lower = content.lower()

        academic_score = sum(1 for kw in self.ACADEMIC_KEYWORDS if kw in content_lower)
        report_score = sum(1 for kw in self.REPORT_KEYWORDS if kw in content_lower)

        if academic_score >= 3:
            return "academic"
        elif report_score >= 2:
            return "report"
        else:
            return "article"

    def _get_task_description(self, task_type: str, user_input: str) -> str:
        """获取任务描述"""
        descriptions = {
            "write_abstract": f"根据要求生成论文摘要：{user_input}",
            "revise_intro": f"改进引言：{user_input}",
            "revise_conclusion": f"改进结论：{user_input}",
            "general_revision": f"文档优化：{user_input}",
            "analysis": f"文档分析：{user_input}",
        }
        return descriptions.get(task_type, user_input)

    def _identify_target_sections(
        self, task_type: str, doc_structure: Dict
    ) -> List[str]:
        """识别目标段落/章节"""
        paragraphs = doc_structure.get("paragraphs", [])
        target_sections = []

        # 根据任务类型定位目标段落
        if task_type == "write_abstract":
            # 查找摘要位置（通常在文档开头）
            for idx, para in enumerate(paragraphs[:10]):
                text = para.get("text", "").lower()
                if "abstract" in text or "摘要" in text:
                    target_sections.append(f"paragraph_{idx}")
                    break
            if not target_sections:
                target_sections.append("paragraph_0")  # 默认第一段

        elif task_type == "revise_intro":
            # 查找引言
            for idx, para in enumerate(paragraphs[:20]):
                text = para.get("text", "").lower()
                if (
                    "引言" in text
                    or "introduction" in text
                    or para.get("type") == "heading"
                    and para.get("level") == 1
                ):
                    # 找到引言标题后，收集后续段落直到下一个标题
                    target_sections.append(f"paragraph_{idx}")
                    for offset in range(1, 10):
                        if idx + offset < len(paragraphs):
                            next_para = paragraphs[idx + offset]
                            if next_para.get("type") == "heading":
                                break
                            target_sections.append(f"paragraph_{idx + offset}")
                    break

        elif task_type == "revise_conclusion":
            # 查找结论（通常在文档末尾）
            for idx in range(len(paragraphs) - 1, max(0, len(paragraphs) - 30), -1):
                text = paragraphs[idx].get("text", "").lower()
                if "结论" in text or "结语" in text or "conclusion" in text:
                    target_sections.append(f"paragraph_{idx}")
                    # 收集结论段落
                    for offset in range(1, 5):
                        if idx + offset < len(paragraphs):
                            target_sections.append(f"paragraph_{idx + offset}")
                    break

        return target_sections

    def generate_specialized_prompt(
        self, task: Dict, doc_structure: Dict, user_input: str
    ) -> str:
        """
        根据任务类型生成专门的提示词

        Args:
            task: 任务信息
            doc_structure: 文档结构
            user_input: 用户原始输入

        Returns:
            专门的提示词
        """
        task_type = task["type"]

        # 提取文档内容
        paragraphs = doc_structure.get("paragraphs", [])
        doc_text = "\n\n".join([p.get("text", "") for p in paragraphs])

        # 提取文档结构概览
        structure_overview = self._get_structure_overview(doc_structure)

        if task_type == "write_abstract":
            return self._generate_abstract_prompt(
                doc_text, structure_overview, user_input
            )
        elif task_type == "revise_intro":
            return self._generate_intro_prompt(doc_text, structure_overview, user_input)
        elif task_type == "revise_conclusion":
            return self._generate_conclusion_prompt(
                doc_text, structure_overview, user_input
            )
        elif task_type == "general_revision":
            return self._generate_revision_prompt(
                doc_text, structure_overview, user_input
            )
        else:  # analysis
            return self._generate_analysis_prompt(
                doc_text, structure_overview, user_input
            )

    def _get_structure_overview(self, doc_structure: Dict) -> str:
        """获取文档结构概览"""
        paragraphs = doc_structure.get("paragraphs", [])
        headings = [
            (idx, p["text"], p.get("level", 1))
            for idx, p in enumerate(paragraphs)
            if p.get("type") == "heading"
        ]

        structure_lines = ["文档结构概览:"]
        for idx, text, level in headings:
            indent = "  " * (level - 1)
            structure_lines.append(f"{indent}- {text} (段落{idx})")

        return "\n".join(structure_lines)

    def _generate_abstract_prompt(
        self, doc_text: str, structure: str, user_req: str
    ) -> str:
        """生成摘要任务的专用提示词"""
        return f"""你是一位专业的学术论文摘要撰写专家。请根据以下完整论文内容，撰写一篇高质量的中文学术摘要。

用户要求：
{user_req}

文档结构：
{structure}

完整论文内容：
{doc_text}

请按照以下标准学术摘要模板撰写（控制在300-400字）：

1. 研究背景与目的（2-3句）：
   - 简要介绍研究领域的背景
   - 阐述研究的目的和意义

2. 研究方法（2-3句）：
   - 描述研究采用的方法论
   - 说明分析方法和技术手段

3. 研究结果（3-4句）：
   - 概括研究的主要发现
   - 突出创新点和核心贡献

4. 研究结论（2-3句）：
   - 总结研究的主要贡献
   - 指出研究的局限性和未来研究方向

注意事项：
- 摘要应该自成一体，不依赖正文即可理解
- 使用第三人称和客观语气
- 避免使用"本文认为"等主观表达
- 突出理论创新和实践价值
- 严格控制字数在300-400字之间

请直接输出摘要内容，无需其他说明。"""

    def _generate_intro_prompt(
        self, doc_text: str, structure: str, user_req: str
    ) -> str:
        """生成引言改进任务的专用提示词"""
        # 提取当前引言
        lines = doc_text.split("\n")
        intro_start = -1
        intro_end = -1

        for idx, line in enumerate(lines):
            if "引言" in line or "Introduction" in line.lower():
                intro_start = idx
            elif intro_start != -1 and (
                "二 " in line or "第二" in line or "2." in line or "2 " in line[:3]
            ):
                intro_end = idx
                break

        current_intro = (
            "\n".join(lines[intro_start:intro_end])
            if intro_start != -1 and intro_end != -1
            else "未找到明确的引言部分"
        )

        return f"""你是一位专业的学术论文编辑。请根据论文的整体架构和内容，改进引言部分，使其与文章主体结构高度对应。

用户要求：
{user_req}

文档整体结构：
{structure}

当前引言内容：
{current_intro}

完整论文内容（供参考）：
{doc_text[:5000]}...  [文档较长，已截取前5000字]

改进要求：
1. **结构对应**：引言必须清晰地对应论文各章节
   - 为每一章（第二、三、四、五章等）提供明确的导引
   - 说明各章节之间的逻辑递进关系

2. **层次递进**：采用"问题提出 → 层次展开 → 总体归纳"的结构
   - 第一段：提出核心问题
   - 中间段：分层次介绍各章内容（第一层次/第二层次/第三层次...）
   - 最后段：总结全文论证路径

3. **逻辑清晰**：每一层次的说明应包含：
   - 本章要解决什么问题
   - 采用什么方法/理论
   - 得出什么结论

4. **承上启下**：说明各章节之间的因果关联

请直接输出改进后的引言内容，无需其他说明。"""

    def _generate_conclusion_prompt(
        self, doc_text: str, structure: str, user_req: str
    ) -> str:
        """生成结论改进任务的专用提示词"""
        # 提取当前结论
        lines = doc_text.split("\n")
        conclusion_start = -1

        for idx in range(len(lines) - 1, max(0, len(lines) - 100), -1):
            if (
                "结论" in lines[idx]
                or "结语" in lines[idx]
                or "Conclusion" in lines[idx].lower()
            ):
                conclusion_start = idx
                break

        current_conclusion = (
            "\n".join(lines[conclusion_start:])
            if conclusion_start != -1
            else "未找到明确的结论部分"
        )

        return f"""你是一位专业的学术论文编辑。请根据整篇论文的内容，改进结论部分，使其能够"overcap"（全面总结）整篇文章。

用户要求：
{user_req}

文档整体结构：
{structure}

当前结论内容：
{current_conclusion}

完整论文内容：
{doc_text}

改进要求：
1. **逐章概括**（核心要求）：
   - 必须系统地回顾每一章的主要内容和贡献
   - 第二章：说明了什么问题/采用了什么方法
   - 第三章：揭示了什么根源/提供了什么理论
   - 第四章：论证了什么观点/引入了什么概念
   - 第五章：提出了什么解决方案
   
2. **理论综合**：
   - 阐明各章节之间的逻辑递进关系
   - 说明理论框架是如何一步步建立的
   - 指出关键转折点和枢纽性论证

3. **贡献明确**：
   - 概括研究的核心贡献
   - 指出理论创新点
   - 说明实践意义

4. **局限与展望**：
   - 坦诚指出研究的局限性
   - 提出未来研究方向
   - 保持学术谦逊

请直接输出改进后的结论内容，要求能够让读者仅通过结论就能理解全文的核心论证路径，无需其他说明。"""

    def _generate_revision_prompt(
        self, doc_text: str, structure: str, user_req: str
    ) -> str:
        """生成一般性修改任务的专用提示词"""
        return f"""你是一位专业的文档编辑专家。请根据用户的要求对文档进行改进。

用户要求：
{user_req}

文档结构：
{structure}

文档内容：
{doc_text}

改进原则：
1. 忠实用户意图，精准理解要求
2. 保持文档原有风格和学术水准
3. 改进应该有针对性，不要泛泛而谈
4. 如果是学术文档，保持学术规范
5. 如果涉及结构调整，说明调整理由

请直接输出改进建议或改进后的内容（根据用户要求判断），无需其他说明。"""

    def _generate_analysis_prompt(
        self, doc_text: str, structure: str, user_req: str
    ) -> str:
        """生成分析任务的专用提示词"""
        return f"""你是一位专业的文档分析专家。请根据用户的要求对文档进行深入分析。

用户要求：
{user_req}

文档结构：
{structure}

文档内容：
{doc_text}

分析要求：
1. 结构分析：分析文档的整体结构和逻辑
2. 内容分析：提炼核心观点和关键论证
3. 质量评估：评估文档的完整性和逻辑严密性
4. 改进建议：提出具体的改进方向

请提供系统性的分析结果。"""

    async def process_document_intelligent_streaming(
        self, doc_path: str, user_input: str, session_name: str = None
    ) -> AsyncGenerator[Dict, None]:
        """
        智能流式处理文档分析请求
        根据用户意图，可以：
        1. 生成新文本（摘要/引言/结论）并直接返回
        2. 修改文档并标红
        3. 返回分析结果

        Args:
            doc_path: 文档路径
            user_input: 用户输入
            session_name: 会话名称

        Yields:
            进度事件字典
        """
        from web.document_reader import DocumentReader

        yield {"stage": "reading", "progress": 10, "message": "📖 正在读取文档结构..."}

        # 读取文档结构
        doc_structure = DocumentReader.read_word(doc_path)
        if not doc_structure.get("success"):
            yield {
                "stage": "error",
                "message": f"读取文档失败: {doc_structure.get('error')}",
            }
            return

        yield {
            "stage": "analyzing",
            "progress": 20,
            "message": "🔍 分析用户需求和意图...",
        }

        # 分析用户请求
        request_analysis = self.analyze_request(user_input, doc_structure)
        tasks = request_analysis["tasks"]
        output_type = self._determine_output_type(tasks)

        yield {
            "stage": "planning",
            "progress": 30,
            "message": f"📋 识别到 {len(tasks)} 个任务，输出模式: {output_type}",
            "detail": json.dumps(tasks, ensure_ascii=False),
        }

        # 处理每个任务
        all_results = {}
        generated_contents = []

        for task_idx, task in enumerate(tasks):
            progress_base = 30 + (task_idx * 40 // len(tasks))

            yield {
                "stage": "generating",
                "progress": progress_base,
                "message": f'✍️ 正在处理: {task["description"]}',
            }

            # 生成专用提示词
            specialized_prompt = self.generate_specialized_prompt(
                task, doc_structure, user_input
            )

            # 调用LLM生成内容
            response = await self._call_llm(specialized_prompt)

            all_results[task["type"]] = {"task": task, "generated_content": response}
            generated_contents.append(
                {
                    "task_type": task["type"],
                    "task_description": task["description"],
                    "content": response,
                }
            )

            yield {
                "stage": "task_complete",
                "progress": progress_base + 35 // len(tasks),
                "message": f'✅ 完成: {task["type"]}',
                "detail": response[:200] + "..." if len(response) > 200 else response,
            }

        yield {
            "stage": "processing",
            "progress": 80,
            "message": f"📝 处理输出 (模式: {output_type})...",
        }

        # 根据输出类型处理结果
        if output_type == "generate":
            # 生成模式：直接返回生成的文本
            yield {
                "stage": "complete",
                "progress": 100,
                "message": "✅ 文本生成完成",
                "result": {
                    "output_type": "generated_texts",
                    "tasks_completed": len(tasks),
                    "generated_contents": generated_contents,
                },
            }

        elif output_type == "modify":
            # 修改模式：应用到文档并标红
            output_path = await self._apply_revisions_with_red_marking(
                doc_path,
                all_results,
                doc_structure,
                os.path.join(os.path.dirname(__file__), "..", "workspace", "documents"),
            )

            yield {
                "stage": "complete",
                "progress": 100,
                "message": "✅ 文档修订完成",
                "result": {
                    "output_type": "modified_document",
                    "output_file": output_path,
                    "tasks_completed": len(tasks),
                    "revisions": list(all_results.keys()),
                },
            }

        else:  # analysis
            # 分析模式：返回分析结果
            yield {
                "stage": "complete",
                "progress": 100,
                "message": "✅ 分析完成",
                "result": {
                    "output_type": "analysis_results",
                    "tasks_completed": len(tasks),
                    "analysis": generated_contents,
                },
            }

    def _determine_output_type(self, tasks: List[Dict]) -> str:
        """
        根据任务类型确定输出方式

        Args:
            tasks: 任务列表

        Returns:
            'generate' - 生成新文本并直接返回
            'modify' - 修改文档并标红
            'analysis' - 返回分析结果
        """
        if not tasks:
            return "analysis"

        task_types = [t["type"] for t in tasks]

        # 包含"写"或"改"的任务 -> 生成模式
        generate_types = {
            "write_abstract",
            "revise_intro",
            "revise_conclusion",
            "general_revision",
        }
        if any(t in generate_types for t in task_types):
            return "generate"

        # 分析类任务 -> 分析模式
        if "analysis" in task_types:
            return "analysis"

        # 默认生成
        return "generate"

    def _replace_paragraph_with_diff(self, paragraph, new_text: str):
        """用diff对比方式替换段落并标红修改部分"""
        old_text = paragraph.text

        # 按句子分割
        old_sentences = self._split_sentences(old_text)
        new_sentences = self._split_sentences(new_text)

        # 使用difflib对比
        matcher = difflib.SequenceMatcher(None, old_sentences, new_sentences)

        # 清空段落
        paragraph.clear()

        # 根据diff结果重建段落
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                # 保留的句子 - 黑色
                for sent in new_sentences[j1:j2]:
                    run = paragraph.add_run(sent)
                    run.font.color.rgb = RGBColor(0, 0, 0)
            elif tag in ("replace", "insert"):
                # 新增/修改的句子 - 红色
                for sent in new_sentences[j1:j2]:
                    run = paragraph.add_run(sent)
                    run.font.color.rgb = RGBColor(255, 0, 0)

    def _split_sentences(self, text: str) -> List[str]:
        """按句子分割文本"""
        # 按中文标点分割
        parts = re.split(r"((?:[\u3002\uff01\uff1f\uff1b]|\.(?:\s|$)))", text)
        sentences = []
        i = 0
        while i < len(parts):
            s = parts[i]
            if i + 1 < len(parts):
                s += parts[i + 1]
                i += 2
            else:
                i += 1
            s = s.strip()
            if s:
                sentences.append(s)
        return sentences


def create_intelligent_analyzer(llm_client) -> IntelligentDocumentAnalyzer:
    """工厂函数：创建智能文档分析器实例"""
    return IntelligentDocumentAnalyzer(llm_client)

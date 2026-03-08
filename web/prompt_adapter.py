#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Prompt Adapter - 将用户请求解析为结构化 Markdown
目标：让LLM更易理解、减少歧义、提升任务完成度
"""

import re
from typing import List, Dict, Optional, Callable


class PromptAdapter:
    """任务解析与结构化Markdown转换"""

    SECTION_ORDER = [
        "目标",
        "背景",
        "输入",
        "输出",
        "约束",
        "注意事项",
        "步骤",
        "示例",
    ]

    TASK_HINT_MAP = {
        "FILE_GEN": "文档/文件生成",
        "CODER": "代码编写",
        "RESEARCH": "研究分析",
        "WEB_SEARCH": "联网搜索",
        "CHAT": "对话与解释",
        "SYSTEM": "系统操作",
        "FILE_OP": "文件操作",
        "PAINTER": "图像生成/编辑",
        "VISION": "图像理解",
    }

    @staticmethod
    def _has_markdown(text: str) -> bool:
        return any(mark in text for mark in ["## ", "### ", "- ", "**", "```", "| "])

    @staticmethod
    def _extract_candidates(user_input: str) -> Dict[str, List[str]]:
        """基于规则抽取候选信息"""
        text = user_input.strip()
        candidates = {
            "目标": [],
            "背景": [],
            "输入": [],
            "输出": [],
            "约束": [],
            "注意事项": [],
            "步骤": [],
            "示例": [],
        }

        # 规则：识别常见的“要/需要/生成/做成”等目标表达
        goal_patterns = [
            r"需要(.*?)。",
            r"想要(.*?)。",
            r"帮我(.*?)。",
            r"请(.*?)。",
            r"做成(.*?)(。|$)",
            r"生成(.*?)(。|$)",
            r"制作(.*?)(。|$)",
        ]
        for pat in goal_patterns:
            match = re.search(pat, text)
            if match and match.group(1):
                candidates["目标"].append(match.group(1).strip())

        # 规则：输入/输出关键词
        if any(k in text for k in ["输入", "原始", "源", "数据", "给定", "已有"]):
            candidates["输入"].append(text)
        if any(k in text for k in ["输出", "生成", "得到", "结果", "导出", "保存"]):
            candidates["输出"].append(text)

        # 规则：约束/要求
        if any(k in text for k in ["要求", "必须", "不要", "限制", "格式", "风格", "规范"]):
            candidates["约束"].append(text)

        # 规则：注意事项
        if any(k in text for k in ["注意", "避免", "确保", "请确认"]):
            candidates["注意事项"].append(text)

        return candidates

    @staticmethod
    def _summarize_history(history: Optional[List[dict]], max_turns: int = 4) -> str:
        """
        [已禁用] 为了防止历史上下文干扰当前任务的独立解析，暂时禁用历史摘要注入。
        解决问题：当用户提出新问题时，旧的历史（特别是长篇大论的回答）会误导模型，使其重复回答旧问题。
        """
        return ""
        # if not history:
        #     return ""
        # last_turns = []
        # for turn in history[-max_turns:]:
        #     role = turn.get("role", "")
        #     parts = " ".join(turn.get("parts", []))
        #     if parts:
        #         last_turns.append(f"- {role}: {parts[:120]}")
        # if not last_turns:
        #     return ""
        # return "\n".join(last_turns)

    @staticmethod
    def _build_markdown(
        task_type: str,
        user_input: str,
        candidates: Dict[str, List[str]],
        history_summary: str = "",
    ) -> str:
        task_hint = PromptAdapter.TASK_HINT_MAP.get(task_type, "通用任务")

        lines = [f"# 任务解析", "", f"## 任务类型", f"- {task_hint}", "", "## 用户原始请求", user_input]

        if history_summary:
            lines += ["", "## 上下文摘要", history_summary]

        for section in PromptAdapter.SECTION_ORDER:
            items = candidates.get(section, [])
            if not items:
                continue
            lines += ["", f"## {section}"]
            for item in items:
                lines.append(f"- {item}")

        return "\n".join(lines)

    @staticmethod
    def adapt(
        user_input: str,
        task_type: str,
        history: Optional[List[dict]] = None,
        model_generate: Optional[Callable[[str], str]] = None,
    ) -> str:
        """将用户请求转为结构化Markdown；如提供小模型则二次润色"""
        if not user_input or len(user_input.strip()) < 20:
            return user_input
        # CHAT 和 WEB_SEARCH 是直接问答型任务，不需要结构化包装
        if task_type in ("CHAT", "WEB_SEARCH"):
            return user_input
        if PromptAdapter._has_markdown(user_input):
            return user_input

        candidates = PromptAdapter._extract_candidates(user_input)
        history_summary = PromptAdapter._summarize_history(history)
        base_md = PromptAdapter._build_markdown(task_type, user_input, candidates, history_summary)

        if not model_generate:
            return base_md

        refine_prompt = (
            "你是任务解析器。请将以下内容优化为结构化Markdown，字段要更清晰、无歧义。\n"
            "要求：\n"
            "1) 不新增用户未提到的需求。\n"
            "2) 保持原意并补齐必要字段。\n"
            "3) 仅输出Markdown，不要解释。\n\n"
            f"输入Markdown:\n{base_md}\n"
        )

        try:
            refined = model_generate(refine_prompt)
            refined = (refined or "").strip()
            return refined if refined else base_md
        except Exception:
            return base_md

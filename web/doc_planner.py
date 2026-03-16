#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
doc_planner.py — 复杂文件生成的规划层（Planning Layer）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
负责在正式生成内容前分析用户意图，输出结构化的生成计划:

  DocumentPlan
    ├── doc_type        : word / excel / ppt / pdf
    ├── title           : 文档标题
    ├── target_audience : 读者定位
    ├── tone            : 语气（正式/轻松/技术/营销）
    ├── sections        : List[SectionPlan]  ← 各节规划
    │     ├── heading       : 节标题
    │     ├── section_type  : text / table / chart / comparison / timeline / ...
    │     ├── purpose       : 此节目标
    │     ├── key_points    : 需覆盖的关键信息
    │     └── rough_length  : short/medium/long
    ├── table_schema    : 若有表格，列名+数据类型
    ├── visual_hints    : 配图/图表建议
    └── generation_notes: 整体生成注意事项

使用方式:
    planner = DocumentPlanner(ai_client, model_name="gemini-3.1-pro-preview")
    plan = await planner.plan(user_request, previous_context="")
    # 然后将 plan 传入 content generator 生成各节
"""

import json
import re
from typing import Optional
from dataclasses import dataclass, field, asdict


# ═══════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════

@dataclass
class SectionPlan:
    heading: str = ""
    section_type: str = "text"         # text | table | comparison | timeline | highlight | list
    purpose: str = ""
    key_points: list = field(default_factory=list)
    rough_length: str = "medium"       # short | medium | long
    notes: str = ""


@dataclass
class DocumentPlan:
    doc_type: str = "word"             # word | excel | ppt | pdf
    title: str = ""
    target_audience: str = "通用"
    tone: str = "正式"                 # 正式 | 技术 | 轻松 | 营销 | 学术
    sections: list = field(default_factory=list)   # List[SectionPlan]
    table_schema: list = field(default_factory=list)
    visual_hints: list = field(default_factory=list)
    generation_notes: str = ""
    raw_plan_text: str = ""            # 模型规划原文（调试用）
    success: bool = True
    error: str = ""

    def to_context_str(self) -> str:
        """转为可注入系统提示的字符串摘要"""
        lines = [
            f"[文档规划]",
            f"类型: {self.doc_type.upper()} | 标题: {self.title}",
            f"读者: {self.target_audience} | 语气: {self.tone}",
            f"注意: {self.generation_notes}" if self.generation_notes else "",
            "",
            "章节结构:",
        ]
        for i, sec in enumerate(self.sections):
            pts = "; ".join(sec.key_points[:4]) if sec.key_points else ""
            lines.append(f"  {i+1}. [{sec.section_type}] {sec.heading} — {sec.purpose} | 要点: {pts}")
        if self.table_schema:
            lines.append(f"\n表格列定义: {', '.join(self.table_schema)}")
        if self.visual_hints:
            lines.append(f"配图/图表建议: {'; '.join(self.visual_hints)}")
        return "\n".join(filter(None, lines))


# ═══════════════════════════════════════════════════════
#  规划提示词
# ═══════════════════════════════════════════════════════

_PLANNING_SYSTEM_PROMPT = """\
你是 Koto 的文档规划师。在正式生成内容之前，你需要先分析用户需求，输出一份详细的文档生成计划（JSON）。

规则：
1. 只输出 JSON，不要输出额外说明文字。
2. 严格按照下面的 JSON 结构。
3. sections 中的 section_type 只能是: text | table | comparison | timeline | highlight | list | chart
4. rough_length 只能是: short | medium | long
5. tone 只能是: 正式 | 技术 | 轻松 | 营销 | 学术
6. doc_type 只能是: word | excel | ppt | pdf

JSON 结构:
{
  "doc_type": "word",
  "title": "文档标题",
  "target_audience": "读者描述",
  "tone": "正式",
  "generation_notes": "整体注意事项，如需要包含数据/对比/时间线等特殊要求",
  "table_schema": ["列1:文本", "列2:数字", "列3:日期"],
  "visual_hints": ["封面配图：专业办公场景", "图表：增长曲线"],
  "sections": [
    {
      "heading": "章节标题",
      "section_type": "text",
      "purpose": "此章节要达到的目标",
      "key_points": ["要点1", "要点2", "要点3"],
      "rough_length": "medium",
      "notes": "特殊格式说明（可选）"
    }
  ]
}
"""

_PLANNING_USER_TEMPLATE = """\
用户需求: {user_request}

{ctx_block}
请针对上述需求，输出文档规划 JSON。
"""


# ═══════════════════════════════════════════════════════
#  DocumentPlanner
# ═══════════════════════════════════════════════════════

class DocumentPlanner:
    """
    调用 AI 模型生成文档生成计划。
    支持异步调用（async plan）和同步后备（sync plan_sync）。
    """

    def __init__(self, ai_client, model_name: str = "gemini-3.1-pro-preview"):
        self.client = ai_client
        self.model_name = model_name

    # ──────────────────────────────────────────────────
    #  公开 API
    # ──────────────────────────────────────────────────

    async def plan(self, user_request: str, previous_context: str = "") -> DocumentPlan:
        """异步规划（与 asyncio 事件循环兼容）"""
        import asyncio
        return await asyncio.to_thread(self.plan_sync, user_request, previous_context)

    def plan_sync(self, user_request: str, previous_context: str = "") -> DocumentPlan:
        """同步规划"""
        prompt = self._build_prompt(user_request, previous_context)
        try:
            from google.genai import types as genai_types
            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_PLANNING_SYSTEM_PROMPT,
                    temperature=0.2,
                    max_output_tokens=2000,
                ),
            )
            raw = resp.text or ""
            return self._parse_plan(raw, user_request)
        except Exception as e:
            return self._fallback_plan(user_request, error=str(e))

    # ──────────────────────────────────────────────────
    #  内部方法
    # ──────────────────────────────────────────────────

    def _build_prompt(self, user_request: str, previous_context: str) -> str:
        ctx_block = ""
        if previous_context:
            ctx_block = f"参考信息/已有数据:\n{previous_context[:1500]}\n"
        return _PLANNING_USER_TEMPLATE.format(
            user_request=user_request,
            ctx_block=ctx_block,
        )

    def _parse_plan(self, raw_text: str, user_request: str) -> DocumentPlan:
        """从模型原始输出中解析 DocumentPlan"""
        # 提取 JSON 块
        json_str = self._extract_json(raw_text)
        if not json_str:
            print(f"[DocPlanner] ⚠️ 无法从输出中提取 JSON，使用 fallback")
            return self._fallback_plan(user_request, raw_plan_text=raw_text)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"[DocPlanner] ⚠️ JSON 解析失败: {e}")
            return self._fallback_plan(user_request, raw_plan_text=raw_text)

        # 转换 sections
        sections = []
        for sec_data in data.get("sections", []):
            sections.append(SectionPlan(
                heading=sec_data.get("heading", ""),
                section_type=sec_data.get("section_type", "text"),
                purpose=sec_data.get("purpose", ""),
                key_points=sec_data.get("key_points", []),
                rough_length=sec_data.get("rough_length", "medium"),
                notes=sec_data.get("notes", ""),
            ))

        plan = DocumentPlan(
            doc_type=data.get("doc_type", self._detect_doc_type(user_request)),
            title=data.get("title", ""),
            target_audience=data.get("target_audience", "通用"),
            tone=data.get("tone", "正式"),
            sections=sections,
            table_schema=data.get("table_schema", []),
            visual_hints=data.get("visual_hints", []),
            generation_notes=data.get("generation_notes", ""),
            raw_plan_text=raw_text,
            success=True,
        )
        print(f"[DocPlanner] ✅ 规划完成: {plan.doc_type} | {len(plan.sections)} 节 | {plan.title}")
        return plan

    def _extract_json(self, text: str) -> Optional[str]:
        """从文本中提取 JSON 块"""
        # 尝试 ```json ... ```
        m = re.search(r'```(?:json)?\s*(\{[\s\S]+?\})\s*```', text, re.DOTALL)
        if m:
            return m.group(1)
        # 尝试裸 JSON
        m = re.search(r'(\{[\s\S]+\})', text, re.DOTALL)
        if m:
            return m.group(1)
        return None

    def _detect_doc_type(self, user_request: str) -> str:
        req = user_request.lower()
        if any(k in req for k in ["ppt", "幻灯片", "演示", "presentation"]):
            return "ppt"
        if any(k in req for k in ["excel", "xlsx", "表格", "spreadsheet"]):
            return "excel"
        if "pdf" in req:
            return "pdf"
        return "word"

    def _fallback_plan(self, user_request: str, error: str = "", raw_plan_text: str = "") -> DocumentPlan:
        """当 AI 规划失败时，生成基础 fallback 计划"""
        doc_type = self._detect_doc_type(user_request)
        sections = [
            SectionPlan(heading="概述", section_type="text",
                        purpose="介绍背景与目的", key_points=["背景", "目的", "范围"]),
            SectionPlan(heading="主体内容", section_type="text",
                        purpose="核心内容展开", key_points=[user_request[:40]]),
            SectionPlan(heading="总结", section_type="text",
                        purpose="总结与建议", key_points=["结论", "建议", "后续步骤"]),
        ]
        return DocumentPlan(
            doc_type=doc_type,
            title="",
            target_audience="通用",
            tone="正式",
            sections=sections,
            success=False,
            error=error,
            raw_plan_text=raw_plan_text,
        )


# ═══════════════════════════════════════════════════════
#  系统提示增强器 —— 将规划注入生成阶段的系统提示
# ═══════════════════════════════════════════════════════

def build_generation_prompt_from_plan(plan: DocumentPlan, user_request: str, previous_data: str = "") -> str:
    """
    根据 DocumentPlan 构建发送给内容生成模型的用户提示。
    包含完整的结构指引 + 格式要求 + Markdown 输出规范。
    """
    # 类型感知的格式指令
    format_guide = _format_guide_for_type(plan)

    # 章节指引
    section_guide = _section_guide(plan)

    # 前序数据块
    data_block = f"\n[参考数据/已有信息]\n{previous_data[:2000]}\n" if previous_data.strip() else ""

    prompt = f"""用户需求: {user_request}
{data_block}
━━━ 文档规划 ━━━
{plan.to_context_str()}

━━━ 输出格式要求 ━━━
{format_guide}

━━━ 章节展开指引 ━━━
{section_guide}

请根据以上规划，直接输出最终文档内容（Markdown 格式）。
重要：只输出文档正文，不要输出任何解释、前言或代码块包装。
"""
    return prompt


def _format_guide_for_type(plan: DocumentPlan) -> str:
    """根据文档类型生成格式指南"""
    base = (
        "- 使用 # ## ### 等 Markdown 标题结构化内容\n"
        "- 列表项使用 - 开头\n"
        "- 重要内容可用 **加粗**\n"
        "- 表格使用标准 Markdown 表格格式（|列1|列2|）\n"
        "- 不要在正文中留下任何 AI 对话性前缀（如「好的，以下是...」）\n"
    )
    if plan.doc_type == "word":
        return base + "- 在正文中保留 Markdown 格式，系统会自动转换为 Word 格式\n"
    elif plan.doc_type == "excel":
        return (
            "- 输出完整的 Markdown 表格，包含表头和所有数据行\n"
            "- 表格格式: | 列名 | 列名 | ...\n"
            f"- 列定义: {', '.join(plan.table_schema) if plan.table_schema else '根据需求自定义'}\n"
            "- 数据行尽量详细，每行完整\n"
        )
    elif plan.doc_type == "ppt":
        return (
            "- 使用 # 作为演示标题，## 作为章节/幻灯片标题\n"
            "- 每个 ## 下用 - 列举要点（3-5 个）\n"
            "- 要点简洁有力，不要长句\n"
            "- 可以在 ## 前加 [详细] [概览] [亮点] [过渡页] 等标签\n"
        )
    return base


def _section_guide(plan: DocumentPlan) -> str:
    """为每个章节生成具体指引"""
    if not plan.sections:
        return "按逻辑顺序展开内容即可。"
    lines = []
    for i, sec in enumerate(plan.sections):
        length_map = {"short": "200-400字", "medium": "400-800字", "long": "800-1500字"}
        length_hint = length_map.get(sec.rough_length, "适当长度")

        type_hint = {
            "table": "输出 Markdown 表格",
            "comparison": "使用对比结构（两栏或对比表格）",
            "timeline": "按时间顺序列出事件，格式：时间 — 事件描述",
            "highlight": "使用数据或关键指标突出显示，格式：- 数值 | 说明",
            "list": "使用编号或项目列表展开",
            "text": "段落式展开，论点清晰",
            "chart": "描述图表数据，配合 Markdown 表格辅助说明",
        }.get(sec.section_type, "根据内容选择合适格式")

        pts = "\n    ".join(f"• {p}" for p in sec.key_points if p)
        lines.append(
            f"{i+1}. **{sec.heading}** [{sec.section_type}] ({length_hint})\n"
            f"   目标: {sec.purpose}\n"
            f"   须涵盖:\n    {pts if pts else '（根据需求自由展开）'}\n"
            f"   格式要求: {type_hint}\n"
            + (f"   特殊说明: {sec.notes}\n" if sec.notes else "")
        )
    return "\n".join(lines)

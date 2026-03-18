"""
skill_recorder.py — 从对话自动提取 SkillDefinition
======================================================
将一段用户-AI 对话转化为标准化的 SkillDefinition，
方便后续复用、导出 MCP 工具、或触发 LoRA 微调。

主要入口:
  SkillRecorder.from_conversation(session_id, skill_name, description)
  SkillRecorder.from_text(user_input, ai_response, skill_name, description)
  SkillRecorder.save_and_register(skill_def)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys as _sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 路径辅助 ──────────────────────────────────────────────────────────────────


def _get_base_dir() -> Path:
    if getattr(_sys, "frozen", False):
        return Path(_sys.executable).parent
    return Path(__file__).resolve().parents[3]


_BASE_DIR = _get_base_dir()
_CHATS_DIR = str(_BASE_DIR / "chats")
_SKILLS_DIR = str(_BASE_DIR / "config" / "skills")


# ── LLM 对话语义解析 Prompt ───────────────────────────────────────────────────
_ANALYSIS_PROMPT = """你是 Koto Skill 抽象引擎。从以下示例对话中提炼一个通用的、可复用的 Skill 定义。
你的目标是理解这段对话背后的「意图模式」，而不是复制具体内容。

Skill 名称: {skill_name}
Skill 描述: {description}

示例对话:
{conversation}

请严格输出以下 JSON（禁止任何额外文字和 markdown 代码块）:
{{
  "system_prompt": "面向 AI 助手的 system prompt，80-200字，描述通用的行为准则和输出规范，不含示例中的具体数据",
  "intent_description": "≤30字，概括何种用户意图应触发此 Skill，需包含核心关键词",
  "task_types": ["最适用的1-2个类型，从 CHAT/CODER/RESEARCH/FILE_GEN/DOC_ANNOTATE/SYSTEM/WEB_SEARCH 中选"],
  "output_format": "markdown或plain或json或code之一",
  "skill_nature": "domain_skill或model_hint之一",
  "tags": ["2-5个关键词标签"],
  "input_variables": [
    {{"name": "变量名", "description": "参数说明（≤20字）", "required": true}}
  ],
  "trigger_keywords": ["3-6个中文关键词，用户输入包含这些词时应触发此 Skill，必须简洁精准"],
  "executor_tools": ["执行此任务时应调用的工具，从以下中选（可为空列表）: read_file_snippet, find_file, summarize_file, execute_python, list_directory, web_search, memory_search"],
  "plan_template": ["完成此任务的有序步骤1", "步骤2（若无需多步骤则为空列表）"]
}}

要求:
- system_prompt 应是通用行为约束与输出规范，禁止照抄示例中的具体问题或数据
- intent_description 用于对话自动匹配触发，要简洁且含核心触发词
- trigger_keywords 是供关键词匹配引擎使用的词表，应覆盖用户触发本 Skill 时最可能用到的表达
- executor_tools 仅填写此 Skill 真正需要调用的工具；若为纯对话/风格类 Skill 则填 []
- plan_template 填写 2-5 个有序执行步骤；若为纯风格/对话类 Skill 则填 []
- 若 Skill 主要调整 AI 输出风格/行为 → skill_nature = "model_hint"
- 若 Skill 提供专业知识/领域模板/分析框架 → skill_nature = "domain_skill"
- input_variables 只列真正关键的输入，通常 1-3 个"""


# ── 懒加载 SkillDefinition ────────────────────────────────────────────────────


def _get_skill_schema():
    from app.core.skills.skill_schema import InputVariable, OutputSpec, SkillDefinition

    return SkillDefinition, InputVariable, OutputSpec


def _get_skill_manager():
    from app.core.skills.skill_manager import SkillManager

    return SkillManager


# ── 对话加载 ─────────────────────────────────────────────────────────────────


def _load_chat_history(session_id: str) -> List[Dict[str, str]]:
    """
    从 chats/{session_id}.json 读取对话记录。
    支持多种格式:
      - [{"role": "user"|"model"|"assistant", "content": "...", "parts": [...]}]
      - {"history": [...]}
    """
    path = os.path.join(_CHATS_DIR, f"{session_id}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict) and "history" in raw:
            return raw["history"]
    except Exception as e:
        logger.warning(f"[skill_recorder] 无法读取 chats/{session_id}.json: {e}")
    return []


def _extract_turns(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    将原始历史记录规范化为 [{"role": "user"|"assistant", "text": str}, ...]
    """
    turns = []
    for entry in history:
        role_raw = entry.get("role", "")
        role = "assistant" if role_raw in ("model", "assistant", "ai") else "user"

        # content 可能是字符串或 parts 列表
        text = entry.get("content") or ""
        if not text and "parts" in entry:
            parts = entry["parts"]
            if isinstance(parts, list):
                text = " ".join(
                    p.get("text", p) if isinstance(p, dict) else str(p) for p in parts
                )
        if text:
            turns.append({"role": role, "text": str(text)})
    return turns


# ── 技能元信息推断 ────────────────────────────────────────────────────────────

_TAG_PATTERNS = [
    (r"帮我写|请写|生成.*文章|写一篇", "writing"),
    (r"翻译|translate|译文", "translation"),
    (r"分析|总结|摘要|summary", "analysis"),
    (r"代码|python|javascript|程序|function|def |class ", "coding"),
    (r"搜索|查找|查询|search|find", "search"),
    (r"计算|算出|数学|math", "math"),
    (r"邮件|email|mail", "email"),
    (r"会议|日程|提醒|schedule", "productivity"),
]


def _infer_tags(text: str) -> List[str]:
    tags = []
    text_lower = text.lower()
    for pattern, tag in _TAG_PATTERNS:
        if re.search(pattern, text_lower):
            tags.append(tag)
    return list(set(tags)) or ["general"]


def _infer_input_variables(user_text: str):
    """
    简单启发式：识别 {占位符} 或常见参数模式。
    """
    SkillDefinition, InputVariable, OutputSpec = _get_skill_schema()
    placeholders = re.findall(r"\{(\w+)\}", user_text)
    if placeholders:
        return [
            InputVariable(
                name=p,
                description=f"输入参数: {p}",
                required=True,
                example=p,
            )
            for p in dict.fromkeys(placeholders)  # 去重保序
        ]
    # 默认单输入
    return [
        InputVariable(
            name="input",
            description="用户输入",
            required=True,
            example=user_text[:80] if user_text else "...",
        )
    ]


def _make_skill_id(name: str) -> str:
    """生成 URL 安全、小写、无空格的 skill id"""
    slug = re.sub(r"[^\w\u4e00-\u9fff]", "_", name.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or f"skill_{hashlib.md5(name.encode()).hexdigest()[:8]}"


def _auto_register_intent_binding(skill_def) -> None:
    """
    从 intent_description 和 tags 中提取触发关键词，
    并向 SkillBindingManager 注册意图绑定，使新 Skill 立即参与关键词自动匹配。
    无论成功与否都不抛出异常，只记录日志。
    """
    # 收集候选关键词：tags 优先，再从 intent_description 提取短语
    skip_generic = {
        "general",
        "custom",
        "style",
        "behavior",
        "domain",
        "workflow",
        "auto-extracted",
        "chat",
        "coder",
        "research",
    }
    keywords: list = []

    # 1. 有效 tags
    for tag in getattr(skill_def, "tags", None) or []:
        tag = str(tag).strip()
        if len(tag) >= 2 and tag.lower() not in skip_generic:
            keywords.append(tag)

    # 2. 从 intent_description 按常用分隔符拆出 2-8 字短语
    intent = str(getattr(skill_def, "intent_description", "") or "").strip()
    if intent:
        parts = re.split(r"[、，；。/|或]", intent)
        for part in parts:
            # 去掉常见前缀
            for prefix in ("用户需要", "用户想要", "当用户", "用于", "适用于", "用户"):
                if part.startswith(prefix):
                    part = part[len(prefix) :]
            part = part.strip()
            if 2 <= len(part) <= 8:
                keywords.append(part)

    # 去重，最多 8 个
    seen: set = set()
    patterns: list = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            patterns.append(kw)
            if len(patterns) >= 8:
                break

    if not patterns:
        return  # 无有效关键词，跳过

    try:
        from app.core.skills.skill_trigger_binding import get_skill_binding_manager

        mgr = get_skill_binding_manager()
        # 检查是否已有同 Skill 的意图绑定，避免重复注册
        existing = mgr.list_bindings(skill_id=skill_def.id, binding_type="intent")
        if existing:
            return
        mgr.bind_intent(
            skill_id=skill_def.id,
            intent_patterns=patterns,
            auto_disable_after_turns=3,
        )
        logger.info(
            f"[skill_recorder] 自动注册意图绑定: {skill_def.id} " f"keywords={patterns}"
        )
    except Exception as e:
        logger.debug(f"[skill_recorder] 意图绑定注册跳过: {e}")


# ── 核心类 ────────────────────────────────────────────────────────────────────


class SkillRecorder:
    """
    从对话 / 文本片段提取标准化 SkillDefinition。

    用法示例:
        sd = SkillRecorder.from_conversation("session_abc", "邮件起草助手")
        sd = SkillRecorder.from_text(user_input, ai_response, "代码审查", "帮助审查代码质量")
        SkillRecorder.save_and_register(sd)
    """

    # ── LLM 语义分析 ─────────────────────────────────────────────────────────

    @classmethod
    def _analyze_with_llm(
        cls,
        skill_name: str,
        description: str,
        turns: List[Dict[str, str]],
        timeout: float = 8.0,
    ) -> Optional[Dict[str, Any]]:
        """
        调用 Gemini 对对话进行语义解析，从具体案例中抽象出通用的 Skill 行为定义。

        Returns:
            包含 system_prompt/intent_description/task_types 等字段的 dict；
            LLM 不可用或解析失败时返回 None，调用方自动降级为规则提取。
        """
        import threading as _threading

        # 格式化对话（最多 10 轮，每轮截取前 800 字）
        conv_lines = []
        for t in turns[:10]:
            role = "用户" if t.get("role") == "user" else "助手"
            text = (t.get("text") or "")[:800]
            if text:
                conv_lines.append(f"[{role}]:\n{text}")
        if not conv_lines:
            return None

        # 获取共享 Gemini client（与 LocalPlanner / AIRouter 同一模式）
        import sys as _sys

        _app_module = _sys.modules.get("web.app") or _sys.modules.get("app")
        _client = getattr(_app_module, "client", None) if _app_module else None
        if _client is None:
            logger.debug("[skill_recorder] Gemini client 不可用，跳过 LLM 分析")
            return None

        result_holder: Dict[str, Any] = {"data": None, "error": None}
        prompt_text = _ANALYSIS_PROMPT.format(
            skill_name=skill_name,
            description=description or skill_name,
            conversation="\n\n".join(conv_lines),
        )

        def _call():
            try:
                import importlib

                _types = importlib.import_module("google.genai.types")
                resp = _client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents="请分析对话并输出 Skill 定义 JSON。",
                    config=_types.GenerateContentConfig(
                        system_instruction=prompt_text,
                        response_mime_type="application/json",
                        temperature=0.2,
                        max_output_tokens=700,
                    ),
                )
                result_holder["data"] = (resp.text or "").strip()
            except Exception as e:
                result_holder["error"] = str(e)

        t = _threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            logger.debug("[skill_recorder] LLM 分析超时（%.1fs）", timeout)
            return None
        if result_holder["error"]:
            logger.debug("[skill_recorder] LLM 分析失败: %s", result_holder["error"])
            return None

        raw = re.sub(r"^```[a-z]*\n?", "", result_holder["data"] or "").rstrip("` \n")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or not data.get("system_prompt"):
                return None
            logger.info(
                "[skill_recorder] ✅ LLM 语义分析完成: %s → %s",
                skill_name,
                data.get("intent_description", ""),
            )
            return data
        except json.JSONDecodeError as e:
            logger.debug(
                "[skill_recorder] LLM JSON 解析失败: %s | raw=%r", e, raw[:200]
            )
            return None

    # ── 从 session 对话文件提取 ──────────────────────────────────────────────

    @classmethod
    def from_conversation(
        cls,
        session_id: str,
        skill_name: str,
        description: str = "",
        max_turns: int = 5,
        use_ai_analysis: bool = True,
    ):
        """
        从 chats/{session_id}.json 读取最近 max_turns 轮对话，
        自动提取 SkillDefinition。

        返回 SkillDefinition 对象，如果对话不存在则抛出 ValueError。
        """
        history = _load_chat_history(session_id)
        if not history:
            raise ValueError(
                f"[SkillRecorder] 找不到 session '{session_id}' 的对话记录，"
                f"路径: {_CHATS_DIR}/{session_id}.json"
            )

        turns = _extract_turns(history)
        # 取最近的 max_turns 轮（成对）
        recent = turns[-max_turns * 2 :] if len(turns) > max_turns * 2 else turns

        # 找最后一对 user-assistant
        user_text = ""
        ai_text = ""
        for i in range(len(recent) - 1, -1, -1):
            if recent[i]["role"] == "assistant" and not ai_text:
                ai_text = recent[i]["text"]
            elif recent[i]["role"] == "user" and not user_text:
                user_text = recent[i]["text"]
            if user_text and ai_text:
                break

        examples = [{"user_input": t["text"] for t in recent if t["role"] == "user"}]
        # 改用列表推导更清晰
        example_pairs = cls._build_example_pairs(recent, max_turns)

        analysis = None
        if use_ai_analysis and recent:
            analysis = cls._analyze_with_llm(skill_name, description, recent)

        return cls._build_skill_def(
            skill_name=skill_name,
            description=description,
            prototype_user_input=user_text,
            prototype_ai_response=ai_text,
            example_pairs=example_pairs,
            session_id=session_id,
            analysis=analysis,
        )

    # ── 从文本片段提取 ───────────────────────────────────────────────────────

    @classmethod
    def from_text(
        cls,
        user_input: str,
        ai_response: str,
        skill_name: str,
        description: str = "",
        use_ai_analysis: bool = True,
    ):
        """
        从单对用户输入/AI 响应直接构建 SkillDefinition。
        当 use_ai_analysis=True 时，调用 LLM 对对话进行语义解析，
        从具体案例中抽象出通用的 Skill 行为定义。
        """
        analysis = None
        if use_ai_analysis:
            turns = [
                {"role": "user", "text": user_input},
                {"role": "assistant", "text": ai_response},
            ]
            analysis = cls._analyze_with_llm(skill_name, description, turns)

        return cls._build_skill_def(
            skill_name=skill_name,
            description=description,
            prototype_user_input=user_input,
            prototype_ai_response=ai_response,
            example_pairs=[{"input": user_input, "output": ai_response}],
            session_id=None,
            analysis=analysis,
        )

    # ── 持久化并注册 ─────────────────────────────────────────────────────────

    @classmethod
    def save_and_register(cls, skill_def, overwrite: bool = False) -> str:
        """
        将 SkillDefinition 保存到 config/skills/{id}.json 并在 SkillManager 中注册。
        返回 skill_id。
        """
        os.makedirs(_SKILLS_DIR, exist_ok=True)
        target = os.path.join(_SKILLS_DIR, f"{skill_def.id}.json")
        if os.path.exists(target) and not overwrite:
            raise FileExistsError(
                f"Skill '{skill_def.id}' 已存在。传入 overwrite=True 强制覆盖。"
            )

        # 保存 JSON
        with open(target, "w", encoding="utf-8") as f:
            json.dump(skill_def.to_dict(), f, ensure_ascii=False, indent=2)

        # 注册到 SkillManager
        try:
            SkillManager = _get_skill_manager()
            SkillManager.register_custom(skill_def)
            logger.info(f"[skill_recorder] 已注册 Skill: {skill_def.id}")
        except Exception as e:
            logger.warning(
                f"[skill_recorder] SkillManager 注册失败（已保存到磁盘）: {e}"
            )

        # 自动注册意图绑定：从 intent_description + tags 提取触发关键词
        _auto_register_intent_binding(skill_def)

        # 自动注册意图绑定：从 intent_description + tags 提取触发关键词
        _auto_register_intent_binding(skill_def)

        # ── 注册触发关键词到决策层 ────────────────────────────────────────────
        kws = list(getattr(skill_def, "trigger_keywords", None) or [])
        if kws:
            skill_id = skill_def.id
            # 1) SkillAutoMatcher._PATTERN_MAP（关键词即时匹配）
            try:
                from app.core.skills.skill_auto_matcher import SkillAutoMatcher

                # 避免重复注册
                existing_ids = {
                    e.get("skill_id") for e in SkillAutoMatcher._PATTERN_MAP
                }
                if skill_id not in existing_ids:
                    SkillAutoMatcher._PATTERN_MAP.append(
                        {"skill_id": skill_id, "patterns": kws}
                    )
                    logger.info(
                        "[skill_recorder] ✅ AutoMatcher 注册 %d 个关键词: %s",
                        len(kws),
                        kws,
                    )
            except Exception as e:
                logger.debug("[skill_recorder] AutoMatcher 注册失败: %s", e)

            # 2) SkillBindingManager.bind_intent()（对话 intent 持久绑定）
            try:
                from app.core.skills.skill_trigger_binding import (
                    get_skill_binding_manager,
                )

                get_skill_binding_manager().bind_intent(
                    skill_id=skill_id,
                    intent_patterns=kws,
                    auto_disable_after_turns=3,
                )
                logger.info(
                    "[skill_recorder] ✅ BindingManager intent 绑定: %s", skill_id
                )
            except Exception as e:
                logger.debug("[skill_recorder] BindingManager 绑定失败: %s", e)

        return skill_def.id

    # ── 私有辅助 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_example_pairs(
        turns: List[Dict[str, str]], max_pairs: int
    ) -> List[Dict[str, str]]:
        """从规范化的 turns 列表提取用户-assistant 配对示例"""
        pairs = []
        i = 0
        while i < len(turns) - 1 and len(pairs) < max_pairs:
            if turns[i]["role"] == "user" and turns[i + 1]["role"] == "assistant":
                pairs.append(
                    {"input": turns[i]["text"], "output": turns[i + 1]["text"]}
                )
                i += 2
            else:
                i += 1
        return pairs

    @staticmethod
    def _build_skill_def(
        skill_name: str,
        description: str,
        prototype_user_input: str,
        prototype_ai_response: str,
        example_pairs: List[Dict[str, str]],
        session_id: Optional[str] = None,
        analysis: Optional[Dict[str, Any]] = None,
    ):
        """内部工厂：构建 SkillDefinition 实例（LLM 分析结果优先，规则兜底）"""
        SkillDefinition, InputVariable, OutputSpec = _get_skill_schema()

        skill_id = _make_skill_id(skill_name)

        # ── 优先使用 LLM 分析结果 ────────────────────────────────────────────
        if analysis and analysis.get("system_prompt"):
            system_prompt = analysis["system_prompt"]
            intent_desc = analysis.get("intent_description", "")
            task_types = analysis.get("task_types", [])
            output_fmt = analysis.get("output_format", "markdown")
            skill_nature = analysis.get("skill_nature", "domain_skill")
            tags = analysis.get("tags", [])
            executor_tools = analysis.get("executor_tools", [])
            plan_tmpl = analysis.get("plan_template", [])
            trigger_kws = analysis.get("trigger_keywords", [])

            # 将执行步骤嵌入 system_prompt，确保无论何种触发方式都能注入
            if plan_tmpl:
                system_prompt = (
                    system_prompt
                    + "\n\n### ⚙️ 执行步骤（必须严格按顺序完成）\n"
                    + "\n".join(f"{i+1}. {step}" for i, step in enumerate(plan_tmpl))
                )

            raw_vars = analysis.get("input_variables", [])
            if raw_vars and isinstance(raw_vars, list):
                input_vars = [
                    InputVariable(
                        name=v.get("name", "input"),
                        description=v.get("description", ""),
                        required=bool(v.get("required", True)),
                        example=prototype_user_input[:80] if idx == 0 else None,
                    )
                    for idx, v in enumerate(raw_vars)
                    if isinstance(v, dict) and v.get("name")
                ] or _infer_input_variables(prototype_user_input)
            else:
                input_vars = _infer_input_variables(prototype_user_input)
        else:
            # ── 规则兜底 ─────────────────────────────────────────────────────
            system_prompt = (
                f"你是一个专注于「{skill_name}」任务的 AI 助手。\n"
                + (f"{description}\n" if description else "")
                + "请根据用户的具体输入，给出高质量、简洁明了的回答。"
            )
            intent_desc = ""
            task_types = []
            output_fmt = "markdown"
            skill_nature = "domain_skill"
            tags = _infer_tags(prototype_user_input + " " + description)
            input_vars = _infer_input_variables(prototype_user_input)
            executor_tools = []
            plan_tmpl = []
            trigger_kws = []

        max_ch = max(len(prototype_ai_response), 200) if prototype_ai_response else 2000
        output_spec = OutputSpec(
            format=output_fmt,
            max_chars=min(max_ch * 3, 8000),
            must_contain=[],
            must_not_contain=[],
        )

        sd = SkillDefinition(
            id=skill_id,
            name=skill_name,
            icon="🤖",
            category="custom",
            skill_nature=skill_nature,
            description=description
            or intent_desc
            or f"自动从对话提取的技能：{skill_name}",
            intent_description=intent_desc,
            task_types=task_types,
            version="1.0.0",
            author="skill_recorder",
            tags=tags,
            input_variables=input_vars,
            system_prompt_template=system_prompt,
            prompt=system_prompt,  # 向后兼容
            output_spec=output_spec,
            examples=example_pairs,
            bound_tools=executor_tools,
            executor_tools=executor_tools,
            plan_template=plan_tmpl,
            trigger_keywords=trigger_kws,
        )
        return sd

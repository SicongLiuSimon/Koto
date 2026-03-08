# -*- coding: utf-8 -*-
"""
Koto SkillAutoMatcher — 自动技能匹配引擎
==========================================

当用户没有手动启用任何 Skill 时，根据任务类型和用户输入，自动推荐
最适合本轮对话的 1-3 个 Skill，并以「临时注入」方式作用于本次请求，
不修改 SkillManager 中技能的持久启用状态。

关键特性
--------
1. **本地模型优先**：调用 Ollama (Qwen3 + /no_think) 进行语义匹配；
   响应快、隐私安全、支持中英文。
2. **新 Skill 零重训**：每次从 SkillManager 动态读取技能目录，
   包含 description / intent_description 字段；新增 Skill 后无需
   重新训练，模型可立即从上下文中学到新技能用途，完成匹配。
3. **规则兜底**：Ollama 不可用时自动降级到正则模式匹配，
   保证基础功能不中断。
4. **保守策略**：用户已手动启用了 Skill（当前 task_type 下有任何
   启用的 Skill）时，AutoMatcher 静默退出，尊重用户选择。

用法
----
    from app.core.skills.skill_auto_matcher import SkillAutoMatcher

    # 在 UnifiedAgent/agent_routes 的 inject_into_prompt 之前调用：
    temp_ids = SkillAutoMatcher.match(user_input, task_type="CHAT")
    prompt = SkillManager.inject_into_prompt(
        base_instruction, task_type="CHAT",
        user_input=user_input,
        temp_skill_ids=temp_ids,
    )
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Ollama 调用超时（秒）——匹配推理比任务分类稍微宽松一点
# ──────────────────────────────────────────────────────────────────────────────
_MATCH_TIMEOUT = 8.0
_MAX_AUTO_SKILLS = 3   # 单次最多自动注入的 Skill 数量


class SkillAutoMatcher:
    """自动 Skill 匹配引擎（v1）。"""

    # ── Qwen3 /no_think 格式的匹配 Prompt ─────────────────────────────────────
    MATCH_PROMPT = """/no_think
你是 Koto Skill 匹配引擎。根据任务类型和用户消息，从候选技能列表中选出 \
0-{max_n} 个最合适的技能 ID。
规则：
- 只在技能能明显改善本次回答时才选；不要「凑数」
- 严格只输出 JSON 数组，例如 ["step_by_step"] 或 [] ；禁止任何额外文字
- 不要重复选择同一 ID

任务类型: {task_type}
用户消息: {user_input}

候选技能列表:
{skill_catalog}

输出 JSON 数组:"""

    # ── 规则兜底：意图关键词 → Skill ID ──────────────────────────────────────
    _PATTERN_MAP: List[dict] = [
        {"skill_id": "concise_mode",
         "patterns": ["简短", "简洁", "一句话", "快速说", "长话短说", "总结一下", "brief", "concise", "short"]},
        {"skill_id": "step_by_step",
         "patterns": ["一步一步", "分步骤", "操作流程", "详细步骤", "怎么做", "如何做", "请教我", "step by step", "step-by-step"]},
        {"skill_id": "teaching_mode",
         "patterns": ["教我", "讲解", "通俗解释", "像老师一样", "我没学过", "能解释一下", "浅显", "explain", "teach me"]},
        {"skill_id": "professional_tone",
         "patterns": ["正式一点", "专业一点", "商务语气", "写邮件", "汇报", "报告", "formal", "professional"]},
        {"skill_id": "writing_assistant",
         "patterns": ["润色", "改写", "优化表达", "重写", "整理成文", "polish", "rewrite"]},
        {"skill_id": "code_best_practices",
         "patterns": ["写代码", "重构", "代码优化", "最佳实践", "写个函数", "实现一下", "best practice"]},
        {"skill_id": "security_aware",
         "patterns": ["安全", "风险", "漏洞", "加密", "权限", "注入", "security", "vulnerability"]},
        {"skill_id": "research_depth",
         "patterns": ["深入分析", "深度研究", "详细分析", "全面比较", "in-depth", "comprehensive"]},
        {"skill_id": "task_planner",
         "patterns": ["计划", "安排", "待办", "路线图", "拆解任务", "里程碑", "plan", "roadmap", "todo"]},
        {"skill_id": "data_analysis",
         "patterns": ["数据分析", "统计", "图表", "可视化", "趋势", "data analysis", "visualization"]},
        {"skill_id": "debug_python",
         "patterns": ["调试", "报错", "bug", "错误", "异常", "debug", "error", "exception"]},
        {"skill_id": "creative_writing",
         "patterns": ["创意", "故事", "文案", "诗", "小说", "creative", "story", "poem"]},
        {"skill_id": "bilingual",
         "patterns": ["双语", "英文", "翻译", "术语", "bilingual", "translate", "english"]},
    ]

    @classmethod
    def _build_skill_catalog(cls, task_type: str) -> tuple[List[dict], str]:
        """
        从 SkillManager 动态构建候选技能目录（过滤不适用于当前 task_type 的技能）。
        Returns: (candidate_list, catalog_text_for_prompt)
        """
        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
        except Exception as e:
            logger.debug(f"[AutoMatcher] SkillManager 加载失败: {e}")
            return [], ""

        candidates: List[dict] = []
        lines: List[str] = []
        tt = task_type.upper() if task_type else ""

        for skill_id, s in SkillManager._registry.items():
            applicable = s.get("task_types", [])
            # 如果技能有 task_types 限制，过滤掉不适用的
            if applicable and tt and tt not in applicable:
                continue
            desc = (
                s.get("intent_description")
                or s.get("description", "")
            )
            name = s.get("name", skill_id)
            candidates.append({
                "id": skill_id,
                "name": name,
                "desc": desc,
                "task_types": applicable,
            })
            lines.append(f"  • {skill_id} ({name}): {desc}")

        return candidates, "\n".join(lines)

    @classmethod
    def _has_active_skills_for_task(cls, task_type: str) -> bool:
        """判断当前 task_type 下是否已有用户手动启用的 Skill。"""
        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
            tt = task_type.upper() if task_type else ""
            for s in SkillManager._registry.values():
                if not s.get("enabled", False):
                    continue
                applicable = s.get("task_types", [])
                if not applicable or tt in applicable:
                    return True
        except Exception:
            pass
        return False

    @classmethod
    def _match_with_patterns(cls, user_input: str, candidates: List[dict]) -> List[str]:
        """规则兜底：简单关键词匹配，返回匹配到的 skill_id 列表。"""
        candidate_ids = {c["id"] for c in candidates}
        matched: List[str] = []
        lowered = user_input.lower()
        for entry in cls._PATTERN_MAP:
            sid = entry["skill_id"]
            if sid not in candidate_ids:
                continue
            if any(p.lower() in lowered for p in entry["patterns"]):
                matched.append(sid)
                if len(matched) >= _MAX_AUTO_SKILLS:
                    break
        return matched

    @classmethod
    def _match_with_local_model(
        cls,
        user_input: str,
        task_type: str,
        catalog_text: str,
        candidate_ids: set,
    ) -> Optional[List[str]]:
        """
        使用本地 Ollama 模型进行语义匹配。
        Returns None 如果 Ollama 不可用或调用失败。
        """
        try:
            from app.core.routing.local_model_router import LocalModelRouter

            if not LocalModelRouter.is_ollama_available():
                return None
            if not LocalModelRouter._initialized:
                LocalModelRouter.init_model()
            if not LocalModelRouter._initialized or not LocalModelRouter._model_name:
                return None

            prompt = cls.MATCH_PROMPT.format(
                max_n=_MAX_AUTO_SKILLS,
                task_type=task_type,
                user_input=user_input[:500],  # 截断超长输入
                skill_catalog=catalog_text,
            )

            start = time.time()
            result, err = LocalModelRouter.call_ollama_chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是 Koto Skill 匹配引擎。"
                            "只输出 JSON 数组，格式如 [\"skill_id\"] 或 []，"
                            "禁止任何其他内容。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.0, "num_predict": 80},
                timeout=_MATCH_TIMEOUT,
            )
            latency = time.time() - start

            if err:
                logger.debug(f"[AutoMatcher] Ollama 调用失败: {err}")
                return None

            # 解析 JSON 数组
            result = result.strip()
            # 移除 markdown 代码块
            result = re.sub(r"^```[a-z]*\n?", "", result)
            result = re.sub(r"\n?```$", "", result)
            result = result.strip()

            if not result.startswith("["):
                # 尝试提取 [...] 片段
                m = re.search(r"\[.*?\]", result, re.DOTALL)
                result = m.group() if m else "[]"

            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                logger.debug(f"[AutoMatcher] JSON 解析失败: {result!r}")
                return None

            if not isinstance(parsed, list):
                return None

            # 过滤出已知 skill_id，最多 _MAX_AUTO_SKILLS 个
            valid = [
                sid for sid in parsed
                if isinstance(sid, str) and sid in candidate_ids
            ][:_MAX_AUTO_SKILLS]

            logger.info(
                f"[AutoMatcher] 🎯 本地模型匹配 ({latency:.2f}s): "
                f"{task_type} → {valid}"
            )
            return valid

        except Exception as e:
            logger.debug(f"[AutoMatcher] 本地模型调用异常: {e}")
            return None

    @classmethod
    def match(
        cls,
        user_input: str,
        task_type: str = "CHAT",
        force: bool = False,
    ) -> List[str]:
        """
        自动匹配本轮对话最适合的 Skill ID 列表（临时注入，不修改持久状态）。

        参数
        ----
        user_input : 用户原始输入文本
        task_type  : 任务分类（SmartDispatcher 输出）
        force      : True = 即使用户已手动启用 Skill 也执行匹配（补充模式）

        返回
        ----
        List[str]  : 推荐临时激活的 skill_id 列表；空列表表示不需要额外注入
        """
        # ── 如果用户已经手动启用了适合本轮任务的 Skill，默认退出 ─────────────
        if not force and cls._has_active_skills_for_task(task_type):
            logger.debug(f"[AutoMatcher] 用户已启用 Skill，跳过自动匹配")
            return []

        # ── 构建候选 Skill 目录 ─────────────────────────────────────────────
        candidates, catalog_text = cls._build_skill_catalog(task_type)
        if not candidates:
            return []

        candidate_ids = {c["id"] for c in candidates}

        # ── 优先尝试本地模型匹配 ────────────────────────────────────────────
        model_result = cls._match_with_local_model(
            user_input, task_type, catalog_text, candidate_ids
        )
        if model_result is not None:
            return model_result

        # ── 模型不可用时规则兜底 ────────────────────────────────────────────
        pattern_result = cls._match_with_patterns(user_input, candidates)
        if pattern_result:
            logger.info(
                f"[AutoMatcher] 📋 规则兜底匹配: {task_type} → {pattern_result}"
            )
        return pattern_result

    @classmethod
    def describe_matched(cls, skill_ids: List[str]) -> str:
        """返回匹配到的 Skill 的中文名称列表，用于日志/调试输出。"""
        if not skill_ids:
            return "（无）"
        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
            names = [
                SkillManager._registry.get(sid, {}).get("name", sid)
                for sid in skill_ids
            ]
            return "、".join(names)
        except Exception:
            return ", ".join(skill_ids)

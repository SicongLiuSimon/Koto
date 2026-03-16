# -*- coding: utf-8 -*-
"""
Koto SkillSuggester — 智能 Skill 推荐引擎
==========================================

当 Koto 完成一次回答后，根据用户消息语义，从**未启用**的 Skill 中找出与当前
需求高度相关的候选，以「提示卡片」格式追加到回答末尾，引导用户发现并开启合适
的专项 Skill。

与 SkillAutoMatcher 的区别
--------------------------
- SkillAutoMatcher : 临时注入 Skill prompt 到**当前请求**，影响本轮回答质量
- SkillSuggester   : 在回答结束后，提示用户哪些 Skill **可以**启用以增强未来体验

触发条件
--------
- 有匹配的**未启用** Skill（已启用或本次临时注入的不重复提示）
- 多层相关性评分超过最低阈值
- 最多展示 3 个，避免信息过载

评分策略（多层叠加）
--------------------
1. SkillAutoMatcher._PATTERN_MAP 关键词命中（权重最高，最可靠）
2. Skill tags 词匹配（JSON Skill 专有，精确）
3. 字符二元组 Jaccard 相似度（intent_description / description 语义近似时补充）
4. 任务类型（task_type）亲和度加权
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_SUGGESTIONS = 3
_NGRAM_THRESHOLD = 0.08   # Jaccard 二元组阈值，与 SkillAutoMatcher 保持一致
_MIN_ANSWER_LEN  = 40     # 答案字数阈值——过短通常是闲聊，不推荐 Skill


class SkillSuggester:
    """答案末尾的 Skill 推荐引擎（只推荐未启用的相关 Skill）。"""

    # ── 公开 API ─────────────────────────────────────────────────────────────

    @classmethod
    def suggest(
        cls,
        user_input: str,
        task_type: str = "CHAT",
        already_active_ids: Optional[List[str]] = None,
        answer_text: str = "",
        max_n: int = _MAX_SUGGESTIONS,
    ) -> List[Dict]:
        """
        返回与用户输入相关但当前未启用的 Skill 候选列表。

        Parameters
        ----------
        user_input        : 用户原始消息
        task_type         : 当前任务分类（CHAT / FILE_GEN / RESEARCH …）
        already_active_ids: 本次已临时注入的 Skill ID（不重复提示）
        answer_text       : Koto 本轮回答文本（太短则跳过推荐）
        max_n             : 最多返回几个推荐

        Returns
        -------
        List of dicts: [{"id", "name", "icon", "description", "intent_description"}]
        """
        # 回答过短 → 通常是闲聊或一句话答复，不适合推荐 Skill
        if len(answer_text) < _MIN_ANSWER_LEN:
            return []

        exclude_ids = set(already_active_ids or [])

        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
        except Exception as exc:
            logger.debug(f"[SkillSuggester] SkillManager 加载失败，跳过推荐: {exc}")
            return []

        # ── 收集所有未启用的 Skill 作为候选 ─────────────────────────────────
        candidates: List[Dict] = []
        for skill_id, s in SkillManager._registry.items():
            if s.get("enabled", False):
                continue  # 已启用
            if skill_id in exclude_ids:
                continue  # 本次临时注入过了

            # 从 _def_registry 取完整定义（含 intent_description / tags / trigger_keywords）
            # _def_registry 在 register_custom() 时同步更新，新安装的 Skill 立即可见
            skill_def = SkillManager._def_registry.get(skill_id)
            intent_desc: str = ""
            tags: List[str] = []
            trigger_kws: List[str] = []
            if skill_def:
                intent_desc = getattr(skill_def, "intent_description", "") or ""
                tags = list(getattr(skill_def, "tags", None) or [])
                trigger_kws = list(getattr(skill_def, "trigger_keywords", None) or [])

            candidates.append({
                "id":               skill_id,
                "name":             s.get("name", skill_id),
                "icon":             s.get("icon", "🔧"),
                "description":      s.get("description", ""),
                "intent_description": intent_desc,
                "tags":             tags,
                "trigger_keywords": trigger_kws,
                "task_types":       s.get("task_types", []),
            })

        if not candidates:
            return []

        # ── 评分 & 排序 ──────────────────────────────────────────────────────
        scored = cls._score_candidates(user_input, candidates, task_type)
        scored = [(score, c) for score, c in scored if score > 0]
        scored.sort(key=lambda x: x[0], reverse=True)

        result = []
        for _, c in scored[:max_n]:
            result.append({
                "id":                 c["id"],
                "name":               c["name"],
                "icon":               c["icon"],
                "description":        c["description"],
                "intent_description": c["intent_description"],
            })
        return result

    @classmethod
    def format_hint(cls, suggestions: List[Dict]) -> str:
        """
        将推荐列表格式化为 Markdown 提示块，供直接追加到回答末尾。

        示例输出：
            ---
            💡 **相关 Skill 推荐** — 以下专项技能开启后能让我处理这类需求时表现更好：

            - 📊 **Excel 智能分析** — 上传 Excel/CSV，自动生成数据分析报告与洞察
            - 📝 **Excel 智能填表** — 将数据自动写入 Excel 模板，支持批量录入

            > 在侧边栏「Skills」面板中搜索并开启 ↗
        """
        if not suggestions:
            return ""

        lines = [
            "\n\n---",
            "\n💡 **相关 Skill 推荐** — 以下专项技能开启后能让我处理这类需求时表现更好：\n",
        ]
        for s in suggestions:
            icon = s.get("icon", "🔧")
            name = s.get("name", s["id"])
            desc = (s.get("intent_description") or s.get("description") or "").strip()
            if len(desc) > 65:
                desc = desc[:62] + "…"
            lines.append(f"- {icon} **{name}** — {desc}")

        lines.append("\n> 在侧边栏「Skills」面板中搜索并开启 ↗")
        return "\n".join(lines)

    # ── 内部方法 ─────────────────────────────────────────────────────────────

    @classmethod
    def _score_candidates(
        cls,
        user_input: str,
        candidates: List[Dict],
        task_type: str,
    ) -> List[tuple]:
        """
        为每个候选 Skill 计算相关性分数（多层叠加）。

        Layer 1a — Pattern map 关键词（3.0 pts）：复用 SkillAutoMatcher._PATTERN_MAP，
                   命中即得分，最可靠、覆盖内置 Skill 的典型触发词。

        Layer 1b — trigger_keywords 动态匹配（2.5 pts）：读取 SkillDefinition.trigger_keywords
                   字段，覆盖新安装 Skill 声明的触发词。_def_registry 在 register_custom()
                   后立即更新，所以新安装的 Skill 无需重启就能即时被发现。

        Layer 2  — Tags 精确匹配（1.5 pts）：JSON 自定义 Skill 的 tags 字段，
                   任一 tag 在用户输入中出现即计分。

        Layer 3  — 字符二元组 Jaccard（0.0~2.0 pts）：用 intent_description 或
                   description 与用户输入计算 n-gram 相似度，补充语义近似情形。

        任务类型亲和度：skill.task_types 包含当前 task_type 时乘以 1.2 因子。
        """
        lowered = user_input.lower()
        tt = task_type.upper() if task_type else ""

        # 预取 pattern map 命中集合（覆盖内置 Skill）
        pattern_hits = cls._get_pattern_hits(lowered)

        # 预计算用户输入的字符二元组集合（前 300 字）
        input_bigrams = cls._ngrams(user_input[:300])

        results: List[tuple] = []
        for c in candidates:
            skill_id = c["id"]
            score = 0.0

            # Layer 1a: Pattern map（内置 Skill 的静态触发词）
            if skill_id in pattern_hits:
                score += 3.0

            # Layer 1b: trigger_keywords（新安装 Skill 声明的动态触发词）
            # _def_registry 在 register_custom() 时实时更新，零重启即时生效
            for kw in c.get("trigger_keywords", []):
                if kw and kw.lower() in lowered:
                    score += 2.5
                    break  # 每个 Skill 只加一次

            # Layer 2: Tag 精确匹配
            for tag in c.get("tags", []):
                tag_norm = tag.lower().replace(" ", "")
                if tag_norm and tag_norm in lowered.replace(" ", ""):
                    score += 1.5
                    break  # 每个 Skill 只加一次

            # Layer 3: 字符二元组语义近似
            desc_text = (c.get("intent_description") or c.get("description") or "").strip()
            if desc_text and input_bigrams:
                desc_bigrams = cls._ngrams(desc_text[:400])
                if desc_bigrams:
                    union_size = len(input_bigrams | desc_bigrams)
                    if union_size > 0:
                        jaccard = len(input_bigrams & desc_bigrams) / union_size
                        if jaccard >= _NGRAM_THRESHOLD:
                            score += jaccard * 2.0

            # 任务类型亲和度加权
            applicable = c.get("task_types", [])
            if tt and applicable and tt in applicable:
                score *= 1.2

            results.append((score, c))

        return results

    @classmethod
    def _get_pattern_hits(cls, lowered_input: str) -> set:
        """
        复用 SkillAutoMatcher._PATTERN_MAP 做关键词匹配，
        返回命中 skill_id 的集合。
        独立于 SkillAutoMatcher.match() 调用，不触发 Ollama。
        """
        hits: set = set()
        try:
            from app.core.skills.skill_auto_matcher import SkillAutoMatcher
            for entry in SkillAutoMatcher._PATTERN_MAP:
                if any(p.lower() in lowered_input for p in entry["patterns"]):
                    hits.add(entry["skill_id"])
        except Exception as exc:
            logger.debug(f"[SkillSuggester] _get_pattern_hits 失败: {exc}")
        return hits

    @classmethod
    def _ngrams(cls, text: str, n: int = 2) -> set:
        """生成字符 n-gram 集合（移除空格后）。"""
        t = text.lower().replace(" ", "")
        return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}

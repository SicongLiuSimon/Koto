#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强的记忆管理器 - Phase 1: 自动提取 + 用户画像
支持从对话中自动学习用户偏好，建立用户画像
"""

import json
import math
import os
import time
import threading
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import logging


logger = logging.getLogger(__name__)

class UserProfile:
    """用户画像：综合理解用户特征"""

    def __init__(self, profile_path: str = "config/user_profile.json"):
        self.profile_path = profile_path
        self.profile = self._load_or_create()

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        """深度合并：以 base 为完整默认结构，override 中的值覆盖 base，但不删除 base 的键"""
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = UserProfile._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    def _default_profile(self) -> Dict:
        """返回完整的默认用户画像结构"""
        return {
            "communication_style": {
                "preferred_detail_level": "moderate",
                "preferred_language": "zh-CN",
                "formality": "casual",
                "emoji_usage": True,
                "code_style": "concise",
            },
            "technical_background": {
                "programming_languages": [],
                "experience_level": "intermediate",
                "domains": [],
                "tools": [],
            },
            "work_patterns": {
                "frequent_topics": {},
                "typical_tasks": [],
                "last_active": None,
            },
            "preferences": {"likes": [], "dislikes": [], "habits": []},
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "total_interactions": 0,
                "last_updated": datetime.now().isoformat(),
            },
        }

    def _load_or_create(self) -> Dict:
        """加载或创建用户画像，自动将旧格式/残缺格式与完整默认结构合并"""
        default = self._default_profile()
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # 深度合并：用已有数据覆盖默认值，但确保所有必需键都存在
                return self._deep_merge(default, loaded)
            except Exception as e:
                logger.info(f"[UserProfile] 加载失败，使用默认画像: {e}")
        return default

    def save(self):
        """保存用户画像"""
        try:
            os.makedirs(os.path.dirname(self.profile_path), exist_ok=True)
            with open(self.profile_path, "w", encoding="utf-8") as f:
                json.dump(self.profile, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[UserProfile] 保存失败: {e}")

    def update_from_extraction(self, extracted_info: Dict):
        """从LLM提取的信息更新画像"""
        try:
            # 更新技术背景
            if "programming_languages" in extracted_info:
                for lang in extracted_info["programming_languages"]:
                    if (
                        lang
                        not in self.profile["technical_background"][
                            "programming_languages"
                        ]
                    ):
                        self.profile["technical_background"][
                            "programming_languages"
                        ].append(lang)

            # 更新工具偏好
            if "tools" in extracted_info:
                for tool in extracted_info["tools"]:
                    if tool not in self.profile["technical_background"]["tools"]:
                        self.profile["technical_background"]["tools"].append(tool)

            # 更新领域
            if "domains" in extracted_info:
                for domain in extracted_info["domains"]:
                    if domain not in self.profile["technical_background"]["domains"]:
                        self.profile["technical_background"]["domains"].append(domain)

            # 更新偏好
            if "likes" in extracted_info:
                for item in extracted_info["likes"]:
                    if item not in self.profile["preferences"]["likes"]:
                        self.profile["preferences"]["likes"].append(item)

            if "dislikes" in extracted_info:
                for item in extracted_info["dislikes"]:
                    if item not in self.profile["preferences"]["dislikes"]:
                        self.profile["preferences"]["dislikes"].append(item)

            # 更新沟通风格
            if "communication_style" in extracted_info:
                self.profile["communication_style"].update(
                    extracted_info["communication_style"]
                )

            # 更新元数据
            self.profile["metadata"]["last_updated"] = datetime.now().isoformat()
            self.profile["metadata"]["total_interactions"] += 1

            self.save()

        except Exception as e:
            logger.info(f"[UserProfile] 更新失败: {e}")

    def increment_topic(self, topic: str):
        """增加话题计数"""
        topics = self.profile.get("work_patterns", {}).setdefault("frequent_topics", {})
        topics[topic] = topics.get(topic, 0) + 1
        self.save()

    def to_context_string(self) -> str:
        """转换为LLM上下文字符串"""
        lines = ["\n[用户画像]"]

        # 沟通风格
        style = self.profile.get("communication_style", {})
        lines.append(
            f"• 回复风格：{style.get('preferred_detail_level','moderate')}详细度，{style.get('formality','casual')}语气"
        )
        if style.get("code_style"):
            lines.append(f"• 代码风格：{style['code_style']}")

        # 技术背景
        tech = self.profile.get("technical_background", {})
        if tech.get("programming_languages"):
            lines.append(f"• 编程语言：{', '.join(tech['programming_languages'][:5])}")
        if tech.get("experience_level"):
            lines.append(f"• 经验水平：{tech['experience_level']}")

        # 偏好
        prefs = self.profile.get("preferences", {})
        if prefs.get("likes"):
            lines.append(f"• 喜欢：{', '.join(prefs['likes'][:3])}")
        if prefs.get("dislikes"):
            lines.append(f"• 不喜欢：{', '.join(prefs['dislikes'][:3])}")

        # 常用话题
        topics = self.profile["work_patterns"].get("frequent_topics", {})
        if topics:
            top_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:3]
            lines.append(f"• 常见话题：{', '.join([t[0] for t in top_topics])}")

        return "\n".join(lines) + "\n"

    def get_brief_summary(self) -> str:
        """获取简短总结"""
        tech = self.profile.get("technical_background", {})
        langs = tech.get("programming_languages", [])[:2]
        level = tech.get("experience_level", "intermediate")

        return f"{level}级别开发者" + (f"，熟悉{'/'.join(langs)}" if langs else "")


# ══════════════════════════════════════════════════════════════════════════════
# PersonalityMatrix — 动态个人记忆矩阵
# 追踪用户的认知风格、专长领域、近期目标和价值偏好。
# 持续在后台通过 LLM + ShadowWatcher 观察数据自动更新，无感知、零延迟。
# ══════════════════════════════════════════════════════════════════════════════

class PersonalityMatrix:
    """
    动态个人记忆矩阵 — 四维度持续学习用户个性：
      cognitive     : 主导思维风格（探索/执行/分析/创意）
      expertise     : 专长领域打分（由 ShadowWatcher 话题频次驱动）
      goals         : 近期目标列表（LLM 从每轮对话提取）
      recent_themes : 近期高频关注话题（ShadowWatcher 7 天窗口）
      values        : 偏好价值维度（效率/深度/正式程度）

    更新路径：
      1. ShadowWatcher 整合（无 LLM，毫秒级）— topics → expertise + cognitive
      2. LLM 深度分析（异步线程，不阻塞对话）— 提取 goal / cognitive_hint
    """

    _MATRIX_PATH: str = "config/personality_matrix.json"

    _DEFAULT_DATA: Dict = {
        "cognitive": {
            "exploratory": 0.5,   # 探索型：喜欢发散思维、追问机制
            "executor":    0.5,   # 执行型：聚焦动手完成任务
            "analytical":  0.5,   # 分析型：偏好逻辑推导和比较
            "creative":    0.5,   # 创意型：喜欢头脑风暴和想象
        },
        "expertise":      {},     # {域名: 分值 0–1}，如 "编程开发": 0.82
        "goals":          [],     # 近期目标（最多 10 条）
        "recent_themes":  [],     # 近期高频话题（最多 10 条）
        "values": {
            "efficiency": 0.5,    # 效率倾向（vs. 质量/深度）
            "depth":      0.5,    # 深度倾向（vs. 宽度/速度）
            "formality":  0.5,    # 正式程度偏好
        },
        "last_updated": None,
    }

    # ShadowWatcher 话题 → PersonalityMatrix 专长域 映射
    _TOPIC_TO_DOMAIN: Dict[str, str] = {
        "编程开发": "编程开发",
        "数据分析": "数据分析",
        "写作翻译": "写作翻译",
        "学习研究": "学习研究",
        "工作规划": "工作规划",
        "文件处理": "文件处理",
        "生活日常": "生活日常",
        "沟通协作": "沟通协作",
        "创意设计": "创意设计",
        "系统设置": "系统设置",
    }

    def __init__(self, path: str = None):
        self._path = path or self._MATRIX_PATH
        self.data: Dict = self._load()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        import copy
        default = copy.deepcopy(self._DEFAULT_DATA)
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                return self._deep_merge(default, saved)
            except Exception:
                pass
        return default

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                self.data["last_updated"] = datetime.now().isoformat()
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[PersonalityMatrix] 保存失败: {e}")

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = PersonalityMatrix._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    # ── 上下文生成 ───────────────────────────────────────────────────────────

    def to_context_string(self) -> str:
        """返回适合注入 LLM Prompt 的个人矩阵摘要（精炼、不超过 150 字）。"""
        parts = []

        # 主导认知风格（仅当某维度 > 0.55 时才有意义）
        cog = self.data.get("cognitive", {})
        if cog:
            dominant = max(cog, key=lambda k: cog[k])
            if cog[dominant] > 0.55:
                labels = {
                    "exploratory": "探索", "executor": "执行",
                    "analytical": "分析", "creative": "创意",
                }
                parts.append(f"思维风格：{labels.get(dominant, dominant)}")

        # 专长领域 Top 3（分值 > 0.2 才显示）
        expertise = self.data.get("expertise", {})
        if expertise:
            top3 = [
                t[0] for t in sorted(expertise.items(), key=lambda x: -x[1])[:3]
                if t[1] > 0.2
            ]
            if top3:
                parts.append(f"专长：{', '.join(top3)}")

        # 近期目标（最近 2 条）
        goals = [g for g in self.data.get("goals", []) if g][-2:]
        if goals:
            parts.append(f"近期目标：{' / '.join(goals)}")

        # 近期话题（最近 3 条）
        themes = self.data.get("recent_themes", [])[-3:]
        if themes:
            parts.append(f"近期关注：{', '.join(themes)}")

        if not parts:
            return ""

        return "\n[个人记忆矩阵]\n" + "\n".join(f"• {p}" for p in parts)

    # ── 异步更新入口（外部调用）─────────────────────────────────────────────

    @classmethod
    def update_async(
        cls,
        user_msg: str,
        ai_msg: str,
        llm_fn,
        instance: "PersonalityMatrix",
    ):
        """非阻塞触发矩阵更新，在后台线程执行，不影响主对话延迟。"""
        threading.Thread(
            target=cls._update_sync,
            args=(user_msg, ai_msg, llm_fn, instance),
            daemon=True,
            name="pm_update",
        ).start()

    @classmethod
    def _update_sync(
        cls,
        user_msg: str,
        ai_msg: str,
        llm_fn,
        instance: "PersonalityMatrix",
    ):
        """同步更新逻辑（在后台线程中运行）。"""
        try:
            # 步骤 1：整合 ShadowWatcher 行为数据（无需 LLM）
            instance._sync_from_shadow()
            # 步骤 2：LLM 深度提取（认知风格 + 目标）
            instance._llm_update(user_msg, ai_msg, llm_fn)
            instance._save()
            logger.debug("[PersonalityMatrix] ✅ 矩阵已更新")
        except Exception as e:
            logger.warning(f"[PersonalityMatrix] 更新异常: {e}")

    # ── 内部更新逻辑 ─────────────────────────────────────────────────────────

    def _sync_from_shadow(self):
        """
        从 ShadowWatcher 同步行为观察数据：
          - topics 频次 → expertise 分值（EMA 融合）
          - recent_topics_7d → recent_themes
          - task_types 分布 → cognitive 风格权重
        """
        try:
            from app.core.monitoring.shadow_watcher import ShadowWatcher
            obs = ShadowWatcher.get().get_observations()
        except Exception:
            return

        # ── expertise: ShadowWatcher 话题频次 → 专长分值 ──────────────────
        topics: Dict[str, int] = obs.get("topics", {})
        if topics:
            max_count = max(topics.values()) or 1
            for topic, count in topics.items():
                domain = self._TOPIC_TO_DOMAIN.get(topic, topic)
                new_score = min(1.0, count / max_count)
                existing = self.data["expertise"].get(domain, 0.0)
                # 慢速 EMA（α=0.10），防止单次对话大幅扰动
                self.data["expertise"][domain] = round(
                    existing * 0.90 + new_score * 0.10, 3
                )

        # ── recent_themes: 7 天窗口高频话题 ──────────────────────────────
        recent_7d: Dict[str, int] = obs.get("recent_topics_7d", {})
        if recent_7d:
            top5 = [
                t for t, _ in sorted(recent_7d.items(), key=lambda x: -x[1])[:5]
            ]
            self.data["recent_themes"] = top5

        # ── cognitive: 任务风格分布 → 风格维度 EMA ──────────────────────
        task_style = obs.get("task_style", {})
        task_types: Dict[str, int] = task_style.get("task_types", {})
        if task_types:
            total = sum(task_types.values()) or 1
            mapping = {
                "executor":    task_types.get("执行", 0) / total,
                "analytical":  task_types.get("分析", 0) / total,
                "creative":    task_types.get("创作", 0) / total,
                "exploratory": task_types.get("问答", 0) / total,
            }
            alpha = 0.10
            cog = self.data["cognitive"]
            for dim, ratio in mapping.items():
                cog[dim] = round(cog.get(dim, 0.5) * (1 - alpha) + ratio * alpha, 3)

        # ── values: 对话风格 → 价值偏好 ──────────────────────────────────
        conv_style = obs.get("conversation_style", {})
        if conv_style.get("samples", 0) >= 5:
            alpha = 0.08
            vals = self.data["values"]
            # polite_ratio 高 → formality 偏高
            polite = conv_style.get("polite_ratio", 0.5)
            vals["formality"] = round(vals.get("formality", 0.5) * (1 - alpha) + polite * alpha, 3)
            # avg_query_len > 80 字 → 深度倾向
            avg_len = conv_style.get("avg_query_len", 50)
            depth_signal = min(1.0, avg_len / 160)
            vals["depth"] = round(vals.get("depth", 0.5) * (1 - alpha) + depth_signal * alpha, 3)

    def _llm_update(self, user_msg: str, ai_msg: str, llm_fn):
        """用 LLM 分析单轮对话，提取 cognitive_hint / goal / interest。"""
        if not llm_fn or not user_msg:
            return

        prompt = (
            "分析以下对话，提取用户的思维风格倾向和当前目标，只返回JSON，不要解释：\n\n"
            f"用户：{user_msg[:400]}\n"
            f"AI：{ai_msg[:200]}\n\n"
            "{\n"
            '  "cognitive_hint": "exploratory|executor|analytical|creative（选一个，不确定则空字符串）",\n'
            '  "goal": "用户的当前具体目标（10-30字，无明确目标则返回空字符串）",\n'
            '  "interest": "近期偏好或关注点（10-20字，无则空字符串）"\n'
            "}"
        )
        try:
            raw = (llm_fn(prompt) or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1 if raw.count("```") >= 2 else 0]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw.strip())

            # cognitive_hint → EMA 更新风格权重
            hint = parsed.get("cognitive_hint", "").strip()
            if hint in self.data["cognitive"]:
                alpha = 0.08
                cog = self.data["cognitive"]
                for k in cog:
                    if k == hint:
                        cog[k] = round(cog[k] * (1 - alpha) + 0.9 * alpha, 3)
                    else:
                        # 其他维度温和回归中值
                        cog[k] = round(cog[k] * (1 - alpha * 0.3) + 0.45 * alpha * 0.3, 3)

            # goal → 追加至目标列表（去重，最多 10 条）
            goal = (parsed.get("goal") or "").strip()
            if goal and len(goal) > 3:
                goals = self.data.get("goals", [])
                if goal not in goals:
                    goals.append(goal)
                    self.data["goals"] = goals[-10:]

            # interest → 追加至 recent_themes（去重，最多 10 条）
            interest = (parsed.get("interest") or "").strip()
            if interest and len(interest) > 2:
                themes = self.data.get("recent_themes", [])
                if interest not in themes:
                    themes.append(interest)
                    self.data["recent_themes"] = themes[-10:]

        except Exception as e:
            logger.debug(f"[PersonalityMatrix] LLM 提取跳过: {e}")


# ── 记忆生命周期管理（GC）常量 ──────────────────────────────────────────────────
_GC_STALE_DAYS: int = 90        # 未被访问的自动提取记忆超过此天数将被清理
_GC_MAX_PER_CATEGORY: int = 150  # 单类别记忆条数上限（超出时保留最新 + 用户手动）

_PERSONALITY_MATRIX_PATH = "config/personality_matrix.json"


class PersonalityMatrix:
    """动态个人记忆矩阵：从对话中持续学习用户的认知风格、专长领域、目标与近期主题。

    数据结构（self.data）：
      cognitive     : dict[str, float]  — 认知风格得分 (exploratory/executor/analytical/creative)
      expertise     : dict[str, float]  — 专长领域及熟练度
      goals         : list[str]         — 用户近期目标（最多 10 条，滚动）
      recent_themes : list[str]         — 近期对话主题（最多 20 条，滚动）
    """

    _DEFAULT_COGNITIVE = {
        "exploratory": 0.5,
        "executor": 0.5,
        "analytical": 0.5,
        "creative": 0.5,
    }

    def __init__(self, path: str = _PERSONALITY_MATRIX_PATH):
        self._path = path
        self.data: Dict = self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # 补全缺失键，保证结构完整
                return {
                    "cognitive": {**self._DEFAULT_COGNITIVE, **saved.get("cognitive", {})},
                    "expertise": saved.get("expertise", {}),
                    "goals": saved.get("goals", []),
                    "recent_themes": saved.get("recent_themes", []),
                }
        except Exception:
            pass
        return {
            "cognitive": dict(self._DEFAULT_COGNITIVE),
            "expertise": {},
            "goals": [],
            "recent_themes": [],
        }

    def save(self):
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[PersonalityMatrix] 保存失败: {e}")

    # ── 上下文字符串 ─────────────────────────────────────────────────────────

    def to_context_string(self) -> str:
        """返回注入 LLM 上下文的个人记忆矩阵摘要字符串。"""
        parts = []

        # 认知风格
        cog = self.data.get("cognitive", {})
        if cog:
            dominant = max(cog, key=lambda k: cog[k])
            score = cog[dominant]
            if score > 0.55:
                labels = {
                    "exploratory": "探索型",
                    "executor": "执行型",
                    "analytical": "分析型",
                    "creative": "创意型",
                }
                parts.append(f"认知风格:{labels.get(dominant, dominant)}({score:.2f})")

        # 专长领域（取前 3）
        expertise = self.data.get("expertise", {})
        if expertise:
            top = sorted(expertise.items(), key=lambda x: x[1], reverse=True)[:3]
            parts.append(f"专长:{', '.join(t[0] for t in top)}")

        # 近期目标（最后 2 条）
        goals = [g for g in self.data.get("goals", []) if g]
        if goals:
            parts.append(f"目标:{' / '.join(goals[-2:])}")

        # 近期主题（最后 3 条）
        themes = self.data.get("recent_themes", [])
        if themes:
            parts.append(f"近期主题:{', '.join(themes[-3:])}")

        if not parts:
            return ""
        return "[个人矩阵] " + " | ".join(parts)

    # ── 异步更新（静态入口）──────────────────────────────────────────────────

    @staticmethod
    def update_async(user_msg: str, ai_msg: str, llm_fn, instance: "PersonalityMatrix"):
        """在后台线程中使用 LLM 分析对话，更新 instance 中的矩阵数据。"""

        def _worker():
            try:
                prompt = (
                    "请根据以下对话片段，提取用户的个人特征，以 JSON 格式返回，"
                    "不要包含任何其他文字：\n"
                    f'用户: {user_msg[:400]}\nAI: {ai_msg[:400]}\n\n'
                    "返回格式（所有字段可选，无法判断时留空/省略）：\n"
                    '{"cognitive":{"exploratory":0.0-1.0,"executor":0.0-1.0,'
                    '"analytical":0.0-1.0,"creative":0.0-1.0},'
                    '"expertise":{"话题A":0.0-1.0},'
                    '"goals":["目标（若有）"],'
                    '"recent_themes":["主题1","主题2"]}'
                )
                raw = llm_fn(prompt)
                if not raw:
                    return

                # 提取 JSON 块
                import re
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if not m:
                    return
                extracted = json.loads(m.group())

                # ── 软更新认知风格（指数平滑，α=0.15）──
                new_cog = extracted.get("cognitive", {})
                for k, v in new_cog.items():
                    if isinstance(v, (int, float)):
                        old = instance.data["cognitive"].get(k, 0.5)
                        instance.data["cognitive"][k] = round(0.85 * old + 0.15 * float(v), 4)

                # ── 软更新专长（α=0.2）──
                new_exp = extracted.get("expertise", {})
                for topic, score in new_exp.items():
                    if isinstance(score, (int, float)) and topic:
                        old = instance.data["expertise"].get(topic, 0.0)
                        instance.data["expertise"][topic] = round(0.80 * old + 0.20 * float(score), 4)
                # 专长超过 30 条时删最低分
                if len(instance.data["expertise"]) > 30:
                    sorted_exp = sorted(instance.data["expertise"].items(), key=lambda x: x[1])
                    instance.data["expertise"] = dict(sorted_exp[5:])  # 删最低 5 条

                # ── 滚动追加目标（最多保留 10 条）──
                for g in extracted.get("goals", []):
                    if g and g not in instance.data["goals"]:
                        instance.data["goals"].append(g)
                instance.data["goals"] = instance.data["goals"][-10:]

                # ── 滚动追加近期主题（最多保留 20 条）──
                for t in extracted.get("recent_themes", []):
                    if t:
                        instance.data["recent_themes"].append(t)
                instance.data["recent_themes"] = instance.data["recent_themes"][-20:]

                instance.save()
                logger.debug("[PersonalityMatrix] ✅ 矩阵已更新")
            except Exception as e:
                logger.warning(f"[PersonalityMatrix] ⚠️ 后台更新失败: {e}")

        t = threading.Thread(target=_worker, daemon=True, name="PersonalityMatrix-update")
        t.start()


class EnhancedMemoryManager:
    """增强的记忆管理器"""

    def __init__(
        self,
        memory_path: str = "config/memory.json",
        profile_path: str = "config/user_profile.json",
        summary_path: str = "config/memory_summaries.json",
        vector_path: str = "config/memory_vectors.json",
    ):
        self.memory_path = memory_path
        self.summary_path = summary_path
        self.vector_path = vector_path
        self.memories: List[Dict] = []
        self.summaries: Dict[str, Dict] = {}
        self.vector_memories: List[Dict] = []
        self.user_profile = UserProfile(profile_path)
        self.personality_matrix = PersonalityMatrix()   # 动态个人记忆矩阵
        self._embedding_fn = None
        self._generate_fn = None
        self._memory_rag = None  # 专用 FAISS 记庆索引（懒加载）

        self._load()
        self._load_summaries()
        self._load_vectors()

        logger.info(f"[EnhancedMemory] ✅ 记庆系统已启动")
        logger.info(f"[EnhancedMemory] 📊 当前记庆数：{len(self.memories)}")
        logger.info(f"[EnhancedMemory] 🧠 向量记庆数：{len(self.vector_memories)}")
        logger.info(f"[EnhancedMemory] 👤 用户画像：{self.user_profile.get_brief_summary()}")
        # 首次启动时如果 FAISS 记庆索引为空，自动从 memories.json 迁移建索
        self._rebuild_memory_rag_if_needed()
        # 异步执行记忆生命周期 GC（不阻塞启动）
        self.run_gc()

    def _load(self):
        """加载记忆"""
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    self.memories = json.load(f)
            except Exception as e:
                logger.info(f"[EnhancedMemory] 加载失败: {e}")
                self.memories = []

    def _save(self):
        """保存记忆"""
        try:
            os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)
            with open(self.memory_path, "w", encoding="utf-8") as f:
                json.dump(self.memories, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[EnhancedMemory] 保存失败: {e}")

    def _load_summaries(self):
        """加载对话摘要"""
        if os.path.exists(self.summary_path):
            try:
                with open(self.summary_path, "r", encoding="utf-8") as f:
                    self.summaries = json.load(f)
            except Exception as e:
                logger.info(f"[EnhancedMemory] 摘要加载失败: {e}")
                self.summaries = {}

    def _save_summaries(self):
        """保存对话摘要"""
        try:
            os.makedirs(os.path.dirname(self.summary_path), exist_ok=True)
            with open(self.summary_path, "w", encoding="utf-8") as f:
                json.dump(self.summaries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[EnhancedMemory] 摘要保存失败: {e}")

    def _load_vectors(self):
        """加载向量记忆"""
        if os.path.exists(self.vector_path):
            try:
                with open(self.vector_path, "r", encoding="utf-8") as f:
                    self.vector_memories = json.load(f)
            except Exception as e:
                logger.info(f"[EnhancedMemory] 向量加载失败: {e}")
                self.vector_memories = []

    def _save_vectors(self):
        """保存向量记忆"""
        try:
            os.makedirs(os.path.dirname(self.vector_path), exist_ok=True)
            with open(self.vector_path, "w", encoding="utf-8") as f:
                json.dump(self.vector_memories, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[EnhancedMemory] 向量保存失败: {e}")

    def set_llm_adapters(self, generate_fn=None, embedding_fn=None):
        """设置LLM适配器（摘要与向量）"""
        self._generate_fn = generate_fn
        self._embedding_fn = embedding_fn

    # ── FAISS 记庆向量索引（专用）──────────────────────────────────────────────────

    def _get_memory_rag(self):
        """
        获取专用的长期记庆 FAISS 索引实例（懒加载）。
        与知识库 RAGService 使用不同的目录，避免混杂。
        """
        if self._memory_rag is False:  # 之前初始化失败，不再重试
            return None
        if self._memory_rag is None:
            try:
                from app.core.services.rag_service import RAGService

                self._memory_rag = RAGService(
                    index_dir="config/memory_rag_index",
                    auto_load=True,
                )
                logger.info(f"[EnhancedMemory] 🧠 记庆 FAISS 索引已加载 "
                    f"({self._memory_rag.stats().get('doc_count', 0)} chunks)")
            except Exception as e:
                logger.warning(f"[EnhancedMemory] ⚠️  FAISS 记庆索引初始化失败: {e}")
                self._memory_rag = False  # 哨兵局量：不再重试
                return None
        return self._memory_rag

    def _rebuild_memory_rag_if_needed(self):
        """
        若 FAISS 记庆索引为空但 memories.json 有数据，
        执行一次性迁移重建（异步不阻塞）。
        """
        if not self.memories:
            return
        import threading

        def _rebuild():
            try:
                rag = self._get_memory_rag()
                if rag is None:
                    return
                stats = rag.stats()
                if stats.get("initialized") and stats.get("doc_count", 0) > 0:
                    return  # 已有索引，无需重建
                logger.info(f"[EnhancedMemory] 🔨 首次构建记庆向量索引（{len(self.memories)} 条）...")
                for m in self.memories:
                    content = (m.get("content") or "").strip()
                    mem_id = m.get("id", 0)
                    if content:
                        rag.index_text(content, source=f"mem_{mem_id}")
                logger.info(f"[EnhancedMemory] ✅ 记庆向量索引构建完成")
            except Exception as e:
                logger.warning(f"[EnhancedMemory] ⚠️  记庆向量索引重建失败: {e}")

        threading.Thread(target=_rebuild, daemon=True).start()

    def _is_duplicate(self, content: str, threshold: float = 0.85) -> bool:
        """检查是否与已有记忆重复（Jaccard相似度）"""
        content_lower = content.lower().strip()
        set_a = set(content_lower.split())
        if not set_a:
            return False
        for m in self.memories:
            existing = m.get("content", "").lower().strip()
            if existing == content_lower:
                return True
            set_b = set(existing.split())
            if not set_b:
                continue
            intersection = len(set_a & set_b)
            union = len(set_a | set_b)
            if union > 0 and intersection / union >= threshold:
                return True
        return False

    # ── 记忆生命周期管理（MemGovernance GC）──────────────────────────────────────

    def run_gc(self) -> None:
        """异步启动记忆生命周期 GC（不阻塞主线程）。"""
        import threading
        threading.Thread(target=self._gc_stale, daemon=True).start()

    def _gc_stale(self) -> int:
        """
        修剪过期 / 超限记忆，返回清理条数。

        规则 1 — 过期清理：
          source != 'user' 且 use_count == 0 且创建超过 _GC_STALE_DAYS 天
          → 从未被引用的自动提取记忆，直接删除。

        规则 2 — 超限清理：
          单个 category 超过 _GC_MAX_PER_CATEGORY 条时，
          优先保留：① user-sourced ② 创建时间最新，超出部分删除。
        """
        if not self.memories:
            return 0

        now = datetime.now()
        stale_cutoff = now - timedelta(days=_GC_STALE_DAYS)

        def _is_stale(m: Dict) -> bool:
            if m.get("source") == "user":
                return False  # 用户手动添加的记忆永不主动删除
            if m.get("use_count", 0) > 0:
                return False  # 被访问过 → 保留
            try:
                created = datetime.fromisoformat(m.get("created_at", ""))
                return created < stale_cutoff
            except Exception:
                return False

        before = len(self.memories)
        self.memories = [m for m in self.memories if not _is_stale(m)]
        removed_stale = before - len(self.memories)

        # 规则 2：单类别上限
        by_cat: Dict[str, List] = defaultdict(list)
        for m in self.memories:
            by_cat[m.get("category", "general")].append(m)

        keep: List[Dict] = []
        for cat, items in by_cat.items():
            if len(items) <= _GC_MAX_PER_CATEGORY:
                keep.extend(items)
                continue
            # user-sourced first, then newest created_at
            items.sort(
                key=lambda x: (x.get("source") == "user", x.get("created_at", "")),
                reverse=True,
            )
            keep.extend(items[:_GC_MAX_PER_CATEGORY])

        removed_overflow = len(self.memories) - len(keep)
        self.memories = keep

        total = removed_stale + removed_overflow
        if total:
            self._save()
            print(
                f"[EnhancedMemory] 🗑️  GC: 清理 {removed_stale} 条过期"
                f" + {removed_overflow} 条超限 = {total} 条，"
                f"剩余 {len(self.memories)} 条"
            )
        return total

    def add_memory(self, content: str, category: str = "user_preference",
                   source: str = "user", metadata: Optional[Dict] = None) -> Optional[Dict]:
        """添加记忆（含去重检查）"""
        content = (content or "").strip()
        if not content:
            return None

        # 去重：跳过与现有记忆高度相似的条目
        if self._is_duplicate(content):
            logger.info(f"[EnhancedMemory] ♻️  跳过重复记忆: {content[:40]}...")
            return None

        item = {
            "id": int(time.time() * 1000),
            "content": content,
            "category": category,
            "source": source,
            "created_at": datetime.now().isoformat(),
            "use_count": 0,
            "last_accessed": datetime.now().isoformat(),
            "metadata": metadata or {},
        }

        self.memories.append(item)
        self._save()

        # 同步写入专用 FAISS 记庆索引
        try:
            rag = self._get_memory_rag()
            if rag is not None:
                rag.index_text(item["content"], source=f"mem_{item['id']}")
        except Exception:
            pass  # 向量索引失败不影响主路径

        # 同步写入向量记庆（原有 numpy 路径）
        self.add_vector_memory(
            content=item["content"], metadata={"category": category, "source": source}
        )

        logger.info(f"[EnhancedMemory] ➕ 新记忆: {content[:50]}...")
        return item

    def _build_extraction_prompt(self, user_msg: str, ai_msg: str) -> str:
        """构建记忆提取Prompt（精炼版）"""
        return (
            "分析以下对话，提取值得长期记住的用户特征和偏好。\n\n"
            f"用户：{user_msg[:500]}\n"
            f"AI：{ai_msg[:300]}\n\n"
            "以JSON格式返回（只返回JSON，无其他文字）：\n"
            "{\n"
            '  "programming_languages": [],\n'
            '  "tools": [],\n'
            '  "domains": [],\n'
            '  "likes": [],\n'
            '  "dislikes": [],\n'
            '  "communication_style": {},\n'
            '  "memories_to_save": [\n'
            '    {"content": "...", "category": "user_preference"}\n'
            "  ]\n"
            "}\n\n"
            "规则：\n"
            "1. 只提取明确、重要、可复用的信息\n"
            "2. 忽略临时性问题和闲聊\n"
            "3. memories_to_save 每条 content 需是完整、可独立理解的短句\n"
            "4. 如无值得记录的内容，所有列表返回空\n"
            "5. 只返回JSON，不要解释"
        )

    def _keyword_extract(self, user_msg: str, extracted: Dict):
        """关键词提取（降级方案，无LLM时使用）"""
        user_lower = user_msg.lower()
        lang_keywords = {
            "python": ["python", "py"],
            "javascript": ["javascript", "js", "node"],
            "java": ["java"],
            "c++": ["c++", "cpp"],
            "go": ["golang", "go语言"],
            "rust": ["rust"],
            "typescript": ["typescript", "ts"],
        }
        for lang, keywords in lang_keywords.items():
            if any(kw in user_lower for kw in keywords):
                if (
                    lang
                    not in self.user_profile.profile["technical_background"][
                        "programming_languages"
                    ]
                ):
                    extracted["profile_updates"].setdefault(
                        "programming_languages", []
                    ).append(lang)

        if any(w in user_lower for w in ["喜欢", "prefer", "倾向", "更喜欢"]):
            if "简洁" in user_lower or "简单" in user_lower:
                extracted["profile_updates"]["communication_style"] = {
                    "preferred_detail_level": "brief"
                }

        if extracted["profile_updates"]:
            self.user_profile.update_from_extraction(extracted["profile_updates"])
            logger.info(f"[EnhancedMemory] 🔄 关键词学习：{list(extracted['profile_updates'].keys())}")

    def auto_extract_from_conversation(
        self, user_msg: str, ai_msg: str, history: Optional[List] = None
    ) -> Dict:
        """从对话中自动提取记忆，LLM优先，关键词降级"""
        extracted = {"memories": [], "profile_updates": {}}

        if self._generate_fn is not None:
            prompt = self._build_extraction_prompt(user_msg, ai_msg)
            try:
                raw = self._generate_fn(prompt, temperature=0.1, max_tokens=500)
                raw = (raw or "").strip()
                if raw.startswith("```json"):
                    raw = raw.split("```json")[1].split("```")[0]
                elif raw.startswith("```"):
                    raw = raw.split("```")[1].split("```")[0]
                data = json.loads(raw.strip())

                # 保存显式记忆（去重）
                for mem in data.get("memories_to_save", []):
                    content = (mem.get("content") or "").strip()
                    if content and len(content) > 5 and not self._is_duplicate(content):
                        self.add_memory(
                            content,
                            category=mem.get("category", "general"),
                            source="extraction",
                        )
                        extracted["memories"].append(content)

                # 更新用户画像
                for key in [
                    "programming_languages",
                    "tools",
                    "domains",
                    "likes",
                    "dislikes",
                    "communication_style",
                ]:
                    if data.get(key):
                        extracted["profile_updates"][key] = data[key]

                if extracted["profile_updates"]:
                    self.user_profile.update_from_extraction(
                        extracted["profile_updates"]
                    )
                    logger.info(f"[EnhancedMemory] 🔄 LLM学习：{list(extracted['profile_updates'].keys())}")

            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"[EnhancedMemory] ⚠️  LLM提取失败，降级关键词: {e}")
                self._keyword_extract(user_msg, extracted)
        else:
            self._keyword_extract(user_msg, extracted)

        return extracted

    def _format_history_chunk(self, history_chunk: List[Dict]) -> str:
        """将历史对话片段格式化为摘要输入"""
        lines = []
        for msg in history_chunk:
            role = "用户" if msg.get("role") == "user" else "助手"
            content = (msg.get("parts") or [""])[0]
            if content:
                content = content.replace("\n", " ")
                lines.append(f"{role}: {content[:240]}")
        return "\n".join(lines)

    def get_or_update_summary(
        self, session_name: str, history: List[Dict], max_turns: int = 20
    ) -> str:
        """生成或更新对话摘要（滑动窗口外）"""
        if not session_name or not history:
            return ""

        if len(history) <= max_turns:
            entry = self.summaries.get(session_name, {})
            return entry.get("summary", "")

        entry = self.summaries.get(session_name, {"summary": "", "last_index": 0})
        summary = entry.get("summary", "")
        last_index = int(entry.get("last_index", 0))
        new_index = max(0, len(history) - max_turns)

        if new_index <= last_index:
            return summary

        if self._generate_fn is None:
            return summary

        chunk = history[last_index:new_index]
        chunk_text = self._format_history_chunk(chunk)
        if not chunk_text.strip():
            return summary

        prompt = (
            "你是对话摘要器。请将新增对话片段合并到现有摘要中，输出精炼、可复用的摘要。\n\n"
            f"现有摘要：\n{summary or '（无）'}\n\n"
            f"新增对话：\n{chunk_text}\n\n"
            "请输出更新后的摘要（中文，100-200字左右）。只输出摘要内容，不要解释。"
        )

        try:
            new_summary = self._generate_fn(prompt, temperature=0.2, max_tokens=300)
            new_summary = (new_summary or "").strip()
            if new_summary:
                self.summaries[session_name] = {
                    "summary": new_summary,
                    "last_index": new_index,
                    "updated_at": datetime.now().isoformat(),
                }
                self._save_summaries()
                self._index_summary(session_name, new_summary)
                return new_summary
        except Exception as e:
            logger.info(f"[EnhancedMemory] 摘要更新失败: {e}")

        return summary

    def _index_summary(self, session_name: str, summary: str):
        """将摘要写入向量记忆（如果可用）"""
        if not summary:
            return
        self.add_vector_memory(
            content=summary, metadata={"session": session_name, "category": "summary"}
        )

    def add_vector_memory(self, content: str, metadata: Optional[Dict] = None):
        """添加向量记忆"""
        if not content or self._embedding_fn is None:
            return None

        try:
            embeddings = self._embedding_fn([content])
            if not embeddings:
                return None
            vector = embeddings[0]
            item = {
                "id": int(time.time() * 1000),
                "content": content.strip(),
                "embedding": vector,
                "metadata": metadata or {},
                "created_at": datetime.now().isoformat(),
            }
            self.vector_memories.append(item)
            self._save_vectors()
            return item
        except Exception as e:
            logger.info(f"[EnhancedMemory] 向量写入失败: {e}")
            return None

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        a_vec = np.array(a, dtype=np.float32)
        b_vec = np.array(b, dtype=np.float32)
        denom = np.linalg.norm(a_vec) * np.linalg.norm(b_vec)
        if denom == 0:
            return 0.0
        return float(np.dot(a_vec, b_vec) / denom)

    def search_vector_memories(self, query: str, limit: int = 5) -> List[Dict]:
        """向量检索相关记庆（FAISS 优先，再除降级到 numpy cosine）"""
        if not query:
            return []

        # ── FAISS 路径（推荐：无需 _embedding_fn）─────────────────────────────
        try:
            rag = self._get_memory_rag()
            if rag is not None and rag.stats().get("initialized"):
                faiss_hits = rag.hybrid_retrieve(query, k=limit, score_threshold=0.3)
                if faiss_hits:
                    results: List[Dict] = []
                    for hit in faiss_hits:
                        src = hit.get("source", "")
                        matched = None
                        if src.startswith("mem_"):
                            try:
                                mem_id = int(src.split("_", 1)[1])
                                matched = next(
                                    (m for m in self.memories if m["id"] == mem_id),
                                    None,
                                )
                            except (ValueError, IndexError):
                                pass
                        if matched is None:
                            # 根据内容匹配回单原始记庆对象
                            hit_content = hit.get("content", "")
                            matched = next(
                                (
                                    m
                                    for m in self.memories
                                    if m.get("content", "").strip() in hit_content
                                    or hit_content in m.get("content", "")
                                ),
                                None,
                            )
                        if matched and matched not in results:
                            results.append(matched)
                    if results:
                        return results[:limit]
        except Exception as _fe:
            pass  # FAISS 失败 → 降级

        # ── 降级：原有 numpy cosine 路径 ──────────────────────────────
        if self._embedding_fn is None or not self.vector_memories:
            return []

        try:
            embeddings = self._embedding_fn([query])
            if not embeddings:
                return []
            query_vec = embeddings[0]
            scored = []
            for item in self.vector_memories:
                score = self._cosine_similarity(query_vec, item.get("embedding", []))
                scored.append((score, item))
            scored.sort(key=lambda x: x[0], reverse=True)
            results = [i for s, i in scored[:limit] if s > 0.2]
            return results
        except Exception as e:
            logger.info(f"[EnhancedMemory] 向量检索失败: {e}")
            return []
    
    def search_memories(self, query: str, limit: int = 5,
                        boost_categories: Optional[List[str]] = None) -> List[Dict]:
        """搜索相关记忆（置信度感知 + 类别优先 + 关键词匹配）"""
        if not query:
            return []

        query_lower = query.lower()
        scored = []
        keywords = [k for k in query_lower.split() if len(k) > 1]

        for m in self.memories:
            # ── 置信度过滤：跳过低置信度的自动提取记忆 ──────────────────────
            conf = float((m.get("metadata") or {}).get("confidence", 1.0))
            if conf < 0.4 and m.get("source") != "user":
                continue

            content_lower = m["content"].lower()
            score = 0

            # ── 置信度加权 ────────────────────────────────────────────────────
            if conf >= 0.85:
                score += 2
            elif conf >= 0.6:
                score += 1

            # ── Cube 优先：任务类型对应的高价值分类 ──────────────────────────
            cat = m.get("category", "")
            if boost_categories and cat in boost_categories:
                score += 3

            # ── 固定分类权重 ──────────────────────────────────────────────────
            if cat == "user_preference":
                score += 3
            elif cat in ("correction", "decision", "reminder"):
                score += 2

            # ── 内容匹配 ──────────────────────────────────────────────────────
            if query_lower in content_lower:
                score += 5
            for kw in keywords:
                if kw in content_lower:
                    score += 1

            # 要求至少 2 分（单个关键词命中 = 1 分，不足以证明相关性）
            # 有效命中：类别加权(+2/+3) 或 2+ 个关键词同时匹配
            if score > 1:
                scored.append((score, m))

        scored.sort(key=lambda x: (x[0], x[1]["created_at"]), reverse=True)

        results = [item[1] for item in scored[:limit]]
        for m in results:
            m["use_count"] = m.get("use_count", 0) + 1

        if results:
            self._save()

        return results

    def get_context_string(
        self,
        user_input: str,
        session_name: Optional[str] = None,
        history: Optional[List] = None,
    ) -> str:
        """
        四层记忆注入：
          L0 个人记忆矩阵（认知风格/专长/目标/价值观，持续更新）
          L1 用户画像（全量注入，稳定偏好）
          L2 长期记忆（FAISS 语义优先，关键词降级，去重）
             — FAISS 路径不依赖 _embedding_fn（已修复原有误判条件）
          L3 历史摘要（仅超出滑动窗口时注入）
        """
        lines = []

        # ── L0: 个人记忆矩阵（最动态，每轮后台更新）──
        matrix_ctx = self.personality_matrix.to_context_string()
        if matrix_ctx:
            lines.append(matrix_ctx)

        # ── L1: 用户画像（稳定偏好，每次全量注入）──
        profile_context = self.user_profile.to_context_string().strip()
        if profile_context:
            lines.append(profile_context)

        # ── L2: 长期记忆（FAISS 语义优先，关键词降级，去重）──
        seen_contents: set = set()
        memory_lines: list = []

        # FAISS 语义检索不需要 _embedding_fn；直接尝试，无结果再降级
        vector_hits = self.search_vector_memories(user_input, limit=5)
        for m in vector_hits:
            content = m.get("content", "")
            if content and len(content) < 300 and content not in seen_contents:
                seen_contents.add(content)
                memory_lines.append(f"• {content}")

        if not memory_lines:
            # FAISS 无结果 → 降级关键词检索
            kw_hits = self.search_memories(user_input, limit=4)
            for m in kw_hits:
                content = m.get("content", "")
                if content and len(content) < 200 and content not in seen_contents:
                    seen_contents.add(content)
                    memory_lines.append(f"• {content}")

        if memory_lines:
            lines.append("\n[相关记忆]")
            lines.extend(memory_lines)

        # ── L3: 历史摘要（超出滑动窗口后才注入）──
        if session_name and history and len(history) > 20:
            summary = self.summaries.get(session_name, {}).get("summary", "")
            if summary:
                lines.append(f"\n[历史对话摘要]\n{summary}")

        return "\n".join(lines) if lines else ""

    def _recency_weight(self, iso_ts: str) -> float:
        """指数衰减时效权重：24h内≈1.0，7天≈0.7，30天≈0.4"""
        if not iso_ts:
            return 0.5
        try:
            dt = datetime.fromisoformat(iso_ts)
            age_hours = (datetime.now() - dt).total_seconds() / 3600
            return max(0.2, math.exp(-age_hours / (7 * 24)))
        except Exception:
            return 0.5

    def update_personality_async(self, user_msg: str, ai_msg: str, llm_fn):
        """非阻塞触发 PersonalityMatrix 更新，在 _start_memory_extraction 中调用。"""
        PersonalityMatrix.update_async(user_msg, ai_msg, llm_fn, self.personality_matrix)

    def get_compact_memory_snapshot(self, max_chars: int = 200) -> str:
        """返回适合注入本地模型（Ollama）上下文的精简记忆摘要（≤max_chars字符）。
        从 PersonalityMatrix 提取最有价值的维度，避免撑爆本地模型的短上下文窗口。"""
        mx = self.personality_matrix.data
        parts = []

        # 主导认知风格
        cog = mx.get("cognitive", {})
        if cog:
            dominant = max(cog, key=lambda k: cog[k])
            if cog[dominant] > 0.55:
                labels = {
                    "exploratory": "探索", "executor": "执行",
                    "analytical": "分析", "creative": "创意",
                }
                parts.append(f"风格:{labels.get(dominant, dominant)}")

        # 前2专长领域
        expertise = mx.get("expertise", {})
        if expertise:
            top2 = sorted(expertise.items(), key=lambda x: x[1], reverse=True)[:2]
            parts.append(f"擅长:{','.join(t[0] for t in top2)}")

        # 最近目标
        goals = [g for g in mx.get("goals", []) if g]
        if goals:
            parts.append(f"目标:{goals[-1][:20]}")

        # 最近主题
        themes = mx.get("recent_themes", [])
        if themes:
            parts.append(f"近期:{themes[-1][:10]}")

        if not parts:
            return ""
        result = " | ".join(parts)
        return result[:max_chars]

    def get_all_memories(self) -> List[Dict]:
        """获取所有记忆"""
        return sorted(self.memories, key=lambda x: x["created_at"], reverse=True)

    def delete_memory(self, memory_id: int) -> bool:
        """删除记忆"""
        initial_len = len(self.memories)
        self.memories = [m for m in self.memories if m["id"] != memory_id]

        if len(self.memories) < initial_len:
            self._save()
            return True
        return False

    def get_profile(self) -> Dict:
        """获取用户画像"""
        return self.user_profile.profile

    def update_profile_manually(self, updates: Dict):
        """手动更新用户画像"""
        self.user_profile.profile.update(updates)
        self.user_profile.save()


# 向后兼容的别名
MemoryManager = EnhancedMemoryManager


if __name__ == "__main__":
    # 测试
    logger.info("=" * 60)
    logger.info("  增强记忆管理器测试")
    logger.info("=" * 60)

    mgr = EnhancedMemoryManager()

    # 测试添加记忆
    mgr.add_memory("用户喜欢简洁的代码，不要太多注释", category="user_preference")
    mgr.add_memory("项目名称：Koto AI助手", category="project_info")

    # 测试自动提取
    mgr.auto_extract_from_conversation(
        "我在用Python开发一个Web应用", "好的，我可以帮你..."
    )

    # 测试搜索
    results = mgr.search_memories("代码")
    logger.info(f"\n搜索结果：{len(results)} 条")

    # 显示用户画像
    logger.info(mgr.user_profile.to_context_string())

    logger.info("\n✅ 测试完成")

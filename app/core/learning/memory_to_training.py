# -*- coding: utf-8 -*-
"""
memory_to_training.py — 记忆系统 → 训练数据桥接器
===================================================

职责：
  1. 从 config/memory.json 提取高置信度记忆 → 生成知识探针 QA 训练样本
     （让本地模型"学会认识这个用户"）
  2. 从 config/user_profile.json 生成个性化对话系统提示
     （让训练样本的 system 字段注入真实用户画像）
  3. 从 RatingStore 高质量样本 → 生成带个性化 system 的对话训练样本
     （让本地模型学会个性化响应风格）

产出样本类型：
  - source="memory_probe"   — 测试模型是否记住用户基本信息
  - source="rated_dialogue" — 用户+模型双重认可的高质量对话
  - source="profile_style"  — 风格迁移（学习用户偏好的回复方式）

使用方式（由 TrainingDataBuilder 调用）：
    from app.core.learning.memory_to_training import MemoryToTraining
    samples = MemoryToTraining.build_samples()   # List[dict] with TrainingSample-compatible keys
    system  = MemoryToTraining.get_personalized_system_prompt()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 路径 ─────────────────────────────────────────────────────────────────────
import sys as _sys

def _base_dir() -> Path:
    if getattr(_sys, "frozen", False):
        return Path(_sys.executable).parent
    return Path(__file__).resolve().parents[3]

_BASE          = _base_dir()
_MEMORY_PATH   = _BASE / "config" / "memory.json"
_PROFILE_PATH  = _BASE / "config" / "user_profile.json"

# ── 常量 ─────────────────────────────────────────────────────────────────────
_MIN_MEMORY_CONFIDENCE = 0.70    # 低于此置信度的记忆不转为训练样本
_MIN_MEMORY_LEN        = 10     # 过短的记忆跳过
_MAX_PROBE_SAMPLES     = 60     # 探针样本上限（防止过拟合）

_ROUTER_SYSTEM = (
    "你是 Koto AI 的任务路由分类器。"
    "根据用户输入判断任务类型，严格只输出 JSON: {\"task\":\"TYPE\",\"confidence\":0.9}\n"
    "可用类型: CHAT CODER PAINTER FILE_GEN DOC_ANNOTATE RESEARCH WEB_SEARCH FILE_SEARCH SYSTEM AGENT"
)

# 知识探针：测试模型能否正确回忆用户信息
_PROBE_QA_TEMPLATES: List[tuple] = [
    # (category, question_template, answer_template)
    ("user_fact",    "你还记得我的职业或背景吗？",      "根据我们之前的对话，{content}"),
    ("preference",  "我有什么偏好或习惯是你需要注意的？", "你之前提到过，{content}"),
    ("decision",    "我们之前达成了什么重要决定？",     "你做出了一个决定：{content}"),
    ("reminder",    "有什么我请你记住的事情吗？",       "你让我记住：{content}"),
    ("topic_summary","我们最近讨论过什么重要话题？",    "我们讨论过：{content}"),
]

_CATEGORY_TO_TEMPLATE = {t[0]: t for t in _PROBE_QA_TEMPLATES}


# ══════════════════════════════════════════════════════════════════════════════

class MemoryToTraining:
    """记忆 → 训练样本的转换器（所有方法为 classmethods / staticmethods）。"""

    # ── 公开入口 ─────────────────────────────────────────────────────────────

    @classmethod
    def build_samples(
        cls,
        min_rating_combined: float = 0.75,
        include_probes: bool = True,
        include_rated: bool = True,
        include_style: bool = True,
        verbose: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        构建所有衍生训练样本。

        Returns:
            List of dicts, each compatible with TrainingSample fields:
            {system, user, assistant, task_type, source, quality, metadata}
        """
        samples: List[Dict[str, Any]] = []

        # 1. 知识探针样本
        if include_probes:
            probes = cls._build_memory_probes()
            samples.extend(probes)
            if verbose:
                print(f"[MemoryToTraining] 🧠 记忆探针样本: {len(probes)} 条")

        # 2. 高质量评分对话样本
        if include_rated:
            rated = cls._build_rated_dialogue_samples(min_rating_combined)
            samples.extend(rated)
            if verbose:
                print(f"[MemoryToTraining] ⭐ 高质量评分样本: {len(rated)} 条")

        # 3. 风格迁移样本（从 shadow_traces 中取最高分的，注入个性化 system）
        if include_style:
            style = cls._build_style_samples()
            samples.extend(style)
            if verbose:
                print(f"[MemoryToTraining] 🎨 个性化风格样本: {len(style)} 条")

        return samples

    @classmethod
    def get_personalized_system_prompt(cls, base_prompt: Optional[str] = None) -> str:
        """
        返回注入了用户画像的系统提示。
        用于替换 training_data_builder 中的 _CHAT_SYSTEM，让训练样本
        「天然携带」用户个人化上下文。
        """
        profile = cls._load_profile()
        profile_block = cls._profile_to_prompt_block(profile)

        if base_prompt is None:
            base_prompt = (
                "你是 Koto，一个基于 Gemini 的本地 AI 助手。\n"
                "你擅长中英文对话、代码编写、文档处理和系统操作。\n"
                "请直接、简洁地回答用户问题。"
            )
        if profile_block:
            return base_prompt + "\n\n" + profile_block
        return base_prompt

    # ── 加载工具 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _load_memories() -> List[Dict[str, Any]]:
        """从 config/memory.json 加载记忆列表，容错处理。"""
        if not _MEMORY_PATH.exists():
            return []
        try:
            data = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("memories", [])
        except Exception as e:
            logger.debug(f"[MemToTrain] load memories failed: {e}")
        return []

    @staticmethod
    def _load_profile() -> Dict[str, Any]:
        """从 config/user_profile.json 加载用户画像。"""
        if not _PROFILE_PATH.exists():
            return {}
        try:
            return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"[MemToTrain] load profile failed: {e}")
            return {}

    @staticmethod
    def _profile_to_prompt_block(profile: Dict[str, Any]) -> str:
        """将用户画像转为 system prompt 片段（简洁，不超过 300 字）。"""
        if not profile:
            return ""
        parts: List[str] = ["[用户个性化上下文]"]

        style = profile.get("communication_style", {})
        if style.get("preferred_detail_level"):
            parts.append(f"• 回复详细度偏好：{style['preferred_detail_level']}")
        if style.get("formality"):
            parts.append(f"• 语气风格：{style['formality']}")

        tech = profile.get("technical_background", {})
        langs = tech.get("programming_languages", [])[:4]
        if langs:
            parts.append(f"• 主要编程语言：{', '.join(langs)}")
        level = tech.get("experience_level", "")
        if level:
            parts.append(f"• 技术水平：{level}")
        domains = tech.get("domains", [])[:3]
        if domains:
            parts.append(f"• 专业领域：{', '.join(domains)}")

        prefs = profile.get("preferences", {})
        dislikes = prefs.get("dislikes", [])[:2]
        if dislikes:
            parts.append(f"• 不喜欢：{', '.join(dislikes)}")

        if len(parts) <= 1:
            return ""
        return "\n".join(parts)

    # ── 样本构建 ─────────────────────────────────────────────────────────────

    @classmethod
    def _build_memory_probes(cls) -> List[Dict[str, Any]]:
        """
        生成记忆探针 QA 样本。
        每条高置信度记忆 → 一个 (question, answer) 训练对，
        教本地模型「知道这个用户的具体信息」。
        """
        memories = cls._load_memories()
        samples: List[Dict[str, Any]] = []
        personalized_sys = cls.get_personalized_system_prompt()

        used = 0
        for mem in memories:
            if used >= _MAX_PROBE_SAMPLES:
                break

            content  = str(mem.get("content", "")).strip()
            category = str(mem.get("category", "user_fact")).strip()
            conf_raw = mem.get("confidence")
            # confidence 可能嵌在 metadata
            if conf_raw is None:
                conf_raw = mem.get("metadata", {}).get("confidence", 0.0)
            confidence = float(conf_raw) if conf_raw is not None else 0.0

            if len(content) < _MIN_MEMORY_LEN or confidence < _MIN_MEMORY_CONFIDENCE:
                continue

            tmpl = _CATEGORY_TO_TEMPLATE.get(category, _CATEGORY_TO_TEMPLATE.get("user_fact"))
            if tmpl is None:
                continue
            _, question, answer_tmpl = tmpl

            answer = answer_tmpl.format(content=content)

            samples.append({
                "system":    personalized_sys,
                "user":      question,
                "assistant": answer,
                "task_type": "CHAT",
                "source":    "memory_probe",
                "quality":   min(1.0, confidence * 1.05),  # 轻微上浮奖励
                "metadata":  {"memory_category": category, "confidence": confidence},
            })
            used += 1

        return samples

    @classmethod
    def _build_rated_dialogue_samples(
        cls,
        min_combined: float = 0.75,
    ) -> List[Dict[str, Any]]:
        """
        从 RatingStore 提取双轨高分对话 → 训练样本。
        system 注入个性化画像，让模型学习如何在了解用户偏好的情况下回复。
        """
        try:
            from app.core.learning.rating_store import get_rating_store
            rs = get_rating_store()
            rows = rs.get_high_quality_samples(min_combined=min_combined)
        except Exception as e:
            logger.debug(f"[MemToTrain] get_high_quality_samples failed: {e}")
            return []

        personalized_sys = cls.get_personalized_system_prompt()
        samples: List[Dict[str, Any]] = []

        for row in rows:
            ui = str(row.get("user_input", "")).strip()
            ar = str(row.get("ai_response", "")).strip()
            if len(ui) < 5 or len(ar) < 15:
                continue

            samples.append({
                "system":    personalized_sys,
                "user":      ui,
                "assistant": ar,
                "task_type": str(row.get("task_type", "CHAT")),
                "source":    "rated_dialogue",
                "quality":   float(row.get("combined_score", 0.75)),
                "metadata":  {
                    "stars":         row.get("stars"),
                    "model_overall": row.get("model_overall"),
                    "msg_id":        row.get("msg_id"),
                },
            })

        return samples

    @classmethod
    def _build_style_samples(cls) -> List[Dict[str, Any]]:
        """
        从 shadow_traces 最高质量记录中提取，注入个性化 system prompt。
        相比原始 TrainingDataBuilder._load_shadow_traces()，差异在于
        system 字段携带了真实用户画像，使本地模型学会「有上下文的回复」。
        """
        import sys
        _base = _base_dir()
        shadow_dir = _base / "workspace" / "shadow_traces"
        if not shadow_dir.exists():
            return []

        personalized_sys = cls.get_personalized_system_prompt()
        if not personalized_sys:
            return []

        # 只有当画像非空（比基础 system 长）时才生成风格样本
        base_len = len(
            "你是 Koto，一个基于 Gemini 的本地 AI 助手。\n"
            "你擅长中英文对话、代码编写、文档处理和系统操作。\n"
            "请直接、简洁地回答用户问题。"
        )
        if len(personalized_sys) <= base_len + 10:
            return []

        samples: List[Dict[str, Any]] = []
        for trace_file in sorted(shadow_dir.glob("*.jsonl"), reverse=True)[:5]:
            if trace_file.name.startswith("_"):
                continue
            try:
                lines = trace_file.read_text(encoding="utf-8").splitlines()
                for line in lines[-30:]:   # 最近 30 条
                    rec = json.loads(line)
                    ui = str(rec.get("user_input", "")).strip()
                    ar = str(rec.get("ai_response", "")).strip()
                    if len(ui) < 5 or len(ar) < 20:
                        continue
                    samples.append({
                        "system":    personalized_sys,
                        "user":      ui,
                        "assistant": ar,
                        "task_type": str(rec.get("task_type", "CHAT")),
                        "source":    "profile_style",
                        "quality":   0.82,
                        "metadata":  {"skill_id": rec.get("skill_id")},
                    })
            except Exception as e:
                logger.debug(f"[MemToTrain] style trace read failed ({trace_file.name}): {e}")

        return samples

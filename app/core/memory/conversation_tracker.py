"""
ConversationTracker — 多轮对话语义上下文跟踪器

每个会话维护一个紧凑的上下文快照（persisted to chats/<id>.tracker.json），
包含：
  - active_topic:             当前对话主题（供系统指令注入）
  - recent_entities:          近期出现的实体名词（供指代词解析）
  - last_response_key_points: 上轮回复的关键要点（供"上面那个"类指代解析）
  - accumulated_facts:        会话中确立的关键事实（最多10条）
  - turn_count:               本会话已有的对话轮数

用途：
  1. /chat 路由将 get_context_injection() 注入到 system_instruction，
     使模型感知当前对话的语境。
  2. IntentAnalyzer 可查询 tracker 来解析 "上面第3点"、"那个方案" 等指代。
  3. 对话历史压缩时提供 topic anchor，确保摘要保留核心实体。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 每条要点最大字数
_MAX_POINT_LEN = 120
# 最多保留的实体数量
_MAX_ENTITIES = 10
# 最多保留的已确立事实
_MAX_FACTS = 10
# 最多保留的上轮要点数
_MAX_KEY_POINTS = 5


# ── 轻量级规则抽取工具 ─────────────────────────────────────────────────────────

_TOPIC_KEYWORD_MAP = {
    "天气": ["天气", "气温", "下雨", "晴", "风", "温度"],
    "代码": ["代码", "函数", "报错", "bug", "python", "javascript", "程序", "脚本"],
    "文件": ["文件", "文档", "excel", "word", "pdf", "csv", "读取", "写入"],
    "搜索": ["搜索", "查找", "找一下", "有没有", "最新"],
    "计划": ["计划", "方案", "大纲", "步骤", "流程"],
    "翻译": ["翻译", "translate", "中文", "英文", "日文"],
    "数学": ["计算", "数学", "公式", "方程", "结果"],
    "系统": ["cpu", "内存", "磁盘", "进程", "系统", "硬件"],
}


def _detect_topic(text: str) -> Optional[str]:
    """Rule-based lightweight topic detection from user input."""
    t = text.lower()
    for topic, keywords in _TOPIC_KEYWORD_MAP.items():
        if any(kw in t for kw in keywords):
            return topic
    return None


def _extract_entities(text: str) -> List[str]:
    """
    Extract likely entity names from a response:
    - Quoted content: 「...」《...》"..."
    - Chinese proper-noun-like sequences (2-6 char, uppercase-initial or CJK)
    - ASCII capitalized words (product names, file names)
    - Numbers with units (30元, 5GB, 2小时)
    """
    entities: List[str] = []
    # Quoted single-char to 20-char sequences
    for pat in [
        r"「([^「」]{2,20})」",
        r"《([^《》]{1,20})》",
        r'"([^"]{2,20})"',
        r"'([^']{2,20})'",
        r"`([^`]{2,20})`",
    ]:
        entities += re.findall(pat, text)
    # Capitalized ASCII words (at least 2 chars)
    entities += re.findall(r"\b[A-Z][a-zA-Z0-9_-]{1,30}\b", text)
    # Number+unit patterns
    entities += re.findall(
        r"\d+(?:\.\d+)?(?:元|GB|MB|KB|TB|小时|分钟|秒|度|米|km|kg|%)", text
    )
    # Deduplicate while preserving order
    seen: set = set()
    result: List[str] = []
    for e in entities:
        e = e.strip()
        if e and e not in seen and len(e) <= 30:
            seen.add(e)
            result.append(e)
    return result[:_MAX_ENTITIES]


def _extract_key_points(text: str) -> List[str]:
    """
    Extract key points from an AI response:
    - Numbered items: 1. xxx or 1、xxx
    - Bulleted items: - xxx or • xxx
    - First sentence as fallback
    """
    points: List[str] = []

    # Numbered list
    for m in re.finditer(
        r"(?:^|\n)\s*(?:\d+[.、\)）]|[•·▶→*-])\s*(.{5," + str(_MAX_POINT_LEN) + r"})",
        text,
    ):
        pt = m.group(1).strip().rstrip("。")
        if pt:
            points.append(pt[:_MAX_POINT_LEN])
        if len(points) >= _MAX_KEY_POINTS:
            break

    # First sentence fallback (Chinese sentence end or period)
    if not points:
        first_sent = re.split(r"[。！？\n]", text.strip(), maxsplit=1)[0].strip()
        if 10 <= len(first_sent) <= _MAX_POINT_LEN:
            points.append(first_sent)

    return points[:_MAX_KEY_POINTS]


# ── 主类 ──────────────────────────────────────────────────────────────────────


class ConversationTracker:
    """Per-session conversation state tracker for multi-turn semantic continuity."""

    def __init__(self, state: Optional[Dict[str, Any]] = None):
        self.state: Dict[str, Any] = state or {
            "turn_count": 0,
            "active_topic": None,
            "recent_entities": [],
            "last_response_key_points": [],
            "accumulated_facts": [],
            "updated_at": None,
        }

    # ── Persistence ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls, tracker_path: str) -> "ConversationTracker":
        if os.path.exists(tracker_path):
            try:
                with open(tracker_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    return cls(state)
            except Exception as e:
                logger.debug(f"[Tracker] load failed ({tracker_path}): {e}")
        return cls()

    def save(self, tracker_path: str) -> None:
        try:
            with open(tracker_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"[Tracker] save failed ({tracker_path}): {e}")

    # ── Context Injection ────────────────────────────────────────────────────

    def get_context_injection(self) -> str:
        """
        Returns a short context block to prepend to system_instruction.
        Empty string if tracker has no useful state yet (< 1 turn).
        """
        if self.state.get("turn_count", 0) < 1:
            return ""

        lines: List[str] = ["[对话上下文]"]

        topic = self.state.get("active_topic")
        if topic:
            lines.append(f"当前话题：{topic}")

        entities = self.state.get("recent_entities") or []
        if entities:
            lines.append(f"近期提到：{', '.join(entities[:6])}")

        key_points = self.state.get("last_response_key_points") or []
        if key_points:
            lines.append("上轮回复要点：")
            for pt in key_points[:3]:
                lines.append(f"  • {pt}")

        if len(lines) == 1:
            return ""

        return "\n".join(lines)

    def get_last_response_summary(self) -> str:
        """Compact summary of last response for IntentAnalyzer reference resolution."""
        points = self.state.get("last_response_key_points") or []
        if not points:
            return ""
        return "；".join(p[:60] for p in points[:3])

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, user_input: str, ai_response: str) -> None:
        """
        Synchronous rule-based state update after a completed turn.
        Fast — no LLM call. Call this from the main thread after response ready.
        """
        import time

        self.state["turn_count"] = self.state.get("turn_count", 0) + 1

        # Topic
        new_topic = _detect_topic(user_input)
        if new_topic:
            self.state["active_topic"] = new_topic

        # Entities (from both user input and AI response, deduplicated)
        new_ents = _extract_entities(user_input) + _extract_entities(ai_response)
        existing = self.state.get("recent_entities") or []
        merged: List[str] = list(dict.fromkeys(new_ents + existing))
        self.state["recent_entities"] = merged[:_MAX_ENTITIES]

        # Key points from AI response
        key_points = _extract_key_points(ai_response)
        self.state["last_response_key_points"] = key_points

        # Accumulate important facts (simple: add key points if > 3 chars)
        facts = self.state.get("accumulated_facts") or []
        for pt in key_points[:2]:
            if len(pt) > 20 and pt not in facts:
                facts.append(pt)
        self.state["accumulated_facts"] = facts[-_MAX_FACTS:]

        self.state["updated_at"] = int(time.time())

    def update_async(self, user_input: str, ai_response: str, save_path: str) -> None:
        """Non-blocking update: run in background thread."""

        def _run():
            try:
                self.update(user_input, ai_response)
                self.save(save_path)
            except Exception as e:
                logger.debug(f"[Tracker] async_update failed: {e}")

        threading.Thread(target=_run, daemon=True).start()

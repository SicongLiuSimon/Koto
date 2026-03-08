# -*- coding: utf-8 -*-
"""
Koto ShadowWatcher — 影子追踪 · 长期用户观察引擎
=================================================
设计原则
--------
1. 零感知   — 在后台异步钩挂对话管道，不增加响应延迟
2. 轻量提取 — 无需 LLM，基于规则 + 频率统计完成观察
3. 用户控制 — enabled 开关，随时可关闭，关闭后立即停止所有写入
4. 隐私安全 — 不存储原始对话文本，只存储结构化摘要

观察维度
--------
- topics        : 话题词频（python / 数据分析 / …）
- active_hours  : 活跃小时分布（0-23）
- open_tasks    : 提到但可能未完成的任务
- recurring     : 高频请求模式（"帮我写" / "总结一下" / …）
- streak        : 连续使用天数
- last_seen     : 最后活跃时间

存储
----
config/shadow_observations.json  （人类可读，便于调试）
"""
from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 路径 ────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent.parent.parent / "config"
_OBS_FILE = _BASE / "shadow_observations.json"

# ── 话题关键词（扩展时只需在此添加） ──────────────────────────────────────────
_TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "Python":     ["python", "py", "脚本", "函数", "class", "import"],
    "数据分析":   ["数据", "分析", "图表", "统计", "pandas", "excel", "csv"],
    "写作":       ["写", "文章", "报告", "总结", "摘要", "翻译"],
    "编程":       ["代码", "bug", "调试", "函数", "api", "接口", "开发"],
    "学习":       ["学习", "理解", "解释", "概念", "原理", "教程"],
    "工作效率":   ["待办", "任务", "计划", "安排", "提醒", "日程"],
    "文件管理":   ["文件", "整理", "归档", "目录", "路径"],
    "Koto":       ["koto", "系统", "设置", "功能", "模型"],
}

# ── 开放任务检测模式 ─────────────────────────────────────────────────────────
_OPEN_TASK_PATTERNS = [
    r"(我|我需要|帮我|请帮我|记得|别忘了|待办[:：]\s*)(.{4,40})",
    r"(需要|打算|计划|准备|想要)\s*(.{4,40})",
    r"(后续|之后|下次|明天|下周)\s*(需要|要|去|做|完成)\s*(.{4,40})",
    r"TODO[：:\s]+(.{4,60})",
]

# ── 高频请求模式 ─────────────────────────────────────────────────────────────
_PHRASE_PATTERNS = [
    "帮我写", "帮我", "总结", "翻译", "解释", "分析", "生成", "优化",
    "检查", "修复", "整理", "创建", "查找", "比较",
]


# ============================================================================
# 数据结构
# ============================================================================

def _default_obs() -> Dict[str, Any]:
    return {
        "enabled": True,
        "last_updated": None,
        "total_observations": 0,
        "topics": {},
        "active_hours": {},        # "9": count, "14": count …
        "open_tasks": [],          # [{id, text, mentioned_at, session, done}]
        "recurring_phrases": {},   # phrase: count
        "last_seen": None,
        "streak": {"days": 0, "last_date": None},
        "work_pattern": {
            "avg_session_length": 0,
            "sessions_last_30d": 0,
        },
    }


# ============================================================================
# ShadowWatcher
# ============================================================================

class ShadowWatcher:
    """
    影子追踪器（单例）。
    外部调用: ShadowWatcher.observe(user_msg, ai_msg, session_id)
    """

    _instance: Optional["ShadowWatcher"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._obs_lock = threading.Lock()
        self._obs: Dict[str, Any] = _default_obs()
        self._load()

    # ── 单例 ──────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "ShadowWatcher":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    @classmethod
    def observe(cls, user_msg: str, ai_msg: str, session_id: str = "default"):
        """在对话完成后异步调用；若未启用则直接返回。"""
        watcher = cls.get()
        if not watcher._obs.get("enabled", True):
            return
        threading.Thread(
            target=watcher._process,
            args=(user_msg, ai_msg, session_id),
            daemon=True,
        ).start()

    @property
    def enabled(self) -> bool:
        return bool(self._obs.get("enabled", True))

    def set_enabled(self, value: bool):
        with self._obs_lock:
            self._obs["enabled"] = value
        self._save()
        logger.info("[ShadowWatcher] %s", "已启用" if value else "已禁用")

    def get_observations(self) -> Dict[str, Any]:
        with self._obs_lock:
            return dict(self._obs)

    def get_open_tasks(self) -> List[Dict]:
        with self._obs_lock:
            return [t for t in self._obs.get("open_tasks", []) if not t.get("done")]

    def dismiss_task(self, task_id: str):
        with self._obs_lock:
            for t in self._obs.get("open_tasks", []):
                if t["id"] == task_id:
                    t["done"] = True
                    break
        self._save()

    def reset(self):
        """清空所有观察数据（保留 enabled 设置）。"""
        enabled = self._obs.get("enabled", True)
        with self._obs_lock:
            self._obs = _default_obs()
            self._obs["enabled"] = enabled
        self._save()

    # ── 内部处理 ──────────────────────────────────────────────────────────────

    def _process(self, user_msg: str, ai_msg: str, session_id: str):
        now = datetime.now()
        try:
            with self._obs_lock:
                self._obs["total_observations"] = self._obs.get("total_observations", 0) + 1
                self._obs["last_updated"] = now.isoformat(timespec="seconds")
                self._obs["last_seen"] = now.isoformat(timespec="seconds")

                # 活跃时间
                hour_key = str(now.hour)
                hours = self._obs.setdefault("active_hours", {})
                hours[hour_key] = hours.get(hour_key, 0) + 1

                # 连续天数
                self._update_streak(now.date())

                # 话题词频
                self._extract_topics(user_msg)

                # 高频请求短语
                self._extract_phrases(user_msg)

                # 开放任务
                self._extract_open_tasks(user_msg, session_id, now)

                # 工作会话统计
                wp = self._obs.setdefault("work_pattern", {})
                wp["sessions_last_30d"] = wp.get("sessions_last_30d", 0) + 1

            self._save()
        except Exception as exc:
            logger.debug("[ShadowWatcher] 处理异常: %s", exc)

    def _update_streak(self, today: date):
        streak = self._obs.setdefault("streak", {"days": 0, "last_date": None})
        last_str = streak.get("last_date")
        if last_str:
            try:
                last_date = date.fromisoformat(last_str)
                delta = (today - last_date).days
                if delta == 1:
                    streak["days"] = streak.get("days", 0) + 1
                elif delta > 1:
                    streak["days"] = 1
                # delta == 0: same day, no change
            except (ValueError, TypeError):
                streak["days"] = 1
        else:
            streak["days"] = 1
        streak["last_date"] = today.isoformat()

    def _extract_topics(self, text: str):
        lower = text.lower()
        topics = self._obs.setdefault("topics", {})
        for topic, keywords in _TOPIC_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                topics[topic] = topics.get(topic, 0) + 1

    def _extract_phrases(self, text: str):
        phrases = self._obs.setdefault("recurring_phrases", {})
        for phrase in _PHRASE_PATTERNS:
            if phrase in text:
                phrases[phrase] = phrases.get(phrase, 0) + 1

    def _extract_open_tasks(self, text: str, session_id: str, now: datetime):
        tasks: List[Dict] = self._obs.setdefault("open_tasks", [])
        # Keep max 30 open tasks to avoid bloat
        open_count = sum(1 for t in tasks if not t.get("done"))
        if open_count >= 30:
            return

        for pattern in _OPEN_TASK_PATTERNS:
            for m in re.finditer(pattern, text, re.MULTILINE):
                task_text = m.group(len(m.groups())).strip()
                if len(task_text) < 5:
                    continue
                # Dedup: skip if very similar task already exists
                if any(t["text"][:20] == task_text[:20] for t in tasks if not t.get("done")):
                    continue
                import uuid as _uuid
                tasks.append({
                    "id": str(_uuid.uuid4())[:8],
                    "text": task_text[:120],
                    "mentioned_at": now.isoformat(timespec="seconds"),
                    "session": session_id,
                    "done": False,
                })

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _load(self):
        if _OBS_FILE.exists():
            try:
                data = json.loads(_OBS_FILE.read_text(encoding="utf-8"))
                self._obs.update(data)
            except Exception as exc:
                logger.warning("[ShadowWatcher] 加载失败: %s", exc)

    def _save(self):
        try:
            _BASE.mkdir(parents=True, exist_ok=True)
            with self._obs_lock:
                payload = json.dumps(self._obs, ensure_ascii=False, indent=2)
            _OBS_FILE.write_text(payload, encoding="utf-8")
        except Exception as exc:
            logger.warning("[ShadowWatcher] 保存失败: %s", exc)


def get_shadow_watcher() -> ShadowWatcher:
    return ShadowWatcher.get()

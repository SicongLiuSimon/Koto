# -*- coding: utf-8 -*-
"""
Koto ProactiveAgent — 主动交互决策引擎
=======================================
基于 ShadowWatcher 积累的观察数据，决定"是否/何时/说什么"主动与用户交互。

主动消息类型
-----------
  greeting    — 基于时间 + 间隔的问候
  follow_up   — 跟进开放任务
  suggestion  — 基于高频话题的主动建议
  reminder    — 定时器触发的提醒

队列消息存储在内存中（重启清空），可选持久化到 config/proactive_queue.json。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent.parent.parent / "config"
_QUEUE_FILE = _BASE / "proactive_queue.json"

# 最大队列深度（防止无限积压）
_MAX_QUEUE = 20
# 同类消息最小冷却（小时）
_COOLDOWN_HOURS = {
    "greeting": 6,
    "follow_up": 12,
    "suggestion": 24,
    "reminder": 1,
    "failed_retry": 48,
}


# ============================================================================
# 消息数据结构
# ============================================================================


def _make_msg(
    msg_type: str,
    content: str,
    priority: str = "medium",
    triggered_by: str = "",
    ttl_hours: int = 24,
) -> Dict[str, Any]:
    now = datetime.now()
    return {
        "id": str(uuid.uuid4())[:8],
        "type": msg_type,
        "content": content,
        "priority": priority,
        "triggered_by": triggered_by,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
        "dismissed": False,
    }


# ============================================================================
# ProactiveAgent
# ============================================================================


class ProactiveAgent:
    """
    主动交互决策引擎（单例）。
    外部可调用:
        ProactiveAgent.get().tick()          — 调度器定期触发
        ProactiveAgent.get().pending()        — 前端轮询获取待展示消息
        ProactiveAgent.get().dismiss(msg_id) — 用户关闭消息
    """

    _instance: Optional["ProactiveAgent"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queue: List[Dict] = []
        self._last_type_time: Dict[str, datetime] = {}
        self._last_suggestion_topic: str = ""  # 轮换推荐话题，避免每次推同一个
        self._q_lock = threading.Lock()
        self._load_queue()

    @classmethod
    def get(cls) -> "ProactiveAgent":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 外部接口 ──────────────────────────────────────────────────────────────

    def tick(self, llm_fn=None):
        """
        由 TriggerRegistry 的 interval 触发器周期调用。
        llm_fn: callable(prompt:str) -> str  （可选，用于 LLM 生成内容）
        """
        from app.core.monitoring.shadow_watcher import get_shadow_watcher

        watcher = get_shadow_watcher()
        if not watcher.enabled:
            return

        obs = watcher.get_observations()
        now = datetime.now()

        # 1. 问候（距上次问候超过冷却时间）
        if self._can_fire("greeting"):
            msg = self._build_greeting(obs, now, llm_fn)
            if msg:
                self._enqueue(msg)

        # 2. 开放任务跟进
        if self._can_fire("follow_up"):
            msg = self._build_follow_up(watcher.get_open_tasks(), llm_fn)
            if msg:
                self._enqueue(msg)

        # 3. 话题建议
        if self._can_fire("suggestion"):
            msg = self._build_suggestion(obs, llm_fn)
            if msg:
                self._enqueue(msg)

        # 4. 失败任务重试提示
        if self._can_fire("failed_retry"):
            msg = self._build_failed_retry(watcher.get_failed_tasks(), llm_fn)
            if msg:
                self._enqueue(msg)

    def pending(self) -> List[Dict]:
        """返回未过期、未关闭的消息列表（按优先级排序）。"""
        now = datetime.now()
        _priority_order = {"high": 0, "medium": 1, "low": 2}
        with self._q_lock:
            valid = [
                m
                for m in self._queue
                if not m.get("dismissed")
                and datetime.fromisoformat(m["expires_at"]) > now
            ]
        valid.sort(key=lambda m: _priority_order.get(m.get("priority", "medium"), 1))
        return valid

    def dismiss(self, msg_id: str):
        with self._q_lock:
            for m in self._queue:
                if m["id"] == msg_id:
                    m["dismissed"] = True
                    break
        self._save_queue()

    def dismiss_all(self):
        with self._q_lock:
            for m in self._queue:
                m["dismissed"] = True
        self._save_queue()

    def add_reminder(self, content: str, priority: str = "high"):
        """外部直接注入一条提醒（由触发器 job_type=proactive_remind 调用）。"""
        msg = _make_msg("reminder", content, priority=priority, ttl_hours=48)
        self._enqueue(msg)

    # ── 消息构建 ──────────────────────────────────────────────────────────────

    def _build_greeting(self, obs: Dict, now: datetime, llm_fn=None) -> Optional[Dict]:
        last_seen_str = obs.get("last_seen")
        if not last_seen_str:
            return None

        try:
            last_seen = datetime.fromisoformat(last_seen_str)
        except ValueError:
            return None

        gap_hours = (now - last_seen).total_seconds() / 3600

        # 不足 2 小时不问候
        if gap_hours < 2:
            return None

        hour = now.hour
        if 5 <= hour < 12:
            time_str = "早上好"
        elif 12 <= hour < 18:
            time_str = "下午好"
        else:
            time_str = "晚上好"

        streak = obs.get("streak", {}).get("days", 0)
        # 优先使用近期话题（7天内），再 fallback 到全时段
        recent_7d = obs.get("recent_topics_7d") or {}
        recent_30d = obs.get("recent_topics_30d") or obs.get("topics", {})
        recent_topics = recent_7d or recent_30d
        top_topic = next(iter(recent_topics), "") if recent_topics else ""

        if gap_hours > 48:
            days = int(gap_hours / 24)
            content = f"👋 {time_str}！你已经 {days} 天没来了，有什么我可以帮你的吗？"
        elif streak >= 3:
            content = f"👋 {time_str}！你已经连续使用 Koto {streak} 天了，继续保持！"
        elif top_topic:
            content = (
                f"👋 {time_str}！上次我们聊到了「{top_topic}」，今天还有相关的问题吗？"
            )
        else:
            content = f"👋 {time_str}！有什么我可以帮你的吗？"

        # 可选 LLM 润色
        if llm_fn and top_topic:
            try:
                prompt = (
                    f"你是 Koto AI 助手，正在主动问候用户。"
                    f"当前时间是 {now.strftime('%H:%M')}，"
                    f"用户最感兴趣的话题是「{top_topic}」，"
                    f"连续使用天数 {streak}。"
                    "请用一句温暖自然的中文问候（15-40字，可以带1个emoji），"
                    "不要太正式，不要用你好或您好开头。只输出问候语本身。"
                )
                generated = llm_fn(prompt)
                if generated and 10 < len(generated) < 80:
                    content = generated.strip()
            except Exception:
                pass  # fallback to template

        return _make_msg(
            "greeting", content, priority="low", triggered_by="time_gap", ttl_hours=8
        )

    def _build_follow_up(self, open_tasks: List[Dict], llm_fn=None) -> Optional[Dict]:
        if not open_tasks:
            return None
        # 优先选择：多次提到但未完成（revisited_at 存在且 done=False）
        revisited = [
            t for t in open_tasks if t.get("revisited_at") and not t.get("done")
        ]
        if revisited:
            task = sorted(
                revisited, key=lambda t: t.get("revisited_at", ""), reverse=True
            )[0]
            prefix = "📌 你多次提到"
        else:
            task = sorted(open_tasks, key=lambda t: t.get("mentioned_at", ""))[0]
            prefix = "📌 你之前提到"
        task_text = task["text"]

        content = f"{prefix}：「{task_text}」，这件事完成了吗？需要我帮你继续吗？"

        if llm_fn:
            try:
                prompt = (
                    f"你是 Koto AI 助手，要主动跟进用户之前提到的一个任务。"
                    f"任务内容：「{task_text}」。"
                    f"请用一句简短自然的中文（20-50字，含1个emoji）询问进展，"
                    f"并提示可以继续帮助。只输出这一句话。"
                )
                generated = llm_fn(prompt)
                if generated and 15 < len(generated) < 100:
                    content = generated.strip()
            except Exception:
                pass

        return _make_msg(
            "follow_up",
            content,
            priority="medium",
            triggered_by=f"open_task:{task['id']}",
            ttl_hours=24,
        )

    def _build_suggestion(self, obs: Dict, llm_fn=None) -> Optional[Dict]:
        # 优先使用近期话题（近7天），再 fallback 到近30天或全时段
        recent_topics = (
            obs.get("recent_topics_7d")
            or obs.get("recent_topics_30d")
            or dict(sorted(obs.get("topics", {}).items(), key=lambda x: -x[1]))
        )

        # 轮换话题：跳过上次已推荐的，避免每次推相同内容
        topic_keys = list(recent_topics.keys()) if recent_topics else []
        top_topic = ""
        for candidate in topic_keys:
            if candidate != self._last_suggestion_topic:
                top_topic = candidate
                break
        # 所有话题都推过了（只有一个），仍使用它
        if not top_topic and topic_keys:
            top_topic = topic_keys[0]

        # 从任务风格中获取最高频同样轮换的任务类型
        ts = obs.get("task_style", {})
        task_types = ts.get("task_types", {})
        sorted_tasks = sorted(task_types.items(), key=lambda x: -x[1])
        top_task = ""
        for t_name, t_count in sorted_tasks:
            if t_count >= 3 and t_name != self._last_suggestion_topic:
                top_task = t_name
                break

        if not top_topic and not top_task:
            return None

        if top_task:
            picked = top_task
            content = f"💡 我注意到你经常需要「{top_task}」，要不要创建一个快捷 Skill？"
        elif top_topic:
            picked = top_topic
            content = f"💡 你经常使用 Koto 处理「{top_topic}」相关任务，要不要让我整理一份最佳实践？"
        else:
            return None

        self._last_suggestion_topic = picked
        return _make_msg("suggestion", content, priority="low",
                         triggered_by=f"topic:{picked}", ttl_hours=48)

    def _build_failed_retry(
        self, failed_tasks: List[Dict], llm_fn=None
    ) -> Optional[Dict]:
        """针对 AI 之前未能完成的请求，主动提出换个方式再试。"""
        eligible = [
            f for f in failed_tasks if not f.get("retried") and not f.get("resolved")
        ]
        if not eligible:
            return None
        # 选最近的失败任务
        task = sorted(eligible, key=lambda f: f.get("asked_at", ""), reverse=True)[0]
        text = task["text"]

        content = f"🔄 之前我没能帮你完成「{text[:50]}」，现在可以换个思路再试试吗？"

        if llm_fn:
            try:
                prompt = (
                    f"你是 Koto AI 助手。之前用户请求「{text[:80]}」，但你当时无法完成。"
                    "请用一句简短自然的中文（20-55字，含1个emoji）主动提出现在可以换个方式帮忙。"
                    "只输出这一句话。"
                )
                generated = llm_fn(prompt)
                if generated and 15 < len(generated) < 100:
                    content = generated.strip()
            except Exception:
                pass

        return _make_msg(
            "failed_retry",
            content,
            priority="medium",
            triggered_by=f"failed_task:{task['id']}",
            ttl_hours=48,
        )

    # ── 队列管理 ──────────────────────────────────────────────────────────────

    def _can_fire(self, msg_type: str) -> bool:
        cooldown_h = _COOLDOWN_HOURS.get(msg_type, 6)
        last = self._last_type_time.get(msg_type)
        if last and (datetime.now() - last).total_seconds() < cooldown_h * 3600:
            return False
        return True

    def _enqueue(self, msg: Dict):
        with self._q_lock:
            # Trim expired / dismissed first
            now = datetime.now()
            self._queue = [
                m
                for m in self._queue
                if not m.get("dismissed")
                and datetime.fromisoformat(m["expires_at"]) > now
            ]
            if len(self._queue) >= _MAX_QUEUE:
                return
            self._queue.append(msg)
            self._last_type_time[msg["type"]] = datetime.now()
        self._save_queue()
        logger.info(
            "[ProactiveAgent] 新消息入队: [%s] %s", msg["type"], msg["content"][:40]
        )

    def _save_queue(self):
        try:
            _BASE.mkdir(parents=True, exist_ok=True)
            with self._q_lock:
                # 同时持久化冷却时间，防止重启后立即群发消息
                cooldown_data = {
                    k: v.isoformat(timespec="seconds")
                    for k, v in self._last_type_time.items()
                }
                payload = {
                    "queue": self._queue,
                    "last_type_time": cooldown_data,
                    "last_suggestion_topic": self._last_suggestion_topic,
                }
                _QUEUE_FILE.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.debug("[ProactiveAgent] 队列保存失败: %s", exc)

    def _load_queue(self):
        if _QUEUE_FILE.exists():
            try:
                raw = json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
                now = datetime.now()
                # 兼容旧格式（纯列表）和新格式（含冷却时间的字典）
                if isinstance(raw, list):
                    queue_raw = raw
                elif isinstance(raw, dict):
                    queue_raw = raw.get("queue", [])
                    for k, v_str in raw.get("last_type_time", {}).items():
                        try:
                            self._last_type_time[k] = datetime.fromisoformat(v_str)
                        except (ValueError, TypeError):
                            pass
                    self._last_suggestion_topic = raw.get("last_suggestion_topic", "")
                else:
                    queue_raw = []
                # 过滤过期/已关闭条目
                self._queue = [
                    m for m in queue_raw
                    if not m.get("dismissed")
                    and datetime.fromisoformat(m.get("expires_at", "2000-01-01")) > now
                ]
            except Exception as exc:
                logger.debug("[ProactiveAgent] 队列加载失败: %s", exc)


def get_proactive_agent() -> ProactiveAgent:
    return ProactiveAgent.get()

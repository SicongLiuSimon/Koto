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
import queue
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
    "greeting": 4,
    "follow_up": 3,
    "suggestion": 12,
    "reminder": 1,
    "failed_retry": 24,
    "context_carry": 0.25,
    "session_summary": 1,
    "insight": 8,
    "correction_hint": 48,  # 48h — 相当低频，避免打扰
}
# 这些类型入队新消息时自动淘汰同类旧消息（时效性单槽）
_SINGLE_SLOT_TYPES = {"greeting", "session_summary", "insight", "correction_hint"}

# 任务类型 → 接续建议（用于 context_carry）
_TASK_CARRY_HINTS: Dict[str, str] = {
    "翻译": "需要我再润色一下，或者翻译其他段落吗？",
    "写": "要不要让我帮你检查一遍，或者生成一个大纲？",
    "代码": "要不要我帮你写测试用例，或者添加注释？",
    "总结": "要不要我再提炼出几个关键行动点？",
    "分析": "需要我把分析结果整理成报告格式吗？",
    "邮件": "要不要我帮你想一个更好的主题行？",
    "数据": "要不要我帮你生成一张可视化图表？",
    "报告": "要不要我帮你生成一份简洁的执行摘要？",
    "表格": "要不要我帮你分析一下数据规律？",
    "图片": "需要我帮你调整描述或生成更多变体吗？",
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

    # SSE 推送订阅者列表（每个前端连接对应一个 Queue）
    _sse_subs: List["queue.Queue[Dict]"] = []
    _sse_lock = threading.Lock()

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

    def tick_immediate(self):
        """
        由 ShadowWatcher 检测到新失败请求或新开放任务时立即调用（绕过定时 interval）。
        仅检查高优先级类型，使用缩短的冷却时间：
          follow_up    → 1h
          failed_retry → 4h
        """
        from app.core.monitoring.shadow_watcher import get_shadow_watcher

        watcher = get_shadow_watcher()
        if not watcher.enabled:
            return

        if self._can_fire("follow_up", min_cooldown_h=1.0):
            msg = self._build_follow_up(watcher.get_open_tasks())
            if msg:
                self._enqueue(msg)

        if self._can_fire("failed_retry", min_cooldown_h=4.0):
            msg = self._build_failed_retry(watcher.get_failed_tasks())
            if msg:
                self._enqueue(msg)

    def tick_after_exchange(
        self,
        session_exchanges: int,
        completed_task_text: Optional[str],
        obs: Dict,
    ):
        """
        每次对话完成后由 ShadowWatcher 调用，实现真正意义上的「主动 AI」。

        session_exchanges    — 本次会话已进行的轮次数
        completed_task_text  — 刚完成任务的文本（若本轮完成了某个开放任务，否则 None）
        obs                  — 当前影子观察数据快照
        """
        from app.core.monitoring.shadow_watcher import get_shadow_watcher

        if not get_shadow_watcher().enabled:
            return

        # 1. 任务完成后立即接续（「完成了！要不要接着做 X？」）
        if completed_task_text and self._can_fire("context_carry"):
            msg = self._build_context_carry(completed_task_text)
            if msg:
                self._enqueue(msg)

        # 2. 长会话摘要提示（10 轮以上）
        if session_exchanges >= 10 and self._can_fire("session_summary"):
            msg = self._build_session_summary(obs, session_exchanges)
            if msg:
                self._enqueue(msg)

        # 3. 模式洞察（高频话题/短语 → 建议建 Skill / 工作流）
        if self._can_fire("insight"):
            msg = self._build_insight(obs)
            if msg:
                self._enqueue(msg)

        # 4. 修正次数过多 → 提示用户精确描述需求
        if self._can_fire("correction_hint", min_cooldown_h=24.0):
            msg = self._build_correction_hint(obs)
            if msg:
                self._enqueue(msg)

    # ── SSE 订阅管理 ──────────────────────────────────────────────────────────

    @classmethod
    def subscribe_sse(cls) -> "queue.Queue[Dict]":
        """注册一个 SSE 订阅者，返回接收新消息的 Queue（每个前端长连接调用一次）。"""
        q: queue.Queue[Dict] = queue.Queue(maxsize=50)
        with cls._sse_lock:
            cls._sse_subs.append(q)
        return q

    @classmethod
    def unsubscribe_sse(cls, q: "queue.Queue[Dict]"):
        """移除 SSE 订阅者（连接断开时调用）。"""
        with cls._sse_lock:
            try:
                cls._sse_subs.remove(q)
            except ValueError:
                pass

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
        # 深夜（0-5点）不主动问候
        if hour < 5:
            return None

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
        return _make_msg(
            "suggestion",
            content,
            priority="low",
            triggered_by=f"topic:{picked}",
            ttl_hours=48,
        )

    def _build_context_carry(self, completed_task_text: str) -> Optional[Dict]:
        """任务刚完成后，主动推断并提出可能的后续动作。"""
        hint = "还有什么我可以继续帮你的吗？"
        for kw, suggestion in _TASK_CARRY_HINTS.items():
            if kw in completed_task_text:
                hint = suggestion
                break
        content = f"✅ 「{completed_task_text[:40]}」完成了！{hint}"
        return _make_msg(
            "context_carry",
            content,
            priority="medium",
            triggered_by=f"task_done:{completed_task_text[:20]}",
            ttl_hours=2,
        )

    def _build_session_summary(
        self, obs: Dict, session_exchanges: int
    ) -> Optional[Dict]:
        """会话进行 10 轮以上时，主动提议生成摘要供用户保存。"""
        recent_topics = obs.get("recent_topics_7d") or obs.get("topics", {})
        top_topics = list(recent_topics.keys())[:3]
        topics_str = "、".join(top_topics) if top_topics else "多个话题"
        content = (
            f"📋 我们已经聊了 {session_exchanges} 轮，主要围绕「{topics_str}」。"
            "要不要我整理一份对话摘要，方便你保存或下次继续？"
        )
        return _make_msg(
            "session_summary",
            content,
            priority="low",
            triggered_by="session_length",
            ttl_hours=4,
        )

    def _build_insight(self, obs: Dict) -> Optional[Dict]:
        """基于高频话题 / 短语，主动提出效率优化建议（创建 Skill、工作流等）。"""
        recent_topics = obs.get("recent_topics_7d") or {}
        phrases = obs.get("recurring_phrases", {})

        # 话题本周出现 5 次以上 → 提议建工作流
        for topic, count in recent_topics.items():
            if count >= 5:
                content = (
                    f"🔍 我注意到你这周已经处理了 {count} 次「{topic}」相关任务，"
                    "要不要让我帮你建一个专属工作流，下次一键搞定？"
                )
                return _make_msg(
                    "insight",
                    content,
                    priority="low",
                    triggered_by=f"insight_topic:{topic}",
                    ttl_hours=12,
                )

        # 某个短语被使用 8 次以上 → 提议创建 Skill
        for phrase, count in sorted(phrases.items(), key=lambda x: -x[1]):
            if count >= 8:
                content = (
                    f"💡 你已经用「{phrase}」请求了 {count} 次，"
                    "要不要我把它做成一个快捷 Skill，以后触发更高效？"
                )
                return _make_msg(
                    "insight",
                    content,
                    priority="low",
                    triggered_by=f"insight_phrase:{phrase}",
                    ttl_hours=12,
                )

        # 时段×任务洞察：当前时段某任务类型出现 ≥ 4 次时，主动预热
        hourly_tasks: Dict[str, Dict[str, int]] = obs.get("hourly_task_type", {})
        current_hour = str(datetime.now().hour)
        current_slot: Dict[str, int] = hourly_tasks.get(current_hour, {})
        if current_slot:
            top_task_type = max(current_slot, key=lambda k: current_slot[k])
            top_count = current_slot[top_task_type]
            if top_count >= 4:
                _TASK_TYPE_LABELS = {
                    "分析": "分析工作",
                    "创作": "写作",
                    "执行": "系统操作",
                    "问答": "学习研究",
                    "修改": "内容优化",
                    "搜索": "资料查找",
                    "翻译": "翻译任务",
                    "讨论": "探讨",
                }
                label = _TASK_TYPE_LABELS.get(top_task_type, top_task_type)
                content = f"⏰ 这个时间段你通常在做「{label}」——今天也有需要处理的吗？"
                return _make_msg(
                    "insight",
                    content,
                    priority="low",
                    triggered_by=f"hourly_habit:{current_hour}:{top_task_type}",
                    ttl_hours=6,
                )

        return None

    def _build_correction_hint(self, obs: Dict) -> Optional[Dict]:
        """当用户修正 AI 次数较多时，主动提示如何更精确地描述需求。"""
        corrections = obs.get("corrections", 0)
        # 修正 ≥ 5 次才触发，降低噪音
        if corrections < 5:
            return None
        content = (
            f"💬 我注意到你在对话中纠正过我 {corrections} 次，"
            "如果我经常误解你的意图，可以试试在问题前加一句背景说明，效果会更好哦。"
        )
        return _make_msg(
            "correction_hint",
            content,
            priority="low",
            triggered_by=f"corrections:{corrections}",
            ttl_hours=48,
        )

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

        msg = _make_msg(
            "failed_retry",
            content,
            priority="medium",
            triggered_by=f"failed_task:{task['id']}",
            ttl_hours=48,
        )
        msg["task_id"] = task["id"]  # 方便前端直接取用，无需解析 triggered_by
        return msg

    # ── 队列管理 ──────────────────────────────────────────────────────────────

    def _can_fire(self, msg_type: str, min_cooldown_h: Optional[float] = None) -> bool:
        """检查该类型消息是否脱离冷却期。min_cooldown_h 可覆盖默认冷却时长（用于紧急触发）。"""
        cooldown_h = (
            min_cooldown_h
            if min_cooldown_h is not None
            else _COOLDOWN_HOURS.get(msg_type, 6)
        )
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
            # 时效性单槽：淘汰同类旧消息，确保只保留最新的一条
            if msg["type"] in _SINGLE_SLOT_TYPES:
                for m in self._queue:
                    if m["type"] == msg["type"]:
                        m["dismissed"] = True
                self._queue = [m for m in self._queue if not m.get("dismissed")]
            if len(self._queue) >= _MAX_QUEUE:
                return
            self._queue.append(msg)
            self._last_type_time[msg["type"]] = datetime.now()
        self._save_queue()
        logger.info(
            "[ProactiveAgent] 新消息入队: [%s] %s", msg["type"], msg["content"][:40]
        )
        # 实时推送给所有 SSE 订阅者
        with self.__class__._sse_lock:
            dead: List["queue.Queue[Dict]"] = []
            for sub_q in self.__class__._sse_subs:
                try:
                    sub_q.put_nowait(msg)
                except queue.Full:
                    dead.append(sub_q)
            for sub_q in dead:
                self.__class__._sse_subs.remove(sub_q)

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
                    m
                    for m in queue_raw
                    if not m.get("dismissed")
                    and datetime.fromisoformat(m.get("expires_at", "2000-01-01")) > now
                ]
            except Exception as exc:
                logger.debug("[ProactiveAgent] 队列加载失败: %s", exc)


def get_proactive_agent() -> ProactiveAgent:
    return ProactiveAgent.get()

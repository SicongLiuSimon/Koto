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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 路径 ────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent.parent.parent / "config"
_OBS_FILE = _BASE / "shadow_observations.json"

# ── 话题关键词（扩展时只需在此添加） ──────────────────────────────────────────
_TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "数据分析":   ["数据分析", "报表", "图表", "统计", "可视化", "pandas", "excel", "csv", "数据集", "数据库"],
    "写作翻译":   ["写文章", "写报告", "写邮件", "总结一下", "帮我写", "翻译", "润色", "改写", "作文", "文案"],
    "编程开发":   ["代码", "编程", "调试", "bug", "接口", "api", "开发", "python", "javascript",
                   "java", "sql", "算法", "脚本文件", "py文件", "程序报错"],
    "学习研究":   ["学习", "解释一下", "帮我理解", "是什么意思", "原理", "为什么会", "教程", "研究", "查一下"],
    "工作规划":   ["待办", "任务清单", "计划", "日程", "安排", "提醒", "截止", "项目进度", "工作流", "会议"],
    "文件处理":   ["文件整理", "归档", "文档处理", "pdf", "表格处理", "文件夹", "重命名", "找文件"],
    "生活日常":   ["天气", "美食", "购物", "旅游", "健身", "运动", "电影", "音乐", "游戏", "睡眠", "休假"],
    "沟通协作":   ["邮件", "微信", "回复消息", "发给", "联系", "汇报", "沟通", "发送"],
    "创意设计":   ["设计", "创意", "想法", "灵感", "头脑风暴", "画图", "配色", "排版", "logo"],
    "系统设置":   ["koto", "设置", "技能", "模型切换", "配置", "功能开关"],
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

# ── AI 拒绝/失败模式（用于检测 AI 无法完成的请求） ──────────────────────────────
_AI_FAILURE_PATTERNS = [
    "我无法", "我不能", "无法完成", "没有权限", "暂时无法",
    "这超出了我", "很遗憾", "抱歉，我做不到", "抱歉我无法",
    "i cannot", "i can't", "i'm unable", "i don't have access",
    "无法访问", "无法执行", "不支持该功能", "当前无法",
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
        "open_tasks": [],          # [{id, text, mentioned_at, session, done, revisited_at?, completed_at?}]
        "failed_tasks": [],        # [{id, text, asked_at, session, retried, resolved}]
        "topic_history": [],       # [{topic, date}] capped 500 — for recency calculation
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
            obs = dict(self._obs)
        # 追加动态计算的近期话题（供 ProactiveAgent 使用）
        obs["recent_topics_7d"] = self.get_recent_topics(7)
        obs["recent_topics_30d"] = self.get_recent_topics(30)
        return obs

    def get_open_tasks(self) -> List[Dict]:
        with self._obs_lock:
            return [t for t in self._obs.get("open_tasks", []) if not t.get("done")]

    def get_failed_tasks(self) -> List[Dict]:
        """返回 AI 之前未能完成、且尚未解决的请求列表。"""
        with self._obs_lock:
            return [f for f in self._obs.get("failed_tasks", []) if not f.get("resolved")]

    def get_recent_topics(self, days: int = 30) -> Dict[str, int]:
        """返回最近 N 天按频次降序排列的话题字典。"""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._obs_lock:
            history = list(self._obs.get("topic_history", []))
        counts: Dict[str, int] = {}
        for entry in history:
            if entry.get("date", "") >= cutoff:
                t = entry.get("topic", "")
                if t:
                    counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

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

                # 话题词频（含时间记录）
                self._extract_topics(user_msg, now)

                # 高频请求短语
                self._extract_phrases(user_msg)

                # 开放任务
                self._extract_open_tasks(user_msg, session_id, now)

                # 任务完成/失败追踪
                self._check_task_followup(user_msg, ai_msg, session_id, now)
                self._detect_failed_request(user_msg, ai_msg, session_id, now)

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

    def _extract_topics(self, text: str, now: Optional[datetime] = None):
        lower = text.lower()
        topics = self._obs.setdefault("topics", {})
        history: List[Dict] = self._obs.setdefault("topic_history", [])
        date_str = (now.date() if now else date.today()).isoformat()
        for topic, keywords in _TOPIC_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                topics[topic] = topics.get(topic, 0) + 1
                history.append({"topic": topic, "date": date_str})
        # Keep history capped at 500 entries (FIFO)
        if len(history) > 500:
            self._obs["topic_history"] = history[-500:]

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

    def _check_task_followup(self, user_msg: str, ai_msg: str, session_id: str, now: datetime):
        """检测当前对话是否在跟进/完成之前的开放或失败任务，并更新状态。"""
        for task in self._obs.get("open_tasks", []):
            if task.get("done"):
                continue
            if self._task_matches_msg(task["text"], user_msg):
                task["revisited_at"] = now.isoformat(timespec="seconds")
                if not self._is_ai_failure(ai_msg):
                    task["done"] = True
                    task["completed_at"] = now.isoformat(timespec="seconds")

        for ftask in self._obs.get("failed_tasks", []):
            if ftask.get("resolved"):
                continue
            if self._task_matches_msg(ftask["text"], user_msg):
                ftask["retried"] = True
                ftask["retried_at"] = now.isoformat(timespec="seconds")
                if not self._is_ai_failure(ai_msg):
                    ftask["resolved"] = True

    def _task_matches_msg(self, task_text: str, user_msg: str) -> bool:
        """判断用户消息是否在跟进某个任务（基于双字符/词元重叠）。"""
        def _tokens(s: str) -> set:
            s = s.lower()
            result: set = set()
            for i in range(len(s) - 1):
                if '\u4e00' <= s[i] <= '\u9fa5' and '\u4e00' <= s[i + 1] <= '\u9fa5':
                    result.add(s[i:i + 2])
            result.update(re.findall(r'[a-z0-9]{2,}', s))
            return result

        task_tokens = _tokens(task_text)
        msg_tokens = _tokens(user_msg)
        if not task_tokens:
            return False
        overlap = len(task_tokens & msg_tokens)
        return overlap >= 2 and overlap / len(task_tokens) >= 0.35

    def _is_ai_failure(self, ai_msg: str) -> bool:
        """判断 AI 回复是否表示无法完成任务。"""
        lower = ai_msg.lower()
        return any(p.lower() in lower for p in _AI_FAILURE_PATTERNS)

    def _detect_failed_request(self, user_msg: str, ai_msg: str, session_id: str, now: datetime):
        """若 AI 明确拒绝/无法完成，将用户请求记录为失败任务以便后续跟进。"""
        if not self._is_ai_failure(ai_msg) or len(user_msg.strip()) < 8:
            return
        failed: List[Dict] = self._obs.setdefault("failed_tasks", [])
        # 去重
        if any(f["text"][:25] == user_msg[:25] for f in failed):
            return
        # 上限
        if sum(1 for f in failed if not f.get("resolved")) >= 50:
            return
        import uuid as _uuid
        failed.append({
            "id": str(_uuid.uuid4())[:8],
            "text": user_msg[:150],
            "asked_at": now.isoformat(timespec="seconds"),
            "session": session_id,
            "retried": False,
            "resolved": False,
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

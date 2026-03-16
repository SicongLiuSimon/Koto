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
import uuid
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
    "数据分析": [
        "数据分析",
        "报表",
        "图表",
        "统计",
        "可视化",
        "pandas",
        "excel",
        "csv",
        "数据集",
        "数据库",
    ],
    "写作翻译": [
        "写文章",
        "写报告",
        "写邮件",
        "总结一下",
        "帮我写",
        "翻译",
        "润色",
        "改写",
        "作文",
        "文案",
    ],
    "编程开发": [
        "代码",
        "编程",
        "调试",
        "bug",
        "接口",
        "api",
        "开发",
        "python",
        "javascript",
        "java",
        "sql",
        "算法",
        "脚本文件",
        "py文件",
        "程序报错",
    ],
    "学习研究": [
        "学习",
        "解释一下",
        "帮我理解",
        "是什么意思",
        "原理",
        "为什么会",
        "教程",
        "研究",
        "查一下",
    ],
    "工作规划": [
        "待办",
        "任务清单",
        "计划",
        "日程",
        "安排",
        "提醒",
        "截止",
        "项目进度",
        "工作流",
        "会议",
    ],
    "文件处理": [
        "文件整理",
        "归档",
        "文档处理",
        "pdf",
        "表格处理",
        "文件夹",
        "重命名",
        "找文件",
    ],
    "生活日常": [
        "天气",
        "美食",
        "购物",
        "旅游",
        "健身",
        "运动",
        "电影",
        "音乐",
        "游戏",
        "睡眠",
        "休假",
    ],
    "沟通协作": ["邮件", "微信", "回复消息", "发给", "联系", "汇报", "沟通", "发送"],
    "创意设计": [
        "设计",
        "创意",
        "想法",
        "灵感",
        "头脑风暴",
        "画图",
        "配色",
        "排版",
        "logo",
    ],
    "系统设置": ["koto", "设置", "技能", "模型切换", "配置", "功能开关"],
}

# ── 开放任务检测模式 ─────────────────────────────────────────────────────────
_OPEN_TASK_PATTERNS = [
    r"(我|我需要|帮我|请帮我|记得|别忘了|待办[:：]\s*)(.{4,40})",
    r"(需要|打算|计划|准备|想要)\s*(.{4,40})",
    r"(后续|之后|下次|明天|下周)\s*(需要|要|去|做|完成)\s*(.{4,40})",
    r"TODO[：:\s]+(.{4,60})",
]

# ── 任务类型语义（取代浅层用词追踪，关注行为意图而非表面词汇）─────────────────────
_TASK_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "分析":  ["分析", "评估", "比较", "对比", "解读", "解释", "review", "analyze", "compare", "evaluate"],
    "创作":  ["写", "创作", "生成", "起草", "撰写", "write", "create", "generate", "draft", "compose"],
    "执行":  ["运行", "执行", "打开", "启动", "关闭", "安装", "下载", "run", "execute", "open", "install"],
    "问答":  ["是什么", "怎么", "为什么", "如何", "能解释", "what is", "how to", "why", "explain"],
    "修改":  ["修改", "优化", "改进", "润色", "修复", "调整", "edit", "fix", "improve", "refine", "revise"],
    "搜索":  ["找", "搜", "查找", "查一下", "find", "search", "look for"],
    "翻译":  ["翻译", "译成", "translate", "翻成"],
    "讨论":  ["讲讲", "聊聊", "谈谈", "discuss", "your thoughts", "opinion"],
}

# ── 对话风格特征（追踪行为模式，服务于智能推荐和技能生成）─────────────────────────
_CONV_STYLE_SIGNALS: Dict[str, str] = {
    # 明确声明输出偏好
    "explicit_pref": r"用中文|用英文|以[^\s]{1,6}格式|不要[^\s]{1,10}|输出[^\s]{1,8}|返回[^\s]{1,8}格式",
    # 提供背景上下文后再提问
    "context_heavy": r"背景[是：:]|目前[^，。\n]{0,20}|我在做|基于|前提是",
    # 礼貌请求标记
    "polite":        r"请[帮给做写]|麻烦|能帮|可以吗|请问|能否",
    # 多步任务结构
    "multistep":     r"第一[步个]|首先.{1,20}然后|分.*?步骤|step\s*1|先.*?再.*?最后",
}

# ── 输出格式偏好（追踪期望输出形式，供技能推荐和 Prompt 合成使用）─────────────────
_OUTPUT_FORMAT_SIGNALS: Dict[str, str] = {
    "代码":  r"代码|脚本|函数|```|code|script|function",
    "列表":  r"列表|列出|清单|分条|bullet|numbered",
    "表格":  r"表格|对比表|table",
    "详细":  r"详细|详尽|全面|深入|step by step|逐步",
    "简短":  r"一句话|简洁|简短|brief|concise|tldr",
}

# ── AI 拒绝/失败模式（用于检测 AI 无法完成的请求） ──────────────────────────────
_AI_FAILURE_PATTERNS = [
    "我无法",
    "我不能",
    "无法完成",
    "没有权限",
    "暂时无法",
    "这超出了我",
    "很遗憾",
    "抱歉，我做不到",
    "抱歉我无法",
    "i cannot",
    "i can't",
    "i'm unable",
    "i don't have access",
    "无法访问",
    "无法执行",
    "不支持该功能",
    "当前无法",
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
        "conversation_style": {    # 对话风格画像（增量更新，服务于技能推荐和 Prompt 生成）
            "avg_query_len": 0,       # 用户消息平均字数
            "polite_ratio": 0.5,      # 礼貌请求占比  [0=直接命令, 1=全部礼貌]
            "context_ratio": 0.0,     # 提供背景上下文的对话占比
            "explicit_pref_ratio": 0.0,  # 明确声明输出格式偏好的占比
            "multistep_ratio": 0.0,   # 提出多步任务的对话占比
            "samples": 0,
        },
        "task_style": {            # 任务风格分布（近期请求的语义分类统计）
            "task_types": {},         # {task_type: count}  e.g. {"分析": 12, "创作": 5}
            "output_format": {},      # {format: count}  e.g. {"代码": 8, "列表": 3}
            "samples": 0,
        },
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
        # 会话轮次计数（内存中，重启清零）
        self._session_exchanges: Dict[str, int] = {}
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
            return [
                f for f in self._obs.get("failed_tasks", []) if not f.get("resolved")
            ]

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
        _should_trigger = False
        try:
            with self._obs_lock:
                self._obs["total_observations"] = (
                    self._obs.get("total_observations", 0) + 1
                )
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

                # 对话风格 + 任务风格（语义分析，取代浅层用词统计）
                self._analyze_conversation_style(user_msg)
                self._analyze_task_style(user_msg)

                # 记录任务/失败事件的变更前快照
                _prev_failed = len(self._obs.get("failed_tasks", []))
                _prev_open = sum(
                    1 for t in self._obs.get("open_tasks", []) if not t.get("done")
                )

                # 开放任务
                self._extract_open_tasks(user_msg, session_id, now)

                # 任务完成/失败追踪（返回刚完成的任务文本）
                _completed_task_text = self._check_task_followup(user_msg, ai_msg, session_id, now)
                self._detect_failed_request(user_msg, ai_msg, session_id, now)

                # 检测是否新增了重要事件（新失败请求 / 新开放任务），用于立即唤醒 ProactiveAgent
                _new_failed = len(self._obs.get("failed_tasks", [])) > _prev_failed
                _new_open = (
                    sum(1 for t in self._obs.get("open_tasks", []) if not t.get("done"))
                    > _prev_open
                )
                _should_trigger = _new_failed or _new_open

                # 工作会话统计
                wp = self._obs.setdefault("work_pattern", {})
                wp["sessions_last_30d"] = wp.get("sessions_last_30d", 0) + 1

            # 会话轮次计数（无需持锁）
            _session_ex = self._inc_session_exchanges(session_id)

            self._save()
            # 有新的未完成任务或失败请求时，立即（绕过定时 interval）唤醒 ProactiveAgent
            if _should_trigger:
                self._trigger_proactive_tick()
            # 每次对话完成后都调用 per-exchange 主动训练
            self._trigger_after_exchange(_session_ex, _completed_task_text)
        except Exception as exc:
            logger.debug("[ShadowWatcher] 处理异常: %s", exc)

    def _trigger_proactive_tick(self):
        """在后台线程中立即触发 ProactiveAgent（绕过定时 interval）。"""
        def _run():
            try:
                from app.core.agent.proactive_agent import get_proactive_agent
                get_proactive_agent().tick_immediate()
            except Exception as e:
                logger.debug("[ShadowWatcher] 立即触发 ProactiveAgent 失败: %s", e)
        threading.Thread(target=_run, daemon=True, name="sw_proactive_immediate").start()

    def _inc_session_exchanges(self, session_id: str) -> int:
        """为指定会话累加轮次计数（内存，重启清零）。"""
        count = self._session_exchanges.get(session_id, 0) + 1
        self._session_exchanges[session_id] = count
        return count

    def _trigger_after_exchange(
        self, session_exchanges: int, completed_task_text: Optional[str]
    ):
        """每次对话完成后在后台线程调用 tick_after_exchange，实现真正的「随时主动」行为。"""
        _session_ex = session_exchanges
        _completed = completed_task_text
        def _run():
            try:
                from app.core.agent.proactive_agent import get_proactive_agent
                obs = self.get_observations()
                get_proactive_agent().tick_after_exchange(_session_ex, _completed, obs)
            except Exception as e:
                logger.debug("[ShadowWatcher] tick_after_exchange 失败: %s", e)
        threading.Thread(target=_run, daemon=True, name="sw_after_exchange").start()

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

    def _analyze_conversation_style(self, user_msg: str):
        """
        从用户消息中提取对话风格特征，增量更新 conversation_style 画像。
        追踪的是宏观行为模式（礼貌程度、上下文习惯、格式偏好、任务复杂度），
        而非具体用词。

        使用自适应 EMA：前 20 次对话用累积均值快速建立基线，
        之后固定 alpha=0.05（约 20 次对话内即可感知行为模式变化）。
        """
        cs = self._obs.setdefault("conversation_style", {
            "avg_query_len": 0, "polite_ratio": 0.5, "context_ratio": 0.0,
            "explicit_pref_ratio": 0.0, "multistep_ratio": 0.0, "samples": 0,
        })
        n = cs.get("samples", 0) + 1
        cs["samples"] = min(n, 9999)  # 防整数溢出，EMA 本身不依赖 n 的绝对值

        # 自适应 alpha：早期高权重快速收敛，稳定后固定 0.05
        alpha = max(0.05, 1.0 / n)

        def _ema(current: float, new_val: float) -> float:
            return round(current * (1 - alpha) + new_val * alpha, 4)

        # 平均查询长度（EMA）
        cs["avg_query_len"] = round(
            cs.get("avg_query_len", 0) * (1 - alpha) + len(user_msg) * alpha
        )

        lower = user_msg.lower()
        # 各风格维度：本次是否触发（1.0 or 0.0）
        cs["polite_ratio"]        = _ema(cs.get("polite_ratio", 0.5),
                                          1.0 if re.search(_CONV_STYLE_SIGNALS["polite"], lower) else 0.0)
        cs["context_ratio"]       = _ema(cs.get("context_ratio", 0.0),
                                          1.0 if re.search(_CONV_STYLE_SIGNALS["context_heavy"], lower) else 0.0)
        cs["explicit_pref_ratio"] = _ema(cs.get("explicit_pref_ratio", 0.0),
                                          1.0 if re.search(_CONV_STYLE_SIGNALS["explicit_pref"], lower) else 0.0)
        cs["multistep_ratio"]     = _ema(cs.get("multistep_ratio", 0.0),
                                          1.0 if re.search(_CONV_STYLE_SIGNALS["multistep"], lower) else 0.0)

    def _analyze_task_style(self, user_msg: str):
        """
        识别用户请求的任务类型和期望的输出格式，累积分布统计。
        数据供 ProactiveAgent 推荐技能、SkillAutoBuilder 生成画像使用。
        """
        ts = self._obs.setdefault("task_style", {"task_types": {}, "output_format": {}, "samples": 0})
        ts["samples"] = ts.get("samples", 0) + 1

        lower = user_msg.lower()
        task_types: Dict[str, int] = ts.setdefault("task_types", {})
        output_fmt: Dict[str, int] = ts.setdefault("output_format", {})

        # 任务类型识别（可多标签）
        for t_name, keywords in _TASK_TYPE_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                task_types[t_name] = task_types.get(t_name, 0) + 1

        # 输出格式偏好识别（可多标签）
        for fmt_name, pattern in _OUTPUT_FORMAT_SIGNALS.items():
            if re.search(pattern, lower):
                output_fmt[fmt_name] = output_fmt.get(fmt_name, 0) + 1

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
                if any(
                    t["text"][:20] == task_text[:20] for t in tasks if not t.get("done")
                ):
                    continue
                tasks.append({
                    "id": str(uuid.uuid4())[:8],
                    "text": task_text[:120],
                    "mentioned_at": now.isoformat(timespec="seconds"),
                    "session": session_id,
                    "done": False,
                })

    def _check_task_followup(
        self, user_msg: str, ai_msg: str, session_id: str, now: datetime
    ) -> Optional[str]:
        """检测当前对话是否在跟进/完成之前的开放或失败任务，并更新状态。
        返回刚完成的任务文本（若本轮完成了某个任务），否则返回 None。"""
        completed_text: Optional[str] = None
        for task in self._obs.get("open_tasks", []):
            if task.get("done"):
                continue
            if self._task_matches_msg(task["text"], user_msg):
                task["revisited_at"] = now.isoformat(timespec="seconds")
                if not self._is_ai_failure(ai_msg):
                    task["done"] = True
                    task["completed_at"] = now.isoformat(timespec="seconds")
                    completed_text = task["text"]

        for ftask in self._obs.get("failed_tasks", []):
            if ftask.get("resolved"):
                continue
            if self._task_matches_msg(ftask["text"], user_msg):
                ftask["retried"] = True
                ftask["retried_at"] = now.isoformat(timespec="seconds")
                if not self._is_ai_failure(ai_msg):
                    ftask["resolved"] = True

        return completed_text

    def _task_matches_msg(self, task_text: str, user_msg: str) -> bool:
        """判断用户消息是否在跟进某个任务（基于双字符/词元重叠）。"""

        def _tokens(s: str) -> set:
            s = s.lower()
            result: set = set()
            for i in range(len(s) - 1):
                if "\u4e00" <= s[i] <= "\u9fa5" and "\u4e00" <= s[i + 1] <= "\u9fa5":
                    result.add(s[i : i + 2])
            result.update(re.findall(r"[a-z0-9]{2,}", s))
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

    def _detect_failed_request(
        self, user_msg: str, ai_msg: str, session_id: str, now: datetime
    ):
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
        failed.append({
            "id": str(uuid.uuid4())[:8],
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

    def _dirty_save(self):
        """累积变更计数；每 5 次写一次磁盘，降低高频对话下的 I/O 压力。"""
        self._dirty_count += 1
        if self._dirty_count >= 5:
            self._dirty_count = 0
            self._save()

    def _save(self):
        try:
            _BASE.mkdir(parents=True, exist_ok=True)
            with self._obs_lock:
                # 裁剪已完成的 open_tasks（保留全部未完成 + 最近 20 条已完成）
                open_tasks = self._obs.get("open_tasks", [])
                undone = [t for t in open_tasks if not t.get("done")]
                done   = [t for t in open_tasks if t.get("done")]
                self._obs["open_tasks"] = undone + done[-20:]
                # 裁剪已解决的 failed_tasks（保留全部未解决 + 最近 10 条已解决）
                failed_tasks = self._obs.get("failed_tasks", [])
                unresolved = [f for f in failed_tasks if not f.get("resolved")]
                resolved   = [f for f in failed_tasks if f.get("resolved")]
                self._obs["failed_tasks"] = unresolved + resolved[-10:]
                payload = json.dumps(self._obs, ensure_ascii=False, indent=2)
            _OBS_FILE.write_text(payload, encoding="utf-8")
        except Exception as exc:
            logger.warning("[ShadowWatcher] 保存失败: %s", exc)


def get_shadow_watcher() -> ShadowWatcher:
    return ShadowWatcher.get()

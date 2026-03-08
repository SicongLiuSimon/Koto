# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   Koto  ─  MacroRecorder：AI 时代的宏录制引擎                    ║
╚══════════════════════════════════════════════════════════════════╝

后台静默追踪用户重复的对话工作流。
当检测到用户在滚动窗口内重复了同一意图模式 ≥ REPEAT_THRESHOLD 次时，
生成一个 MacroSuggestion，由前端弹出：
    "老板，我发现你经常做这个，帮你固化成专属按钮？"

设计原则
────────
1. 零感知   — 异步后台处理，不影响对话响应速度
2. 本地规则 — 无需 LLM，纯关键词 + 集合相似度匹配
3. 用户优先 — 建议需用户确认才能创建 Skill；用户可随时忽略
4. 去重保护 — 同一模式建议一次后进入冷却，不重复打扰

工作流程
────────
1. 每次 AI 响应完成后调用 MacroRecorder.record_turn(user_msg, task_type, session_id)
2. 引擎提取意图指纹（intent fingerprint）
3. 在滚动窗口内统计相同/相似的指纹
4. ≥ REPEAT_THRESHOLD 次 → 生成 MacroSuggestion 并持久化
5. 前端轮询 /api/macro/pending 拿到建议后弹出 toast

存储
────
    config/macro_suggestions.json
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── 路径 ─────────────────────────────────────────────────────────────────────

def _get_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]

_BASE_DIR = _get_base_dir()
_CONFIG_DIR = _BASE_DIR / "config"
_SUGGESTIONS_FILE = _CONFIG_DIR / "macro_suggestions.json"

# ── 配置常量 ──────────────────────────────────────────────────────────────────

REPEAT_THRESHOLD    = 3     # 重复多少次触发建议
WINDOW_SIZE         = 60    # 滚动窗口大小（最近 N 次对话轮次）
SIMILARITY_THRESHOLD = 0.35 # Jaccard 相似度阈值（视为"相同意图"的边界）
COOLDOWN_MIN_TURNS  = 15    # 触发建议后间隔多少次对话才能再次检测同类

# ── 动作关键词表 ──────────────────────────────────────────────────────────────
# 格式：(匹配词, 规范化标签)

_ACTION_KEYWORDS: List[Tuple[str, str]] = [
    # 文本/写作
    ("总结",   "总结"),
    ("摘要",   "总结"),
    ("概括",   "总结"),
    ("summarize", "总结"),
    ("翻译",   "翻译"),
    ("translate", "翻译"),
    ("写",     "写作"),
    ("撰写",   "写作"),
    ("起草",   "写作"),
    ("生成",   "生成"),
    ("创作",   "写作"),
    # 分析
    ("分析",   "分析"),
    ("比较",   "比较"),
    ("对比",   "比较"),
    ("评估",   "评估"),
    ("评价",   "评估"),
    ("research", "研究"),
    ("研究",   "研究"),
    # 代码
    ("代码",   "代码"),
    ("编程",   "代码"),
    ("调试",   "调试"),
    ("debug",  "调试"),
    ("修复",   "修复"),
    ("bug",    "调试"),
    ("重构",   "重构"),
    ("优化",   "优化"),
    ("optimize", "优化"),
    ("test",   "测试"),
    ("测试",   "测试"),
    # 数据/文件
    ("整理",   "整理"),
    ("格式化", "格式化"),
    ("提取",   "提取"),
    ("解析",   "提取"),
    ("查找",   "搜索"),
    ("搜索",   "搜索"),
    ("search", "搜索"),
    ("清洗",   "数据处理"),
    ("处理",   "数据处理"),
    # 解释/学习
    ("解释",   "解释"),
    ("举例",   "解释"),
    ("说明",   "解释"),
    ("理解",   "解释"),
    ("教",     "教学"),
    ("explain", "解释"),
    # 审阅
    ("检查",   "检查"),
    ("校对",   "校对"),
    ("审阅",   "审阅"),
    ("review", "审阅"),
]

# 对象关键词（增强指纹区分度）
_OBJECT_KEYWORDS: List[str] = [
    "文件", "文档", "pdf", "ppt", "excel", "csv", "表格", "报告",
    "邮件", "代码", "函数", "class", "api", "接口", "数据", "图片",
    "视频", "网页", "文章", "论文", "合同", "方案", "计划",
    "笔记", "列表", "项目", "需求", "规格", "日志", "配置",
]

# 构建 {匹配词 → 标签} 字典（供 O(1) 查找）
_ACTION_MAP: Dict[str, str] = {kw: label for kw, label in _ACTION_KEYWORDS}


# ══════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════

class MacroSuggestion:
    """一个宏建议条目，可序列化为 JSON。"""

    __slots__ = (
        "id", "status", "fingerprint", "task_type",
        "actions", "objects", "sample_messages",
        "detected_count", "suggested_name", "suggested_desc",
        "created_at", "dismissed_at", "confirmed_at", "skill_id",
    )

    def __init__(
        self,
        *,
        id: str,
        status: str = "pending",      # pending / confirmed / dismissed
        fingerprint: str,
        task_type: str,
        actions: List[str],
        objects: List[str],
        sample_messages: List[str],
        detected_count: int,
        suggested_name: str,
        suggested_desc: str,
        created_at: str,
        dismissed_at: Optional[str] = None,
        confirmed_at: Optional[str] = None,
        skill_id: Optional[str] = None,
    ):
        self.id             = id
        self.status         = status
        self.fingerprint    = fingerprint
        self.task_type      = task_type
        self.actions        = actions
        self.objects        = objects
        self.sample_messages = sample_messages
        self.detected_count = detected_count
        self.suggested_name = suggested_name
        self.suggested_desc = suggested_desc
        self.created_at     = created_at
        self.dismissed_at   = dismissed_at
        self.confirmed_at   = confirmed_at
        self.skill_id       = skill_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":             self.id,
            "status":         self.status,
            "fingerprint":    self.fingerprint,
            "task_type":      self.task_type,
            "actions":        self.actions,
            "objects":        self.objects,
            "sample_messages": self.sample_messages,
            "detected_count": self.detected_count,
            "suggested_name": self.suggested_name,
            "suggested_desc": self.suggested_desc,
            "created_at":     self.created_at,
            "dismissed_at":   self.dismissed_at,
            "confirmed_at":   self.confirmed_at,
            "skill_id":       self.skill_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MacroSuggestion":
        return cls(
            id=d["id"],
            status=d.get("status", "pending"),
            fingerprint=d["fingerprint"],
            task_type=d.get("task_type", ""),
            actions=d.get("actions", []),
            objects=d.get("objects", []),
            sample_messages=d.get("sample_messages", []),
            detected_count=d.get("detected_count", 0),
            suggested_name=d.get("suggested_name", ""),
            suggested_desc=d.get("suggested_desc", ""),
            created_at=d.get("created_at", ""),
            dismissed_at=d.get("dismissed_at"),
            confirmed_at=d.get("confirmed_at"),
            skill_id=d.get("skill_id"),
        )


class _TurnRecord:
    """一次对话轮次的轻量记录（只存在内存中）。"""
    __slots__ = ("fingerprint", "task_type", "actions", "objects", "user_msg", "turn_at")

    def __init__(
        self,
        fingerprint: str,
        task_type: str,
        actions: List[str],
        objects: List[str],
        user_msg: str,
        turn_at: str,
    ):
        self.fingerprint = fingerprint
        self.task_type   = task_type
        self.actions     = actions
        self.objects     = objects
        self.user_msg    = user_msg
        self.turn_at     = turn_at


# ══════════════════════════════════════════════════════════════════
# MacroRecorder — 核心引擎（单例）
# ══════════════════════════════════════════════════════════════════

class MacroRecorder:
    """
    单例。外部通过 MacroRecorder.record_turn(user_msg, task_type, session_id)
    在每次 AI 响应完成后异步触发后台检测。
    """

    _instance: Optional["MacroRecorder"] = None
    _cls_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._window: List[_TurnRecord]     = []   # 滚动窗口
        self._suggestions: List[MacroSuggestion] = []
        self._seen_fps: Set[str]             = set()  # 已建议/忽略的指纹
        self._cooldown: Dict[str, int]       = {}   # fp → remaining_turns_before_recheck
        self._enabled = True
        self._load()

    # ── 单例工厂 ──────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "MacroRecorder":
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 公开 API ──────────────────────────────────────────────────────────────

    @classmethod
    def record_turn(cls, user_msg: str, task_type: str = "CHAT", session_id: str = "default"):
        """主入口：AI 响应完成后异步调用，零阻塞。"""
        rec = cls.get()
        if not rec._enabled:
            return
        threading.Thread(
            target=rec._process,
            args=(user_msg, task_type, session_id),
            daemon=True,
        ).start()

    def pending(self) -> List[Dict[str, Any]]:
        """返回所有 status='pending' 的建议（供前端轮询）。"""
        with self._lock:
            return [s.to_dict() for s in self._suggestions if s.status == "pending"]

    def dismiss(self, suggestion_id: str) -> bool:
        """用户忽略建议。"""
        with self._lock:
            for s in self._suggestions:
                if s.id == suggestion_id and s.status == "pending":
                    s.status       = "dismissed"
                    s.dismissed_at = _now_iso()
                    self._seen_fps.add(s.fingerprint)
                    self._save()
                    logger.info(f"[MacroRecorder] 用户忽略建议: {s.suggested_name}")
                    return True
        return False

    def confirm(self, suggestion_id: str, skill_name: str) -> Optional[str]:
        """
        用户确认建议 → 调用 SkillManager 创建真实 Skill。
        返回新 skill_id 或 None（失败时）。
        """
        with self._lock:
            target = next(
                (s for s in self._suggestions
                 if s.id == suggestion_id and s.status == "pending"),
                None,
            )
            if target is None:
                return None

        try:
            skill_id = self._create_skill(target, skill_name)
            with self._lock:
                target.status       = "confirmed"
                target.confirmed_at = _now_iso()
                target.skill_id     = skill_id
                self._seen_fps.add(target.fingerprint)
                self._save()
            logger.info(f"[MacroRecorder] ✅ 宏已固化为 Skill: {skill_name} ({skill_id})")
            return skill_id
        except Exception as exc:
            logger.error(f"[MacroRecorder] confirm 创建 Skill 失败: {exc}")
            return None

    # ── 内部处理 ──────────────────────────────────────────────────────────────

    def _process(self, user_msg: str, task_type: str, session_id: str):
        """在后台线程中提取指纹、检测重复模式。"""
        try:
            actions, objects = _extract_intent(user_msg)
            if not actions:
                return  # 纯闲聊，无可识别意图

            fp      = _make_fingerprint(task_type, actions, objects)
            now_iso = _now_iso()

            turn = _TurnRecord(
                fingerprint=fp,
                task_type=task_type,
                actions=actions,
                objects=objects,
                user_msg=user_msg[:200],
                turn_at=now_iso,
            )

            with self._lock:
                # 1. 加入滚动窗口
                self._window.append(turn)
                if len(self._window) > WINDOW_SIZE:
                    self._window.pop(0)

                # 2. 推进冷却计数器（冷却结束则移除）
                expired = [k for k, v in self._cooldown.items() if v <= 1]
                for k in expired:
                    del self._cooldown[k]
                for k in list(self._cooldown.keys()):
                    self._cooldown[k] -= 1

                # 3. 检测是否需要生成建议
                suggestion = self._detect_pattern(fp, task_type, actions, objects)
                if suggestion:
                    self._suggestions.append(suggestion)
                    self._seen_fps.add(fp)
                    self._cooldown[fp] = COOLDOWN_MIN_TURNS
                    self._save()
                    logger.info(
                        f"[MacroRecorder] 🎯 检测到重复模式 ({suggestion.detected_count}次)"
                        f"，建议名称: 「{suggestion.suggested_name}」"
                    )
        except Exception as exc:
            logger.debug(f"[MacroRecorder] _process 异常: {exc}")

    def _detect_pattern(
        self,
        current_fp: str,
        task_type: str,
        actions: List[str],
        objects: List[str],
    ) -> Optional[MacroSuggestion]:
        """
        在当前滚动窗口内检测 ≥ REPEAT_THRESHOLD 次相似意图。
        已建议过的指纹或正在冷却中的指纹直接跳过。
        """
        if current_fp in self._seen_fps:
            return None
        if current_fp in self._cooldown:
            return None

        current_set = set(actions + objects)
        matching: List[_TurnRecord] = []

        for t in self._window:
            # 任务类型必须相同
            if t.task_type != task_type:
                continue
            # 优先比较动作集合（动作重叠度高则认为是同类工作流）
            action_sim  = _jaccard(set(t.actions), set(actions))
            combined_sim = _jaccard(set(t.actions + t.objects), current_set)
            sim = max(action_sim, combined_sim)
            if sim >= SIMILARITY_THRESHOLD:
                matching.append(t)

        if len(matching) < REPEAT_THRESHOLD:
            return None

        # 消息必须来自 ≥2 条不同输入（避免将同一条消息的反复发送误判为"习惯"）
        if len({t.user_msg for t in matching}) < 2:
            return None

        all_actions = sorted({a for t in matching for a in t.actions})
        all_objects = sorted({o for t in matching for o in t.objects})
        samples     = [t.user_msg for t in matching[-3:]]

        name, desc = _suggest_name_desc(task_type, all_actions, all_objects)
        return MacroSuggestion(
            id              = str(uuid.uuid4())[:8],
            fingerprint     = current_fp,
            task_type       = task_type,
            actions         = all_actions,
            objects         = all_objects,
            sample_messages = samples,
            detected_count  = len(matching),
            suggested_name  = name,
            suggested_desc  = desc,
            created_at      = _now_iso(),
        )

    def _create_skill(self, sug: MacroSuggestion, skill_name: str) -> str:
        """根据 MacroSuggestion 创建并注册 SkillDefinition。"""
        from app.core.skills.skill_schema import SkillDefinition, SkillCategory
        from app.core.skills.skill_manager import SkillManager

        skill_id     = "macro_" + sug.id
        actions_str  = "、".join(sug.actions) if sug.actions else "处理"
        objects_str  = "、".join(sug.objects) if sug.objects else "内容"

        # 构建 system prompt 片段
        prompt = (
            f"\n\n## 🎯 用户专属宏：{skill_name}\n"
            f"- 本技能对应用户的常见操作习惯：{actions_str} {objects_str}\n"
            f"- 收到此类请求时，直接高效执行，无需额外铺垫或重复确认\n"
            f"- 输出保持简洁专业，直接给出结果\n"
            f"- 用户典型请求示例：\n"
        )
        for i, msg in enumerate(sug.sample_messages, 1):
            prompt += f"  {i}. {msg}\n"

        skill_def = SkillDefinition(
            id                    = skill_id,
            name                  = skill_name,
            icon                  = "🎯",
            category              = SkillCategory.WORKFLOW,
            description           = sug.suggested_desc,
            intent_description    = f"用户想要{actions_str}相关的{objects_str}",
            system_prompt_template = prompt,
            task_types            = [sug.task_type] if sug.task_type else [],
            author                = "user",
            tags                  = ["macro", "自动录制"] + sug.actions[:2],
            enabled               = True,
        )
        SkillManager.register_custom(skill_def)
        return skill_id

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _save(self):
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "suggestions":       [s.to_dict() for s in self._suggestions],
                "seen_fingerprints": list(self._seen_fps),
            }
            _SUGGESTIONS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(f"[MacroRecorder] 保存失败: {exc}")

    def _load(self):
        try:
            if _SUGGESTIONS_FILE.exists():
                raw = json.loads(_SUGGESTIONS_FILE.read_text(encoding="utf-8"))
                self._suggestions = [
                    MacroSuggestion.from_dict(d)
                    for d in raw.get("suggestions", [])
                ]
                self._seen_fps = set(raw.get("seen_fingerprints", []))
        except Exception as exc:
            logger.debug(f"[MacroRecorder] 加载失败: {exc}")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_intent(text: str) -> Tuple[List[str], List[str]]:
    """从用户消息中提取 (动作标签列表, 对象关键词列表)。"""
    text_lower = text.lower()

    seen_labels: Dict[str, bool] = {}
    for kw, label in _ACTION_KEYWORDS:
        if kw in text_lower and label not in seen_labels:
            seen_labels[label] = True

    objects_found = [obj for obj in _OBJECT_KEYWORDS if obj in text_lower]

    return list(seen_labels.keys()), objects_found[:4]


def _make_fingerprint(task_type: str, actions: List[str], objects: List[str]) -> str:
    """生成稳定的 12 位 MD5 指纹供去重。"""
    key = f"{task_type}|{','.join(sorted(actions))}|{','.join(sorted(objects))}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _jaccard(a: set, b: set) -> float:
    """Jaccard 相似度（0.0 ~ 1.0）。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _suggest_name_desc(
    task_type: str,
    actions: List[str],
    objects: List[str],
) -> Tuple[str, str]:
    """生成对用户友好的按钮名称和描述。"""
    action_str = "、".join(actions[:2]) if actions else "处理"
    object_str = "、".join(objects[:2]) if objects else "内容"
    name = f"{action_str}{object_str}" if objects else action_str
    desc = (
        f"📽️ 自动录制：一键{action_str}您的{object_str}。"
        f"源自您最近 {WINDOW_SIZE} 次对话中的高频操作习惯。"
    )
    return name, desc


def get_macro_recorder() -> MacroRecorder:
    """获取全局单例（供 Blueprint 使用）。"""
    return MacroRecorder.get()

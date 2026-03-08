# -*- coding: utf-8 -*-
"""
Koto SkillTriggerBinding — 技能触发器绑定层
============================================
让技能（Skill）不再是静态的 prompt 片段，
而成为可被事件自动激活的可执行能力单元。

支持的绑定目标：
  trigger_type  — 复用 TriggerRegistry，绑定 interval / cron / webhook / startup
  intent        — 在 UnifiedAgent 识别到匹配意图时自动注入 skill
  file_event    — 当文件/目录发生变化时触发（基于轮询，Phase 1）

绑定的执行方式：
  mode="inject"   — 将 skill 注入到下一次 agent 调用的 system_prompt（实时效果）
  mode="execute"  — 以 job_runner 作业形式在后台执行

持久化：config/skill_bindings.json

用法示例：
    from app.core.skills.skill_trigger_binding import SkillBindingManager

    mgr = SkillBindingManager()

    # 绑定：每天 08:00 自动执行"每日摘要"技能
    mgr.bind_trigger(
        skill_id="daily_summary",
        trigger_type="cron",
        trigger_config={"time": "08:00"},
        mode="execute",
        job_payload={"query": "生成今日工作摘要"},
    )

    # 绑定：检测到"简短回答"意图时自动激活 concise_mode
    mgr.bind_intent(
        skill_id="concise_mode",
        intent_patterns=["简短", "快速", "简洁", "一句话"],
    )
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE = Path(
    os.environ.get(
        "KOTO_DB_DIR",
        Path(__file__).parent.parent.parent.parent / "config",
    )
)
_BINDINGS_FILE = _BASE / "skill_bindings.json"

_RECOMMENDED_INTENT_BINDINGS = [
    {
        "skill_id": "concise_mode",
        "patterns": ["简短", "简洁", "一句话", "快速说", "长话短说", "总结一下"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "step_by_step",
        "patterns": ["一步一步", "分步骤", "操作流程", "详细步骤", "怎么做"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "teaching_mode",
        "patterns": ["教我", "讲解", "通俗解释", "像老师一样", "我没学过"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "professional_tone",
        "patterns": ["正式一点", "专业一点", "商务语气", "写邮件", "汇报", "报告"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "writing_assistant",
        "patterns": ["润色", "改写", "优化表达", "重写", "整理成文"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "code_best_practices",
        "patterns": ["写代码", "重构", "代码优化", "最佳实践", "写个函数", "实现一下"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "security_aware",
        "patterns": ["安全", "风险", "漏洞", "权限", "注入", "加密"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "research_depth",
        "patterns": ["深入分析", "深度研究", "详细分析", "全面比较", "背景和影响"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "task_planner",
        "patterns": ["计划", "安排", "待办", "路线图", "拆解任务", "里程碑"],
        "auto_disable_after_turns": 2,
    },
]


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class SkillBinding:
    """一条技能绑定记录。"""
    binding_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    skill_id: str = ""
    binding_type: str = "trigger"       # trigger / intent / file_event
    mode: str = "execute"               # inject / execute

    # trigger 绑定
    trigger_type: str = ""              # interval / cron / webhook / startup
    trigger_config: Dict[str, Any] = field(default_factory=dict)
    trigger_id: Optional[str] = None   # TriggerRegistry 中的 trigger_id，自动生成
    job_payload: Dict[str, Any] = field(default_factory=dict)

    # intent 绑定
    intent_patterns: List[str] = field(default_factory=list)
    auto_disable_after_turns: int = 3   # 自动注入的最大轮次

    # file_event 绑定
    watch_path: str = ""
    watch_events: List[str] = field(default_factory=lambda: ["created", "modified"])

    enabled: bool = True
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="milliseconds")
    )

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "SkillBinding":
        valid = SkillBinding.__dataclass_fields__.keys()
        return SkillBinding(**{k: v for k, v in d.items() if k in valid})


# ============================================================================
# SkillBindingManager
# ============================================================================

class SkillBindingManager:
    """
    管理技能绑定的生命周期。

    职责：
      - CRUD 绑定记录
      - 创建 trigger 绑定时，同步在 TriggerRegistry 注册触发器
      - 提供 match_intent() 供 UnifiedAgent 在路由时查询
      - 提供 check_file_events() 供后台轮询文件变化
    """

    def __init__(self):
        self._bindings: Dict[str, SkillBinding] = {}
        self._lock = threading.Lock()
        self._load_persisted()
        self.ensure_recommended_bindings()

    # ── Trigger 绑定 ──────────────────────────────────────────────────────────

    def bind_trigger(
        self,
        skill_id: str,
        trigger_type: str,
        trigger_config: Optional[Dict[str, Any]] = None,
        mode: str = "execute",
        job_payload: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
    ) -> SkillBinding:
        """将 skill 绑定到调度触发器。"""
        payload = job_payload or {}
        if mode == "execute":
            payload.setdefault("skill_id", skill_id)
            payload.setdefault("query", f"执行技能: {skill_id}")

        binding = SkillBinding(
            skill_id=skill_id,
            binding_type="trigger",
            mode=mode,
            trigger_type=trigger_type,
            trigger_config=trigger_config or {},
            job_payload=payload,
        )

        # 同步注册到 TriggerRegistry
        try:
            from app.core.jobs.trigger_registry import TriggerSpec, get_trigger_registry

            trig_spec = TriggerSpec(
                name=name or f"skill:{skill_id}",
                trigger_type=trigger_type,
                job_type="skill_exec",
                job_payload=payload,
                config=trigger_config or {},
            )
            get_trigger_registry().register(trig_spec)
            binding.trigger_id = trig_spec.trigger_id
            logger.info(
                "[SkillBinding] 技能 %s 已绑定 trigger %s",
                skill_id, trig_spec.trigger_id[:8],
            )
        except Exception as exc:
            logger.warning("[SkillBinding] 注册触发器失败: %s", exc)

        self._save(binding)
        return binding

    # ── Intent 绑定 ───────────────────────────────────────────────────────────

    def bind_intent(
        self,
        skill_id: str,
        intent_patterns: List[str],
        auto_disable_after_turns: int = 3,
    ) -> SkillBinding:
        """当用户输入匹配 intent_patterns 时，自动注入该 skill 的 prompt。"""
        binding = SkillBinding(
            skill_id=skill_id,
            binding_type="intent",
            mode="inject",
            intent_patterns=intent_patterns,
            auto_disable_after_turns=auto_disable_after_turns,
        )
        self._save(binding)
        logger.info("[SkillBinding] 技能 %s 已绑定 intent 模式", skill_id)
        return binding

    def ensure_recommended_bindings(self, force: bool = False) -> Dict[str, Any]:
        """Seed curated built-in intent bindings so runtime skill resolution works out of the box."""
        created = []
        skipped = []

        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
        except Exception as exc:
            logger.warning("[SkillBinding] 初始化推荐绑定失败: %s", exc)
            return {"created": created, "skipped": [f"init_failed:{exc}"]}

        existing_intents = self.list_bindings(binding_type="intent")
        existing_by_key = {
            (binding.skill_id, tuple(sorted(p.lower() for p in binding.intent_patterns))): binding
            for binding in existing_intents
        }

        for preset in _RECOMMENDED_INTENT_BINDINGS:
            skill_id = preset["skill_id"]
            if not SkillManager.get_definition(skill_id):
                skipped.append(skill_id)
                continue

            patterns = [pattern.strip() for pattern in preset["patterns"] if pattern.strip()]
            preset_key = (skill_id, tuple(sorted(pattern.lower() for pattern in patterns)))
            existing = existing_by_key.get(preset_key)
            if not force and existing:
                skipped.append(skill_id)
                continue

            if force and existing:
                self.remove(existing.binding_id)

            self.bind_intent(
                skill_id=skill_id,
                intent_patterns=patterns,
                auto_disable_after_turns=int(preset.get("auto_disable_after_turns", 2)),
            )
            refreshed = self.list_bindings(skill_id=skill_id, binding_type="intent")
            for binding in refreshed:
                key = (binding.skill_id, tuple(sorted(p.lower() for p in binding.intent_patterns)))
                existing_by_key[key] = binding
            created.append(skill_id)

        return {"created": created, "skipped": skipped}

    def match_intent(self, user_input: str) -> List[str]:
        """
        供 UnifiedAgent/Tool Router 调用：
        返回匹配当前输入的所有 skill_id 列表。
        """
        matched = []
        lower_input = user_input.lower()
        for binding in self._bindings.values():
            if binding.binding_type != "intent" or not binding.enabled:
                continue
            for pattern in binding.intent_patterns:
                if pattern.lower() in lower_input:
                    matched.append(binding.skill_id)
                    break
        return matched

    # ── 通用 CRUD ─────────────────────────────────────────────────────────────

    def list_bindings(
        self,
        skill_id: Optional[str] = None,
        binding_type: Optional[str] = None,
    ) -> List[SkillBinding]:
        with self._lock:
            result = list(self._bindings.values())
        if skill_id:
            result = [b for b in result if b.skill_id == skill_id]
        if binding_type:
            result = [b for b in result if b.binding_type == binding_type]
        return result

    def get(self, binding_id: str) -> Optional[SkillBinding]:
        return self._bindings.get(binding_id)

    def remove(self, binding_id: str) -> bool:
        """删除绑定，同时清理对应 TriggerRegistry 条目。"""
        with self._lock:
            binding = self._bindings.pop(binding_id, None)
        if not binding:
            return False

        if binding.trigger_id:
            try:
                from app.core.jobs.trigger_registry import get_trigger_registry
                get_trigger_registry().remove(binding.trigger_id)
            except Exception:
                pass

        self._persist()
        return True

    def enable(self, binding_id: str, enabled: bool = True):
        with self._lock:
            if binding_id in self._bindings:
                self._bindings[binding_id].enabled = enabled
        self._persist()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _save(self, binding: SkillBinding):
        with self._lock:
            self._bindings[binding.binding_id] = binding
        self._persist()

    def _persist(self):
        try:
            _BASE.mkdir(parents=True, exist_ok=True)
            data = [b.to_dict() for b in self._bindings.values()]
            _BINDINGS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("[SkillBinding] 持久化失败: %s", exc)

    def _load_persisted(self):
        if not _BINDINGS_FILE.exists():
            return
        try:
            data = json.loads(_BINDINGS_FILE.read_text(encoding="utf-8"))
            for d in data:
                b = SkillBinding.from_dict(d)
                self._bindings[b.binding_id] = b
            logger.info(
                "[SkillBinding] 加载 %d 条技能绑定", len(self._bindings)
            )
        except Exception as exc:
            logger.warning("[SkillBinding] 加载失败: %s", exc)


# ============================================================================
# 单例
# ============================================================================

_manager: Optional[SkillBindingManager] = None
_mgr_lock = threading.Lock()


def get_skill_binding_manager() -> SkillBindingManager:
    global _manager
    if _manager is None:
        with _mgr_lock:
            if _manager is None:
                _manager = SkillBindingManager()
    return _manager

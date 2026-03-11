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
        "patterns": [
            # 明确要求步骤
            "一步一步", "分步骤", "详细步骤", "步骤说明", "分步说明",
            # 流程/操作类
            "操作流程", "操作步骤", "操作方法", "操作指南", "操作说明",
            "安装步骤", "配置步骤", "部署步骤", "设置步骤", "配置方法",
            # 「如何」系列
            "如何安装", "如何配置", "如何部署", "如何设置", "如何使用",
            "如何操作", "如何完成", "如何实现", "如何修复", "如何解决",
            # 「怎么」系列
            "怎么安装", "怎么配置", "怎么使用", "怎么操作", "怎么做",
            "怎样安装", "怎样配置", "怎样使用", "怎样做",
            # 流程/流程图
            "工作流程", "业务流程", "完整流程", "处理流程", "操作流",
            # 排查类
            "故障排查", "问题排查", "如何排查", "怎么排查", "排查思路",
            # 学习类
            "从零开始", "零基础", "新手入门", "快速上手", "入门教程",
        ],
        "auto_disable_after_turns": 3,
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
    # ── 文件/工作区 ──
    {
        "skill_id": "workspace_context",
        "patterns": ["当前目录", "项目结构", "我的项目", "工程目录", "工作目录",
                     "这个项目", "目录结构", "项目里有哪些"],
        "auto_disable_after_turns": 3,
    },
    {
        "skill_id": "archive_assistant",
        "patterns": ["整理文件", "归档文件", "清理下载", "整理文件夹", "整理桌面",
                     "文件夹整理", "自动分类文件"],
        "auto_disable_after_turns": 1,
    },
    # ── 高价值商业 Workflow ──
    {
        "skill_id": "email_composer",
        "patterns": ["写邮件", "帮我写邮件", "邮件正文", "回复邮件", "邮件草稿",
                     "起草邮件", "客户邮件", "一封邮件", "封邮件", "封邮", "邮件范文"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "meeting_minutes",
        "patterns": ["会议纪要", "整理会议", "会议记录", "帮我整理会议", "会议总结"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "negotiation_assist",
        "patterns": ["谈判", "砍价", "商务谈判", "谈条件", "价格谈判", "谈判话术",
                     "谈判策略", "谈判技巧"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "root_cause",
        "patterns": ["根因分析", "根本原因", "问题溯源", "rca", "故障复盘",
                     "追溯问题", "为什么会发生"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "brainstorm",
        "patterns": ["头脑风暴", "想法发散", "帮我想想", "有什么方案", "想点子",
                     "有哪些思路", "集思广益"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "pros_cons",
        "patterns": ["优缺点", "利弊分析", "正反两面", "帮我比较", "方案对比",
                     "权衡利弊", "做决策"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "contract_reviewer",
        "patterns": ["审合同", "看合同", "合同条款", "合同风险", "审核合同",
                     "合同有没有问题", "协议审查"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "interview_prep",
        "patterns": ["面试准备", "面试题", "帮我准备面试", "模拟面试", "面试技巧",
                     "面试常见问题", "面试自我介绍"],
        "auto_disable_after_turns": 2,
    },
    {
        "skill_id": "social_copy",
        "patterns": ["朋友圈文案", "小红书文案", "社媒文案", "营销文案", "推广文案",
                     "抖音文案", "种草文案", "公众号文案"],
        "auto_disable_after_turns": 1,
    },
    {
        "skill_id": "prompt_engineer",
        "patterns": ["写prompt", "优化prompt", "提示词", "写提示词", "提示词优化",
                     "system prompt", "如何写prompt", "ai提示词"],
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
        """Seed curated built-in intent bindings so runtime skill resolution works out of the box.

        Strategy: index existing intent bindings by skill_id. If a preset's skill_id already has
        a binding with IDENTICAL patterns → skip. If patterns differ (preset was updated) → remove
        old binding and create fresh one. This prevents stale duplicate bindings after updates.
        """
        created = []
        skipped = []

        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
        except Exception as exc:
            logger.warning("[SkillBinding] 初始化推荐绑定失败: %s", exc)
            return {"created": created, "skipped": [f"init_failed:{exc}"]}

        existing_intents = self.list_bindings(binding_type="intent")
        # Index by (skill_id, sorted_patterns) for exact match check
        existing_by_exact_key = {
            (b.skill_id, tuple(sorted(p.lower() for p in b.intent_patterns))): b
            for b in existing_intents
        }
        # Index by skill_id for stale-pattern cleanup
        existing_by_skill: Dict[str, List] = {}
        for b in existing_intents:
            existing_by_skill.setdefault(b.skill_id, []).append(b)

        for preset in _RECOMMENDED_INTENT_BINDINGS:
            skill_id = preset["skill_id"]
            if not SkillManager.get_definition(skill_id):
                skipped.append(skill_id)
                continue

            patterns = [pattern.strip() for pattern in preset["patterns"] if pattern.strip()]
            preset_key = (skill_id, tuple(sorted(p.lower() for p in patterns)))

            # Exact match exists and not forcing → skip
            if not force and existing_by_exact_key.get(preset_key):
                skipped.append(skill_id)
                continue

            # Remove ALL existing intent bindings for this skill_id (stale or forced update)
            for old_binding in existing_by_skill.get(skill_id, []):
                try:
                    self.remove(old_binding.binding_id)
                except Exception:
                    pass
            existing_by_skill.pop(skill_id, None)

            self.bind_intent(
                skill_id=skill_id,
                intent_patterns=patterns,
                auto_disable_after_turns=int(preset.get("auto_disable_after_turns", 2)),
            )
            # Refresh local index
            refreshed = self.list_bindings(skill_id=skill_id, binding_type="intent")
            for b in refreshed:
                key = (b.skill_id, tuple(sorted(p.lower() for p in b.intent_patterns)))
                existing_by_exact_key[key] = b
                existing_by_skill.setdefault(b.skill_id, []).append(b)
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

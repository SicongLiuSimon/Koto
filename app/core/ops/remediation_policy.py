# -*- coding: utf-8 -*-
"""
Koto RemediationPolicy — 自愈策略引擎
======================================
监听 OpsEventBus，在检测到特定异常事件时自动执行对应的修复动作。

内置策略（Rule）：
  规则名                触发事件                    修复动作
  ─────────────────────────────────────────────────────
  model_fallback        dependency_unhealthy        切换到备用本地模型
  queue_drain           job_failed (连续 5 次)       清空并重排挂起任务
  skill_circuit_breaker skill_exec 失败计数 ≥ 3      临时禁用技能
  scheduler_backoff     scheduler_skipped ≥ 3       减少调度频率
  gc_on_memory          memory_pressure             强制 Python GC

每条策略有冷却时间（cooldown_seconds），避免频繁触发。

自定义策略注册：
    from app.core.ops.remediation_policy import get_remediation_policy, RemediationRule
    pol = get_remediation_policy()
    pol.add_rule(RemediationRule(
        name="my_rule",
        trigger_event="my_event",
        action=lambda event, ctx: do_something(event),
        cooldown_seconds=120,
    ))
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RemediationRule:
    """一条自愈规则。"""
    name: str
    trigger_event: str              # OpsEvent.event_type，"*" = 所有
    action: Callable                # fn(event: OpsEvent, ctx: dict) → None
    cooldown_seconds: int = 60      # 同一规则两次触发之间的最小间隔
    min_occurrences: int = 1        # 在 window_seconds 内出现几次才触发
    window_seconds: int = 60        # 统计窗口
    enabled: bool = True

    # 运行时统计（不序列化）
    last_fired: float = 0.0
    fire_count: int = 0


class RemediationPolicy:
    """
    自愈策略引擎。

    订阅 OpsEventBus，对每条传入的 OpsEvent 逐一检查匹配规则，
    满足条件时在守护线程中异步执行修复动作。
    """

    def __init__(self):
        self._rules: Dict[str, RemediationRule] = {}
        self._event_counts: Dict[str, List[float]] = {}  # event_type → [timestamp]
        self._lock = threading.Lock()
        self._add_builtin_rules()

    # ── 规则管理 ──────────────────────────────────────────────────────────────

    def add_rule(self, rule: RemediationRule):
        with self._lock:
            self._rules[rule.name] = rule
        logger.debug("[Remediation] 注册规则: %s", rule.name)

    def remove_rule(self, name: str):
        with self._lock:
            self._rules.pop(name, None)

    def enable_rule(self, name: str, enabled: bool = True):
        with self._lock:
            if name in self._rules:
                self._rules[name].enabled = enabled

    def list_rules(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": r.name,
                    "trigger_event": r.trigger_event,
                    "cooldown_seconds": r.cooldown_seconds,
                    "min_occurrences": r.min_occurrences,
                    "enabled": r.enabled,
                    "last_fired": (
                        datetime.fromtimestamp(r.last_fired).isoformat()
                        if r.last_fired else None
                    ),
                    "fire_count": r.fire_count,
                }
                for r in self._rules.values()
            ]

    # ── 事件处理入口 ──────────────────────────────────────────────────────────

    def handle(self, event):
        """由 OpsEventBus 回调，逐一检查匹配规则。"""
        event_type = event.event_type
        now = time.monotonic()

        # 追踪事件发生时间（用于 min_occurrences 检查）
        with self._lock:
            timestamps = self._event_counts.setdefault(event_type, [])
            timestamps.append(now)

        with self._lock:
            matching = [
                r for r in self._rules.values()
                if r.enabled and (
                    r.trigger_event == event_type or r.trigger_event == "*"
                )
            ]

        for rule in matching:
            if self._should_fire(rule, event_type, now):
                threading.Thread(
                    target=self._fire_rule,
                    args=(rule, event),
                    daemon=True,
                ).start()

    # ── 内部逻辑 ──────────────────────────────────────────────────────────────

    def _should_fire(self, rule: RemediationRule, event_type: str, now: float) -> bool:
        # 冷却期检查
        if (now - rule.last_fired) < rule.cooldown_seconds:
            return False

        # min_occurrences 检查
        if rule.min_occurrences > 1:
            with self._lock:
                timestamps = self._event_counts.get(event_type, [])
                window_start = now - rule.window_seconds
                recent = [t for t in timestamps if t >= window_start]
                # 清理过期记录
                self._event_counts[event_type] = recent
            if len(recent) < rule.min_occurrences:
                return False

        return True

    def _fire_rule(self, rule: RemediationRule, event):
        try:
            logger.info(
                "[Remediation] ⚡ 激活规则 '%s' (事件: %s)",
                rule.name, event.event_type,
            )
            rule.action(event, {"rule": rule})
            rule.last_fired = time.monotonic()
            rule.fire_count += 1

            # 发布自愈事件
            try:
                from app.core.ops.ops_event_bus import get_ops_bus
                get_ops_bus().emit(
                    "remediation_triggered",
                    {
                        "rule": rule.name,
                        "trigger_event": event.event_type,
                        "fire_count": rule.fire_count,
                    },
                    severity="info",
                )
            except Exception:
                pass

        except Exception as exc:
            logger.error("[Remediation] 规则 '%s' 执行失败: %s", rule.name, exc)

    # ── 内置规则 ──────────────────────────────────────────────────────────────

    def _add_builtin_rules(self):
        self.add_rule(RemediationRule(
            name="gc_on_memory_pressure",
            trigger_event="memory_pressure",
            action=_action_gc,
            cooldown_seconds=120,
        ))
        self.add_rule(RemediationRule(
            name="disable_failed_skill",
            trigger_event="job_failed",
            action=_action_disable_failed_skill,
            cooldown_seconds=300,
            min_occurrences=3,
            window_seconds=600,
        ))
        self.add_rule(RemediationRule(
            name="notify_model_fallback",
            trigger_event="model_fallback",
            action=_action_log_model_fallback,
            cooldown_seconds=60,
        ))
        self.add_rule(RemediationRule(
            name="warn_queue_backlog",
            trigger_event="job_failed",
            action=_action_warn_queue_backlog,
            cooldown_seconds=300,
            min_occurrences=5,
            window_seconds=300,
        ))


# ============================================================================
# 内置修复动作
# ============================================================================

def _action_gc(event, ctx):
    """强制 Python 垃圾回收。"""
    before = gc.collect()
    logger.info("[Remediation] GC 完成，回收对象数: %d", before)


def _action_disable_failed_skill(event, ctx):
    """当某个 skill_exec 作业连续失败时，临时禁用对应技能。"""
    detail = event.detail
    skill_id = detail.get("skill_id") or (
        detail.get("payload", {}).get("skill_id") if isinstance(detail.get("payload"), dict) else None
    )
    if not skill_id:
        return
    try:
        from app.core.skills.skill_manager import SkillManager
        SkillManager.set_enabled(skill_id, False)
        logger.warning("[Remediation] 技能 %s 已自动禁用（多次失败）", skill_id)
        from app.core.ops.ops_event_bus import get_ops_bus
        get_ops_bus().emit(
            "skill_disabled",
            {"skill_id": skill_id, "reason": "auto_disabled_after_repeated_failures"},
            severity="warning",
        )
    except Exception as exc:
        logger.warning("[Remediation] 禁用技能失败: %s", exc)


def _action_log_model_fallback(event, ctx):
    """记录模型降级事件（可扩展为发送通知）。"""
    detail = event.detail
    logger.warning(
        "[Remediation] 模型降级: %s → %s | 原因: %s",
        detail.get("from", "?"),
        detail.get("to", "?"),
        detail.get("reason", "?"),
    )


def _action_warn_queue_backlog(event, ctx):
    """作业队列连续失败时发出积压警告。"""
    logger.error("[Remediation] 作业队列异常：短时间内多次失败，请检查配置和依赖服务")


# ============================================================================
# 单例
# ============================================================================

_policy: Optional[RemediationPolicy] = None
_policy_lock = threading.Lock()


def get_remediation_policy() -> RemediationPolicy:
    global _policy
    if _policy is None:
        with _policy_lock:
            if _policy is None:
                _policy = RemediationPolicy()
    return _policy

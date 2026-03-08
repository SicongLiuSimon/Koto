# -*- coding: utf-8 -*-
"""
Koto OpsEventBus — 运维事件总线
================================
与 ProgressBus（面向用户的任务进度）相对，OpsEventBus 专门处理
系统级运维事件，供告警系统、自愈策略和诊断日志消费。

标准事件类型（event_type）：
  "job_failed"             — 后台作业执行失败
  "job_recovered"          — 崩溃恢复重排了挂起作业
  "scheduler_skipped"      — 调度触发器触发失败
  "skill_disabled"         — 某技能被系统自动禁用
  "dependency_unhealthy"   — 依赖服务不可达（模型 / Ollama / 文件系统）
  "model_fallback"         — AI 模型降级到备选模型
  "remediation_triggered"  — 自愈策略被激活
  "port_conflict"          — 端口冲突被检测到
  "startup_complete"       — 应用启动完成
  "memory_pressure"        — 内存或上下文窗口压力告警

消费方：
  - AlertManager（通知 → 邮件/Webhook）
  - RemediationPolicy（触发 → 自愈动作）
  - OpsSnapshot（聚合 → /api/ops/incidents）
  - 日志系统

用法:
    from app.core.ops.ops_event_bus import get_ops_bus
    get_ops_bus().emit("model_fallback", {
        "from": "gemini-2.5-flash",
        "to":   "ollama/qwen2.5",
        "reason": "API timeout",
    })
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# 保留最近 N 条事件（供 HealthSnapshot 聚合）
_HISTORY_MAX = 500


@dataclass
class OpsEvent:
    """一条运维事件记录。"""
    event_type: str
    detail: Dict[str, Any]
    severity: str = "info"      # info / warning / error / critical
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="milliseconds")
    )
    source: str = "system"      # 发出方标识

    def to_dict(self) -> Dict:
        return {
            "event_type": self.event_type,
            "detail": self.detail,
            "severity": self.severity,
            "timestamp": self.timestamp,
            "source": self.source,
        }


class OpsEventBus:
    """
    运维事件总线。

    - emit(event_type, detail) 广播给所有已注册的处理器
    - subscribe(event_type, handler) 注册处理器（支持通配符 "*"）
    - get_recent(n) 返回最近 n 条事件（用于 incidents 接口）
    """

    def __init__(self):
        self._history: Deque[OpsEvent] = deque(maxlen=_HISTORY_MAX)
        self._handlers: Dict[str, List[Callable[[OpsEvent], None]]] = {}
        self._lock = threading.Lock()

    # ── 发布 ──────────────────────────────────────────────────────────────────

    def emit(
        self,
        event_type: str,
        detail: Optional[Dict[str, Any]] = None,
        severity: str = "info",
        source: str = "system",
    ):
        """发布运维事件，同步调用所有已注册处理器（在守护线程中）。"""
        event = OpsEvent(
            event_type=event_type,
            detail=detail or {},
            severity=severity,
            source=source,
        )

        with self._lock:
            self._history.append(event)
            # 收集：特定类型 + 通配符
            handlers = list(self._handlers.get(event_type, []))
            handlers += list(self._handlers.get("*", []))

        if handlers:
            def _dispatch():
                for h in handlers:
                    try:
                        h(event)
                    except Exception as exc:
                        logger.debug("[OpsEventBus] 处理器异常: %s", exc)

            threading.Thread(target=_dispatch, daemon=True).start()

        # 高严重度事件直接写日志
        if severity in ("error", "critical"):
            logger.error("[OpsEvent] %s | %s | %s", severity.upper(), event_type, detail)
        elif severity == "warning":
            logger.warning("[OpsEvent] %s | %s", event_type, detail)
        else:
            logger.debug("[OpsEvent] %s | %s", event_type, detail)

    # ── 订阅 ──────────────────────────────────────────────────────────────────

    def subscribe(
        self,
        event_type: str,
        handler: Callable[[OpsEvent], None],
    ):
        """
        注册事件处理器。

        event_type 使用 "*" 订阅所有类型。
        handler 签名: fn(event: OpsEvent) → None
        """
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get_recent(self, n: int = 50) -> List[OpsEvent]:
        """返回最近 n 条运维事件（最新在前）。"""
        with self._lock:
            events = list(self._history)
        return list(reversed(events[-n:]))

    def get_by_type(self, event_type: str, limit: int = 20) -> List[OpsEvent]:
        with self._lock:
            events = [e for e in self._history if e.event_type == event_type]
        return list(reversed(events[-limit:]))

    def get_stats(self) -> Dict[str, Any]:
        """返回各事件类型的计数统计。"""
        with self._lock:
            events = list(self._history)
        counts: Dict[str, int] = {}
        for e in events:
            counts[e.event_type] = counts.get(e.event_type, 0) + 1
        return {"total": len(events), "by_type": counts}


# ============================================================================
# 单例 + 默认订阅者注册
# ============================================================================

_bus: Optional[OpsEventBus] = None
_bus_lock = threading.Lock()


def get_ops_bus() -> OpsEventBus:
    """获取全局 OpsEventBus 单例，首次调用时绑定 AlertManager 订阅。"""
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = OpsEventBus()
                _setup_default_subscriptions(_bus)
    return _bus


def _setup_default_subscriptions(bus: OpsEventBus):
    """将 AlertManager 和 RemediationPolicy 绑定到总线。"""

    def _alert_handler(event: OpsEvent):
        try:
            from app.core.monitoring.alert_manager import get_alert_manager
            am = get_alert_manager()
            am.process_event({
                "event_type": event.event_type,
                "severity": _map_severity(event.severity),
                "description": str(event.detail),
                "metric_name": event.event_type,
                "metric_value": 1,
                "threshold": 1,
                "timestamp": event.timestamp,
            })
        except Exception:
            pass

    def _remediation_handler(event: OpsEvent):
        try:
            from app.core.ops.remediation_policy import get_remediation_policy
            get_remediation_policy().handle(event)
        except Exception:
            pass

    bus.subscribe("*", _alert_handler)
    bus.subscribe("*", _remediation_handler)


def _map_severity(koto_sev: str) -> str:
    """将 OpsEvent severity 映射到 AlertManager 的 severity。"""
    return {"info": "low", "warning": "medium", "error": "high", "critical": "high"}.get(
        koto_sev, "medium"
    )

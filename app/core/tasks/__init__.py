# -*- coding: utf-8 -*-
"""
app.core.tasks
==============
统一任务管理子系统

模块:
  task_ledger   — 持久化任务台账（SQLite）
  progress_bus  — 全局进度事件总线（SSE + 内存订阅）
  task_planner  — 通用多步骤 DAG 规划器
"""

from .task_ledger import (
    TaskLedger,
    TaskRecord,
    TaskStatus,
    get_ledger,
)

from .progress_bus import (
    ProgressBus,
    ProgressEvent,
    get_progress_bus,
)

from .task_planner import (
    TaskPlanner,
    PlanStep,
    StepStatus,
    Plan,
)

__all__ = [
    # ledger
    "TaskLedger",
    "TaskRecord",
    "TaskStatus",
    "get_ledger",
    # bus
    "ProgressBus",
    "ProgressEvent",
    "get_progress_bus",
    # planner
    "TaskPlanner",
    "PlanStep",
    "StepStatus",
    "Plan",
]

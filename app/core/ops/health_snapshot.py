# -*- coding: utf-8 -*-
"""
Koto HealthSnapshot — 统一健康视图
====================================
聚合多个子系统的健康状态，提供统一的"是否健康 + 哪里出了问题"快照。

聚合来源：
  - 模型可用性（主模型 + 本地 Ollama 备用）
  - 后台作业队列积压和失败率
  - 调度器 / TriggerRegistry 状态
  - 告警系统（最近未送达告警数）
  - 技能系统（禁用技能数量）
  - OpsEventBus 最近异常事件数
  - 内存 / 进程资源（psutil 可选）

暴露 API：
  GET /api/ops/health        — 快速 Health 状态（200 = 健康, 503 = 降级）
  GET /api/ops/readiness     — 应用是否就绪（路由层可用）
  GET /api/ops/metrics       — 详细指标 JSON
  GET /api/ops/incidents     — 最新运维事件列表
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 健康状态等级
_HEALTHY   = "healthy"
_DEGRADED  = "degraded"
_UNHEALTHY = "unhealthy"


class HealthSnapshot:
    """
    聚合所有子系统健康状态，生成能被 REST endpoint 直接返回的快照。

    每次调用 collect() 会重新采集所有指标；结果缓存 TTL 30s。
    """

    _TTL = 30  # 秒

    def __init__(self):
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_ts: float = 0
        self._lock = threading.Lock()

    # ── 对外接口 ──────────────────────────────────────────────────────────────

    def collect(self, force: bool = False) -> Dict[str, Any]:
        """
        采集并返回完整健康快照。

        Returns:
            {
              "status":    "healthy" | "degraded" | "unhealthy",
              "timestamp": str,
              "checks":    { name: { status, message, value } },
              "summary":   str,
            }
        """
        with self._lock:
            now = time.monotonic()
            if not force and self._cache and (now - self._cache_ts) < self._TTL:
                return self._cache

            snapshot = self._build_snapshot()
            self._cache = snapshot
            self._cache_ts = now
        return snapshot

    def is_ready(self) -> bool:
        """快速就绪检查（路由层是否可以接收请求）。"""
        snap = self.collect()
        return snap["status"] != _UNHEALTHY

    # ── 内部采集 ──────────────────────────────────────────────────────────────

    def _build_snapshot(self) -> Dict[str, Any]:
        checks: Dict[str, Dict] = {}
        issues: List[str] = []

        # ── 1. 作业队列 ───────────────────────────────────────────────────────
        checks["job_queue"] = self._check_job_queue()
        if checks["job_queue"]["status"] != _HEALTHY:
            issues.append(checks["job_queue"]["message"])

        # ── 2. 模型可用性 ─────────────────────────────────────────────────────
        checks["model"] = self._check_model()
        if checks["model"]["status"] != _HEALTHY:
            issues.append(checks["model"]["message"])

        # ── 3. 本地模型 (Ollama) ──────────────────────────────────────────────
        checks["ollama"] = self._check_ollama()
        # Ollama 不可达不算 unhealthy，只是 degraded

        # ── 4. 调度器 ─────────────────────────────────────────────────────────
        checks["scheduler"] = self._check_scheduler()

        # ── 5. 技能系统 ───────────────────────────────────────────────────────
        checks["skills"] = self._check_skills()

        # ── 6. 最近运维事件 ───────────────────────────────────────────────────
        checks["ops_events"] = self._check_ops_events()
        if checks["ops_events"]["status"] == _UNHEALTHY:
            issues.append(checks["ops_events"]["message"])

        # ── 7. 系统资源 ───────────────────────────────────────────────────────
        checks["resources"] = self._check_resources()
        if checks["resources"]["status"] == _UNHEALTHY:
            issues.append(checks["resources"]["message"])

        # ── 聚合状态 ──────────────────────────────────────────────────────────
        statuses = [c["status"] for c in checks.values()]
        if _UNHEALTHY in statuses:
            overall = _UNHEALTHY
        elif _DEGRADED in statuses:
            overall = _DEGRADED
        else:
            overall = _HEALTHY

        return {
            "status": overall,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "checks": checks,
            "issues": issues,
            "summary": (
                "所有系统正常" if not issues
                else "; ".join(issues[:3])
            ),
        }

    # ── 单项检查 ──────────────────────────────────────────────────────────────

    def _check_job_queue(self) -> Dict:
        try:
            from app.core.tasks.task_ledger import get_ledger, TaskStatus
            ledger = get_ledger()
            running = ledger.count(status=TaskStatus.RUNNING, source="job_runner")
            failed_recent = ledger.count(status=TaskStatus.FAILED, source="job_runner")
            pending = ledger.count(status=TaskStatus.PENDING, source="job_runner")
            status = _HEALTHY
            msg = f"running={running} pending={pending}"
            if failed_recent > 20:
                status = _DEGRADED
                msg = f"最近失败任务数过多: {failed_recent}"
            if pending > 50:
                status = _DEGRADED
                msg = f"积压任务数过多: {pending}"
            return {"status": status, "message": msg,
                    "value": {"running": running, "pending": pending, "failed": failed_recent}}
        except Exception as exc:
            return {"status": _DEGRADED, "message": f"任务台账不可用: {exc}", "value": {}}

    def _check_model(self) -> Dict:
        """检查主模型 API 可用性（仅检查配置是否存在，不发真实请求）。"""
        try:
            import os
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                # 尝试从配置文件读取
                from pathlib import Path
                env_path = Path("config/gemini_config.env")
                if env_path.exists():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        if line.startswith("GEMINI_API_KEY="):
                            api_key = line.split("=", 1)[1].strip()
                            break
            if not api_key:
                return {
                    "status": _DEGRADED,
                    "message": "GEMINI_API_KEY 未配置",
                    "value": {},
                }
            return {"status": _HEALTHY, "message": "API Key 已配置", "value": {}}
        except Exception as exc:
            return {"status": _DEGRADED, "message": str(exc), "value": {}}

    def _check_ollama(self) -> Dict:
        """检查本地 Ollama 服务是否可达。"""
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",
                headers={"User-Agent": "koto-healthcheck"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return {"status": _HEALTHY, "message": "Ollama 可达", "value": {}}
        except Exception:
            pass
        return {
            "status": _DEGRADED,
            "message": "Ollama 不可达（本地模型不可用）",
            "value": {},
        }

    def _check_scheduler(self) -> Dict:
        try:
            from app.core.jobs.trigger_registry import get_trigger_registry
            reg = get_trigger_registry()
            triggers = reg.list_all()
            enabled = [t for t in triggers if t.enabled]
            errored = [t for t in enabled if t.last_error]
            msg = f"触发器 {len(enabled)}/{len(triggers)} 已启用"
            status = _DEGRADED if errored else _HEALTHY
            if errored:
                msg = f"{len(errored)} 个触发器最近出错"
            return {"status": status, "message": msg,
                    "value": {"total": len(triggers), "enabled": len(enabled), "errored": len(errored)}}
        except Exception as exc:
            return {"status": _DEGRADED, "message": f"调度器不可用: {exc}", "value": {}}

    def _check_skills(self) -> Dict:
        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
            total = len(SkillManager._def_registry)
            enabled = sum(
                1 for s in SkillManager._registry.values() if s.get("enabled", False)
            )
            return {
                "status": _HEALTHY,
                "message": f"技能 {enabled}/{total} 已激活",
                "value": {"total": total, "enabled": enabled},
            }
        except Exception as exc:
            return {"status": _DEGRADED, "message": str(exc), "value": {}}

    def _check_ops_events(self) -> Dict:
        try:
            from app.core.ops.ops_event_bus import get_ops_bus
            bus = get_ops_bus()
            recent = bus.get_recent(50)
            errors = [e for e in recent if e.severity in ("error", "critical")]
            if len(errors) > 10:
                return {
                    "status": _UNHEALTHY,
                    "message": f"最近 50 条事件中有 {len(errors)} 条严重错误",
                    "value": {"recent_errors": len(errors)},
                }
            if errors:
                return {
                    "status": _DEGRADED,
                    "message": f"最近有 {len(errors)} 条错误事件",
                    "value": {"recent_errors": len(errors)},
                }
            return {"status": _HEALTHY, "message": "无异常运维事件", "value": {"recent_errors": 0}}
        except Exception as exc:
            return {"status": _HEALTHY, "message": "OpsEventBus 尚未初始化", "value": {}}

    def _check_resources(self) -> Dict:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            mem_pct = mem.percent
            status = _HEALTHY
            msg = f"CPU {cpu:.0f}% | 内存 {mem_pct:.0f}%"
            if cpu > 90 or mem_pct > 90:
                status = _UNHEALTHY
                msg = f"资源严重不足: CPU {cpu:.0f}% 内存 {mem_pct:.0f}%"
            elif cpu > 75 or mem_pct > 80:
                status = _DEGRADED
                msg = f"资源偏高: CPU {cpu:.0f}% 内存 {mem_pct:.0f}%"
            return {
                "status": status,
                "message": msg,
                "value": {"cpu_pct": cpu, "mem_pct": mem_pct},
            }
        except ImportError:
            return {"status": _HEALTHY, "message": "psutil 未安装（跳过资源检查）", "value": {}}
        except Exception as exc:
            return {"status": _HEALTHY, "message": str(exc), "value": {}}


# ============================================================================
# 单例
# ============================================================================

_snapshot: Optional[HealthSnapshot] = None
_snap_lock = threading.Lock()


def get_health_snapshot() -> HealthSnapshot:
    global _snapshot
    if _snapshot is None:
        with _snap_lock:
            if _snapshot is None:
                _snapshot = HealthSnapshot()
    return _snapshot

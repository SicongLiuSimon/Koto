# -*- coding: utf-8 -*-
"""
Koto TriggerRegistry — 统一触发器管理中心
==========================================
将"什么时候自动跑任务"的逻辑统一收口。

支持的触发器类型 (trigger_type)：
  "interval"  — 每隔 N 秒执行一次
                config: { "interval_seconds": 3600 }

  "cron"      — 每天 HH:MM 执行（简化 cron）
                config: { "time": "09:00" }

  "webhook"   — 调用 POST /api/jobs/triggers/<id>/fire 手动触发
                config: {}（无需调度器）

  "startup"   — 应用启动时自动触发一次
                config: {}

触发器持久化存储在 config/triggers.json，重启后自动恢复。

用法示例：
    from app.core.jobs.trigger_registry import get_trigger_registry, TriggerSpec

    reg = get_trigger_registry()

    # 每天 09:00 整理文档
    reg.register(TriggerSpec(
        name="早间文档整理",
        trigger_type="cron",
        job_type="auto_catalog",
        job_payload={"source_dir": "C:/Documents"},
        config={"time": "09:00"},
    ))

    # 每小时同步一次数据
    reg.register(TriggerSpec(
        name="数据同步",
        trigger_type="interval",
        job_type="workflow",
        job_payload={"workflow_id": "hourly_sync"},
        config={"interval_seconds": 3600},
    ))
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 持久化路径 ────────────────────────────────────────────────────────────────
_BASE = Path(
    os.environ.get("KOTO_DB_DIR",
                   Path(__file__).parent.parent.parent.parent / "config")
)
_TRIGGERS_FILE = _BASE / "triggers.json"

_VALID_TRIGGER_TYPES = frozenset({"interval", "cron", "webhook", "startup"})

_RECOMMENDED_TRIGGER_PRESETS = [
    {
        "key": "startup_runtime_health",
        "name": "启动自检摘要",
        "trigger_type": "startup",
        "job_type": "agent_query",
        "job_payload": {
            "query": "请总结当前 Koto 的后台运行状态、关键模块是否就绪，并给出简短的启动后检查结论。"
        },
        "session_id": "system",
        "enabled": True,
        "config": {},
    },
    {
        "key": "daily_task_planner",
        "name": "每日任务规划",
        "trigger_type": "cron",
        "job_type": "skill_exec",
        "job_payload": {
            "skill_id": "task_planner",
            "query": "基于今天的待办，生成一个按优先级排序的执行计划。"
        },
        "session_id": "system",
        "enabled": False,
        "config": {"time": "09:00"},
    },
    {
        "key": "downloads_auto_catalog",
        "name": "下载目录自动整理",
        "trigger_type": "interval",
        "job_type": "auto_catalog",
        "job_payload": {
            "source_dir": "C:/Users/Public/Downloads"
        },
        "session_id": "system",
        "enabled": False,
        "config": {"interval_seconds": 3600},
    },
    {
        "key": "proactive_agent_tick",
        "name": "主动交互巡检",
        "trigger_type": "interval",
        "job_type": "proactive_tick",
        "job_payload": {},
        "session_id": "system",
        "enabled": True,
        "config": {"interval_seconds": 1800},   # 每 30 分钟检查一次
    },
]


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class TriggerSpec:
    """触发器配置条目。"""
    trigger_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""
    trigger_type: str = "webhook"          # interval / cron / webhook / startup
    job_type: str = ""                     # 对应 JobRunner 的 job_type
    job_payload: Dict[str, Any] = field(default_factory=dict)
    session_id: str = "system"
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)  # 类型相关配置

    # 运行时追踪（持久化）
    last_run: Optional[str] = None
    run_count: int = 0
    last_error: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="milliseconds")
    )

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "TriggerSpec":
        valid_keys = TriggerSpec.__dataclass_fields__.keys()
        return TriggerSpec(**{k: v for k, v in d.items() if k in valid_keys})


# ============================================================================
# TriggerRegistry
# ============================================================================

class TriggerRegistry:
    """
    统一触发器注册与调度中心。

    - 持久化：触发器配置存储到 config/triggers.json
    - 调度：后台线程每 15 秒扫描一次，判断是否触发
    - 解耦：触发时通过 get_job_runner().submit() 提交作业
    """

    def __init__(self):
        self._triggers: Dict[str, TriggerSpec] = {}
        self._lock = threading.Lock()
        self._running = False
        self._scheduler_thread: Optional[threading.Thread] = None
        self._load_persisted()
        self.ensure_recommended_triggers()

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, daemon=True, name="koto_trigger_scheduler"
        )
        self._scheduler_thread.start()
        # 延迟触发 startup 类型（等应用完全启动）
        threading.Thread(
            target=self._fire_startup_triggers, daemon=True
        ).start()
        logger.info("[TriggerRegistry] ✅ 调度器已启动，已加载 %d 个触发器",
                    len(self._triggers))

    def stop(self):
        self._running = False

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def register(self, spec: TriggerSpec) -> TriggerSpec:
        """注册触发器。若 trigger_id 已存在则覆盖。"""
        if spec.trigger_type not in _VALID_TRIGGER_TYPES:
            raise ValueError(
                f"无效 trigger_type: {spec.trigger_type!r}. "
                f"允许值: {sorted(_VALID_TRIGGER_TYPES)}"
            )
        with self._lock:
            self._triggers[spec.trigger_id] = spec
        self._persist()
        logger.info(
            "[TriggerRegistry] 注册: %s (%s)", spec.name, spec.trigger_type
        )
        return spec

    def remove(self, trigger_id: str) -> bool:
        """删除触发器。返回是否存在并删除成功。"""
        with self._lock:
            removed = self._triggers.pop(trigger_id, None)
        if removed:
            self._persist()
        return removed is not None

    def get(self, trigger_id: str) -> Optional[TriggerSpec]:
        return self._triggers.get(trigger_id)

    def list_all(self) -> List[TriggerSpec]:
        with self._lock:
            return list(self._triggers.values())

    def enable(self, trigger_id: str, enabled: bool = True):
        with self._lock:
            if trigger_id in self._triggers:
                self._triggers[trigger_id].enabled = enabled
        self._persist()

    def update(self, trigger_id: str, **kwargs) -> Optional[TriggerSpec]:
        """更新触发器字段（name / job_payload / config / enabled 等）。"""
        with self._lock:
            spec = self._triggers.get(trigger_id)
            if not spec:
                return None
            allowed = {"name", "job_type", "job_payload", "session_id",
                       "enabled", "config", "trigger_type"}
            for k, v in kwargs.items():
                if k in allowed:
                    setattr(spec, k, v)
        self._persist()
        return spec

    def list_templates(self) -> List[Dict[str, Any]]:
        """Return curated trigger templates for first-run automation setup."""
        return [dict(item) for item in _RECOMMENDED_TRIGGER_PRESETS]

    def ensure_recommended_triggers(self, force: bool = False) -> Dict[str, Any]:
        """Seed recommended triggers once, keeping user changes intact unless forced."""
        created = []
        skipped = []

        existing_by_key = {}
        with self._lock:
            for trigger in self._triggers.values():
                preset_key = (trigger.job_payload or {}).get("preset_key")
                if preset_key:
                    existing_by_key[preset_key] = trigger

        for preset in _RECOMMENDED_TRIGGER_PRESETS:
            preset_key = preset["key"]
            existing = existing_by_key.get(preset_key)
            if existing and not force:
                skipped.append(preset_key)
                continue

            if existing and force:
                self.remove(existing.trigger_id)

            spec = TriggerSpec(
                name=preset["name"],
                trigger_type=preset["trigger_type"],
                job_type=preset["job_type"],
                job_payload={
                    **(preset.get("job_payload") or {}),
                    "preset_key": preset_key,
                },
                session_id=preset.get("session_id", "system"),
                enabled=bool(preset.get("enabled", True)),
                config=preset.get("config") or {},
            )
            self.register(spec)
            created.append(preset_key)

        return {"created": created, "skipped": skipped}

    # ── 触发 ──────────────────────────────────────────────────────────────────

    def fire(self, trigger_id: str) -> Optional[str]:
        """手动触发一个触发器。返回 task_id，触发器不存在或禁用时返回 None。"""
        spec = self._triggers.get(trigger_id)
        if not spec or not spec.enabled:
            return None
        return self._dispatch(spec)

    def _dispatch(self, spec: TriggerSpec) -> Optional[str]:
        """提交作业并更新运行记录。"""
        try:
            from app.core.jobs.job_runner import JobSpec, get_job_runner
            job_spec = JobSpec(
                job_type=spec.job_type,
                payload=spec.job_payload,
                session_id=spec.session_id or "trigger",
                metadata={
                    "trigger_id": spec.trigger_id,
                    "trigger_name": spec.name,
                },
            )
            task_id = get_job_runner().submit(job_spec)
            with self._lock:
                spec.last_run = datetime.now().isoformat(timespec="milliseconds")
                spec.run_count += 1
                spec.last_error = None
            self._persist()
            logger.info("[TriggerRegistry] ▶ %s → task %s", spec.name, task_id[:8])
            return task_id
        except Exception as exc:
            with self._lock:
                spec.last_error = str(exc)[:300]
            logger.error("[TriggerRegistry] 触发失败 %s: %s", spec.name, exc)
            try:
                from app.core.ops.ops_event_bus import get_ops_bus
                get_ops_bus().emit("scheduler_skipped", {
                    "trigger_id": spec.trigger_id,
                    "trigger_name": spec.name,
                    "error": str(exc)[:200],
                })
            except Exception:
                pass
            return None

    # ── 调度循环 ──────────────────────────────────────────────────────────────

    def _scheduler_loop(self):
        while self._running:
            now = time.time()
            with self._lock:
                specs = list(self._triggers.values())
            for spec in specs:
                if spec.enabled and self._should_fire(spec, now):
                    threading.Thread(
                        target=self._dispatch, args=(spec,), daemon=True
                    ).start()
            time.sleep(15)

    def _should_fire(self, spec: TriggerSpec, now: float) -> bool:
        if spec.trigger_type == "interval":
            interval = max(60, int(spec.config.get("interval_seconds", 3600)))
            if not spec.last_run:
                return True
            try:
                last = datetime.fromisoformat(spec.last_run).timestamp()
                return (now - last) >= interval
            except Exception:
                return True

        if spec.trigger_type == "cron":
            time_str = spec.config.get("time", "00:00")
            now_dt = datetime.now()
            try:
                hh, mm = map(int, time_str.split(":"))
            except Exception:
                return False
            if now_dt.hour != hh or now_dt.minute != mm:
                return False
            if spec.last_run:
                try:
                    last_dt = datetime.fromisoformat(spec.last_run)
                    if last_dt.date() == now_dt.date():
                        return False  # 今天已执行过
                except Exception:
                    pass
            return True

        # webhook / startup 不由调度器主动触发
        return False

    def _fire_startup_triggers(self):
        time.sleep(8)  # 等应用完全初始化
        for spec in list(self._triggers.values()):
            if spec.trigger_type == "startup" and spec.enabled:
                logger.info("[TriggerRegistry] 🚀 startup 触发: %s", spec.name)
                self._dispatch(spec)

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _persist(self):
        try:
            _BASE.mkdir(parents=True, exist_ok=True)
            data = [t.to_dict() for t in self._triggers.values()]
            _TRIGGERS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("[TriggerRegistry] 持久化失败: %s", exc)

    def _load_persisted(self):
        if not _TRIGGERS_FILE.exists():
            return
        try:
            data = json.loads(_TRIGGERS_FILE.read_text(encoding="utf-8"))
            for d in data:
                spec = TriggerSpec.from_dict(d)
                self._triggers[spec.trigger_id] = spec
            logger.info("[TriggerRegistry] 加载 %d 个持久化触发器", len(self._triggers))
        except Exception as exc:
            logger.warning("[TriggerRegistry] 加载触发器失败: %s", exc)


# ============================================================================
# 单例
# ============================================================================

_registry: Optional[TriggerRegistry] = None
_reg_lock = threading.Lock()


def get_trigger_registry() -> TriggerRegistry:
    """获取全局 TriggerRegistry 单例，首次调用时自动启动调度器。"""
    global _registry
    if _registry is None:
        with _reg_lock:
            if _registry is None:
                _registry = TriggerRegistry()
                _registry.start()
    return _registry

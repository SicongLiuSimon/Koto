"""
distill_manager.py — 蒸馏训练任务管理器
==========================================
管理 LoRA 训练任务的生命周期：提交 → 排队 → 训练 → 完成/失败。
支持 SSE 实时进度流推送，前端可直接订阅。

用法:
    mgr = DistillManager.instance()

    # 提交任务（立即返回 job_id）
    job_id = mgr.submit("email_writer")

    # SSE 推送（在 Flask route 中 yield events）
    for event in mgr.stream_progress(job_id):
        yield event

    # 查询状态
    job = mgr.get_job(job_id)

    # 列出所有任务
    jobs = mgr.list_jobs()
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


# ── 状态枚举 ──────────────────────────────────────────────────────────────────

class JobStatus:
    QUEUED    = "queued"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


# ── 任务数据结构 ───────────────────────────────────────────────────────────────

@dataclass
class TrainingJob:
    job_id: str
    skill_id: str
    status: str = JobStatus.QUEUED
    pct: float = 0.0
    current_step: int = 0
    current_loss: Optional[float] = None
    num_samples: int = 0
    eval_loss: Optional[float] = None
    adapter_path: Optional[str] = None
    base_model: str = ""
    error: Optional[str] = None
    skeleton: bool = False
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_s: float = 0.0
    logs: List[str] = field(default_factory=list)
    config_override: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def add_log(self, msg: str) -> None:
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(self.logs) > 500:          # 保留最近 500 条
            self.logs = self.logs[-500:]


# ── 管理器 ────────────────────────────────────────────────────────────────────

class DistillManager:
    """训练任务队列 + 进度追踪单例。"""

    _instance: Optional["DistillManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._jobs: Dict[str, TrainingJob] = {}               # job_id → job
        self._queues: Dict[str, queue.Queue] = {}             # job_id → event queue
        self._worker_thread: Optional[threading.Thread] = None
        self._task_queue: queue.Queue = queue.Queue()         # 待训练的 job_id
        self._start_worker()

    @classmethod
    def instance(cls) -> "DistillManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    # ── 提交 ──────────────────────────────────────────────────────────────────

    def submit(
        self,
        skill_id: str,
        config_override: Optional[Dict[str, Any]] = None,
        dataset_path: Optional[str] = None,
    ) -> str:
        """
        提交一个训练任务，立即返回 job_id。
        任务进入队列，worker 线程按序执行。

        重复提交同一 skill 时，若仍在队列/运行中则返回已有 job_id。
        """
        # 防止重复提交
        for job in self._jobs.values():
            if (job.skill_id == skill_id
                    and job.status in (JobStatus.QUEUED, JobStatus.RUNNING)):
                logger.info(f"[DistillManager] skill={skill_id} 已有进行中的任务 {job.job_id}")
                return job.job_id

        job_id = str(uuid.uuid4())[:8]
        job = TrainingJob(
            job_id=job_id,
            skill_id=skill_id,
            config_override=config_override or {},
        )
        job.add_log(f"任务提交  skill={skill_id}")
        self._jobs[job_id] = job
        self._queues[job_id] = queue.Queue()
        self._task_queue.put((job_id, dataset_path))
        logger.info(f"[DistillManager] 任务已入队 job_id={job_id} skill={skill_id}")
        return job_id

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Optional[TrainingJob]:
        return self._jobs.get(job_id)

    def list_jobs(self, skill_id: Optional[str] = None) -> List[Dict[str, Any]]:
        jobs = list(self._jobs.values())
        if skill_id:
            jobs = [j for j in jobs if j.skill_id == skill_id]
        return [j.to_dict() for j in sorted(jobs, key=lambda j: j.created_at, reverse=True)]

    def cancel(self, job_id: str) -> bool:
        """取消排队中的任务（运行中的任务无法取消）。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            self._push_event(job_id, "cancelled", {"msg": "任务已取消"})
            return True
        return False

    # ── SSE 流 ────────────────────────────────────────────────────────────────

    def stream_progress(self, job_id: str, timeout: float = 7200.0) -> Generator[str, None, None]:
        """
        生成器：产出 SSE 格式的进度事件字符串，直到任务结束或超时。

        在 Flask route 中使用:
            return Response(stream_with_context(mgr.stream_progress(job_id)), ...)
        """
        job = self._jobs.get(job_id)
        if not job:
            yield f"data: {json.dumps({'error': 'job not found', 'job_id': job_id})}\n\n"
            return

        q = self._queues.get(job_id)
        if not q:
            yield f"data: {json.dumps(job.to_dict())}\n\n"
            return

        deadline = time.time() + timeout
        # 先推送当前状态快照
        yield f"data: {json.dumps(job.to_dict())}\n\n"

        while time.time() < deadline:
            try:
                evt = q.get(timeout=2.0)
                yield f"data: {json.dumps(evt)}\n\n"
                if evt.get("status") in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
                    break
            except queue.Empty:
                # 心跳（保持连接）
                yield f": ping\n\n"
                if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
                    break

    # ── 内部：Worker ──────────────────────────────────────────────────────────

    def _start_worker(self) -> None:
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="distill_worker",
        )
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        logger.info("[DistillManager] Worker 线程已启动")
        while True:
            try:
                job_id, dataset_path = self._task_queue.get(timeout=5)
            except queue.Empty:
                continue

            job = self._jobs.get(job_id)
            if not job or job.status == JobStatus.CANCELLED:
                continue

            self._run_job(job, dataset_path)

    def _run_job(self, job: TrainingJob, dataset_path: Optional[str]) -> None:
        job.status = JobStatus.RUNNING
        job.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        job.add_log("开始训练")
        self._push_event(job.job_id, "running", {"msg": "训练开始", "pct": 0})

        def _progress_cb(info: Dict[str, Any]) -> None:
            msg  = info.get("msg", "")
            pct  = info.get("pct", job.pct)
            loss = info.get("loss")
            step = info.get("step", job.current_step)
            job.pct = pct
            job.current_step = step
            if loss is not None:
                job.current_loss = loss
            job.add_log(msg)
            self._push_event(job.job_id, "progress", {
                "msg": msg, "pct": pct, "step": step, "loss": loss
            })

        try:
            from app.core.learning.lora_pipeline import LoRAPipeline, TrainingConfig
            cfg = TrainingConfig.detect_and_build()
            if job.config_override:
                for k, v in job.config_override.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)

            pipeline = LoRAPipeline(config=cfg)
            result = pipeline.train(
                skill_id=job.skill_id,
                dataset_path=dataset_path,
                progress_cb=_progress_cb,
            )

            # 写回结果
            job.num_samples  = result.get("num_samples", 0)
            job.eval_loss    = result.get("eval_loss")
            job.adapter_path = result.get("adapter_path")
            job.base_model   = result.get("base_model", cfg.base_model)
            job.duration_s   = result.get("duration_s", 0)
            job.skeleton     = result.get("skeleton", False)
            job.pct          = 100.0

            if result.get("success"):
                # 自动注册适配器
                if not job.skeleton:
                    pipeline.register_as_adapter(
                        skill_id=job.skill_id,
                        adapter_path=job.adapter_path,
                        num_samples=job.num_samples,
                        eval_loss=job.eval_loss,
                    )
                job.status = JobStatus.DONE
                job.add_log(f"✅ 训练完成  loss={job.eval_loss}  samples={job.num_samples}")
                self._push_event(job.job_id, "done", {
                    "status": JobStatus.DONE,
                    "msg": f"训练完成  loss={job.eval_loss}",
                    "pct": 100,
                    "adapter_path": job.adapter_path,
                    "eval_loss": job.eval_loss,
                    "duration_s": job.duration_s,
                    "skeleton": job.skeleton,
                })
            else:
                raise RuntimeError(result.get("error", "训练返回 success=False"))

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.add_log(f"❌ 训练失败: {e}")
            logger.error(f"[DistillManager] job={job.job_id} 失败: {e}", exc_info=True)
            self._push_event(job.job_id, "failed", {
                "status": JobStatus.FAILED,
                "msg": f"训练失败: {e}",
                "pct": job.pct,
            })
        finally:
            job.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def _push_event(self, job_id: str, event_type: str, data: Dict[str, Any]) -> None:
        q = self._queues.get(job_id)
        if q:
            payload = {"event": event_type, "job_id": job_id, **data}
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

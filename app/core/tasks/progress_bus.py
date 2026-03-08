# -*- coding: utf-8 -*-
"""
Koto Universal Progress Bus
============================
全局进度事件总线：统一所有任务类型（Agent 对话、文件生成、代码执行、调度任务等）
的实时进度上报与订阅，支持 SSE 推送到前端。

设计原则：
  - 发布者（Agent、PPT 生成器、调度器等）调用 get_progress_bus().publish(event)
  - 消费者（Flask SSE 路由）通过 subscribe / stream_events 订阅指定 task_id
  - 零依赖循环（不 import task_ledger，在 task_ledger 内懒加载此模块）
  - 线程安全，基于内存队列（不持久化，进程重启后 SSE 历史丢失）

与旧 web/progress_tracker.py 的关系：
  - 旧模块专注文档生成，可继续使用
  - 本模块是全局总线，旧模块可注册为一个订阅者
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

# 单例
_bus_instance: Optional["ProgressBus"] = None
_bus_lock = threading.Lock()


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class ProgressEvent:
    """
    进度事件（所有任务类型的统一进度消息）

    event_type 可选值:
      "status_change"  — 任务状态变更（pending→running→…）
      "step"           — Agent 执行一个步骤（THOUGHT/ACTION/OBSERVATION）
      "progress"       — 通用进度百分比更新（文件生成等）
      "interrupt"      — 被暂停，等待人工介入
      "resume"         — 从暂停恢复
      "error"          — 发生错误
    """
    task_id: str
    session_id: str = ""
    event_type: str = "progress"       # 见上方 event_type 可选值
    status: str = ""                   # TaskStatus value
    message: str = ""
    progress: int = 0                  # 0-100
    step_type: Optional[str] = None    # THOUGHT / ACTION / OBSERVATION / ANSWER / ERROR
    tool_name: Optional[str] = None    # 若 step_type=ACTION，工具名
    detail: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))

    def to_sse(self, event_name: str = "progress") -> str:
        """格式化为 SSE 消息文本（两个换行结尾）。"""
        data = json.dumps(asdict(self), ensure_ascii=False)
        return f"event: {event_name}\ndata: {data}\n\n"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# ProgressBus
# ============================================================================

class ProgressBus:
    """
    全局进度事件总线。

    发布方::

        from app.core.tasks.progress_bus import get_progress_bus, ProgressEvent
        bus = get_progress_bus()
        bus.publish(ProgressEvent(task_id="xxx", message="工具调用中…", progress=40))

    SSE 端点消费方::

        from flask import Response, stream_with_context
        bus = get_progress_bus()

        @app.get("/api/tasks/<task_id>/stream")
        def stream(task_id):
            def gen():
                yield from bus.stream_events(task_id, timeout=120)
            return Response(stream_with_context(gen()), mimetype="text/event-stream")

    内存回调订阅::

        bus.subscribe("my-task-id", lambda e: print(e.message))
    """

    # 每个 task_id 保留最近 N 条历史事件（供新连接的 SSE 客户端回放）
    _REPLAY_BUFFER = 20
    # 每个 SSE 流最多保留 N 个待发队列项
    _QUEUE_MAX = 200

    def __init__(self):
        # {task_id: [ProgressEvent, ...]}  最近 N 条历史
        self._history: Dict[str, List[ProgressEvent]] = {}
        # {task_id: [Queue, ...]}  每个活跃 SSE 客户端的队列
        self._sse_queues: Dict[str, List[queue.Queue]] = {}
        # {task_id: [Callable]}  内存回调订阅
        self._callbacks: Dict[str, List[Callable[[ProgressEvent], None]]] = {}
        self._lock = threading.Lock()
        logger.info("[ProgressBus] ✅ 全局进度总线初始化")

    # ── 发布 ──────────────────────────────────────────────────────────────────

    def publish(self, event: ProgressEvent):
        """发布一条进度事件，分发给所有订阅方。"""
        tid = event.task_id
        with self._lock:
            # 1. 存入历史缓冲
            buf = self._history.setdefault(tid, [])
            buf.append(event)
            if len(buf) > self._REPLAY_BUFFER:
                del buf[0]

            # 2. 推入所有 SSE 队列
            dead = []
            for q in self._sse_queues.get(tid, []):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._sse_queues[tid].remove(q)
                except ValueError:
                    pass

            # 3. 触发内存回调（在锁外执行，避免死锁）
            cbs = list(self._callbacks.get(tid, []))

        for cb in cbs:
            try:
                cb(event)
            except Exception as e:
                logger.debug(f"[ProgressBus] 回调异常: {e}")

    def publish_step(
        self,
        task_id: str,
        session_id: str,
        step_type: str,
        content: str,
        progress: int = 0,
        tool_name: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ):
        """便捷方法：发布一个 Agent 步骤事件。"""
        self.publish(ProgressEvent(
            task_id=task_id,
            session_id=session_id,
            event_type="step",
            step_type=step_type,
            message=content[:300],
            progress=progress,
            tool_name=tool_name,
            detail=detail,
        ))

    # ── SSE 流 ────────────────────────────────────────────────────────────────

    def stream_events(
        self,
        task_id: str,
        timeout: float = 300.0,
        replay: bool = True,
    ) -> Generator[str, None, None]:
        """
        生成器，供 Flask SSE Response 使用。

        先回放最近历史事件，然后阻塞等待新事件直到任务结束或超时。

        Args:
            task_id: 要订阅的任务 ID
            timeout: 最长等待时间（秒），0 = 永不超时
            replay: 是否先回放历史缓冲
        """
        q: queue.Queue = queue.Queue(maxsize=self._QUEUE_MAX)

        with self._lock:
            history_snapshot = list(self._history.get(task_id, []))
            self._sse_queues.setdefault(task_id, []).append(q)

        try:
            # 回放历史
            if replay:
                for ev in history_snapshot:
                    yield ev.to_sse()

            # 实时流
            deadline = (time.monotonic() + timeout) if timeout > 0 else None
            while True:
                remaining = (deadline - time.monotonic()) if deadline else 5.0
                if remaining <= 0:
                    yield "event: timeout\ndata: {}\n\n"
                    break
                try:
                    ev = q.get(timeout=min(remaining, 5.0))
                    yield ev.to_sse()
                    # 若任务已终止，再 flush 一次后退出
                    if ev.status in ("completed", "failed", "cancelled"):
                        time.sleep(0.1)
                        while not q.empty():
                            yield q.get_nowait().to_sse()
                        break
                except queue.Empty:
                    # 心跳保持连接
                    yield ": heartbeat\n\n"
        finally:
            with self._lock:
                try:
                    self._sse_queues[task_id].remove(q)
                except (KeyError, ValueError):
                    pass

    def get_history(self, task_id: str) -> List[ProgressEvent]:
        """返回 task_id 最近的进度事件历史。"""
        with self._lock:
            return list(self._history.get(task_id, []))

    # ── 内存订阅 ──────────────────────────────────────────────────────────────

    def subscribe(
        self,
        task_id: str,
        callback: Callable[[ProgressEvent], None],
    ):
        """注册内存回调，每次有新事件时调用。"""
        with self._lock:
            self._callbacks.setdefault(task_id, []).append(callback)

    def unsubscribe(self, task_id: str, callback: Callable):
        with self._lock:
            cbs = self._callbacks.get(task_id, [])
            try:
                cbs.remove(callback)
            except ValueError:
                pass

    # ── 全局广播（不绑定 task_id）──────────────────────────────────────────────

    def broadcast_to_session(self, session_id: str, event: ProgressEvent):
        """
        向某 session 下所有已注册 task 广播事件。
        （场景：session 级别的提醒，如资源不足警告）
        """
        with self._lock:
            all_tids = list(self._sse_queues.keys()) + list(self._callbacks.keys())
        for tid in set(all_tids):
            ev_copy = ProgressEvent(**{**asdict(event), "task_id": tid})
            self.publish(ev_copy)

    # ── 清理 ──────────────────────────────────────────────────────────────────

    def cleanup(self, task_id: str, delay: float = 10.0):
        """延迟清理某任务的订阅和历史（给客户端时间接收最后事件）。"""
        def _do_cleanup():
            time.sleep(delay)
            with self._lock:
                self._history.pop(task_id, None)
                self._sse_queues.pop(task_id, None)
                self._callbacks.pop(task_id, None)
        threading.Thread(target=_do_cleanup, daemon=True).start()

    def active_tasks(self) -> List[str]:
        """返回当前有活跃 SSE 客户端的 task_id 列表。"""
        with self._lock:
            return [tid for tid, qs in self._sse_queues.items() if qs]


# ============================================================================
# 单例访问
# ============================================================================

def get_progress_bus() -> ProgressBus:
    """返回全局 ProgressBus 单例（线程安全）。"""
    global _bus_instance
    with _bus_lock:
        if _bus_instance is None:
            _bus_instance = ProgressBus()
    return _bus_instance

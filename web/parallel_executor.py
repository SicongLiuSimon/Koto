# -*- coding: utf-8 -*-
"""
🚀 Koto 并行任务执行系统

这是一个完整的任务队列、资源管理、优先级调度、异常恢复系统。
支持多并发、智能调度、资源感知、熔断重试等。

Features:
  - 多优先级队列（CRITICAL、HIGH、NORMAL、LOW）
  - 基于优先级的智能调度，防止饿死
  - 资源感知调度（内存、CPU、API配额）
  - 异常自动重试（指数退避、熔断器）
  - 任务快照恢复
  - 实时监控和进度追踪
  - 线程安全的操作
"""

import json
import logging
import queue
import threading
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import psutil

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举定义
# ============================================================================


class Priority(Enum):
    """任务优先级"""

    CRITICAL = 4  # 立即执行：中断/取消/系统诊断
    HIGH = 3  # 快速执行：文件操作/代码执行/应用启动
    NORMAL = 2  # 标准执行：普通对话/图像分析
    LOW = 1  # 后台执行：深度研究/大文件处理

    def __lt__(self, other):
        return self.value < other.value

    def __le__(self, other):
        return self.value <= other.value

    def __gt__(self, other):
        return self.value > other.value

    def __ge__(self, other):
        return self.value >= other.value


class TaskStatus(Enum):
    """任务状态"""

    PENDING = "pending"  # 等待执行
    RUNNABLE = "runnable"  # 可以执行（资源充足）
    RUNNING = "running"  # 正在执行
    PAUSED = "paused"  # 暂停（资源不足）
    COMPLETED = "completed"  # 执行完成
    FAILED = "failed"  # 执行失败
    CANCELLED = "cancelled"  # 被取消
    RETRYING = "retrying"  # 重试中


class TaskType(Enum):
    """任务类型（用于资源分配）"""

    CHAT = "chat"  # 对话（低资源）
    CODE_EXECUTION = "code_execution"  # 代码执行（中等资源）
    FILE_OPERATION = "file_operation"  # 文件操作（中等资源）
    SYSTEM_COMMAND = "system_command"  # 系统命令（低资源）
    IMAGE_PROCESSING = "image_processing"  # 图像处理（高资源）
    DOCUMENT_GENERATION = "document_generation"  # 文档生成（高资源）
    RESEARCH = "research"  # 研究/分析（低资源，但长耗时）
    MULTI_STEP = "multi_step"  # 多步任务（可变资源）


# ============================================================================
# 任务数据结构
# ============================================================================


@dataclass
class Task:
    """任务对象"""

    id: str  # 唯一ID
    session_id: str  # 所属会话
    type: TaskType  # 任务类型
    priority: Priority  # 优先级

    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    user_input: str = ""  # 用户输入
    payload: Dict[str, Any] = field(default_factory=dict)  # 额外数据

    status: TaskStatus = TaskStatus.PENDING  # 当前状态
    result: Optional[Any] = None  # 执行结果
    error: Optional[str] = None  # 错误信息

    retry_count: int = 0  # 重试次数
    max_retries: int = 3  # 最大重试次数

    dependencies: Set[str] = field(default_factory=set)  # 依赖的任务ID

    # 估计的资源需求
    estimated_memory_mb: int = 100  # 预计内存使用 MB
    estimated_api_calls: int = 1  # 预计API调用次数

    # 标记和回调
    on_progress: Optional[Callable[[str], None]] = None  # 进度回调
    on_complete: Optional[Callable[[Any], None]] = None  # 完成回调
    on_error: Optional[Callable[[Exception], None]] = None  # 错误回调

    abort_event: threading.Event = field(default_factory=threading.Event)  # 中止事件

    @property
    def elapsed_time(self) -> float:
        """已耗时（秒）"""
        if self.started_at is None:
            return 0
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def is_timeout(self) -> bool:
        """是否超时（默认30秒）"""
        return self.elapsed_time > 30 and self.status == TaskStatus.RUNNING

    @property
    def is_aborted(self) -> bool:
        """是否被中止"""
        return self.abort_event.is_set()

    def abort(self):
        """中止任务"""
        self.abort_event.set()
        self.status = TaskStatus.CANCELLED

    def to_dict(self) -> Dict:
        """转换为JSON序列化的字典"""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "type": self.type.value,
            "priority": self.priority.name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "user_input": self.user_input[:100],  # 截断
            "elapsed_time": self.elapsed_time,
            "retry_count": self.retry_count,
            "error": self.error[:100] if self.error else None,  # 截断
        }


# ============================================================================
# 优先级队列管理
# ============================================================================


class TaskQueueManager:
    """
    优先级任务队列管理器

    使用分层队列结构：
    - CRITICAL 队列（最高优先级，立即处理）
    - HIGH 队列（重要任务，快速处理）
    - NORMAL 队列（标准任务）
    - LOW 队列（后台任务）

    防止饿死：round-robin 在 NORMAL 和 LOW 队列间轮换
    """

    def __init__(self, max_queue_size: int = 500):
        self.max_queue_size = max_queue_size

        self.queues = {
            Priority.CRITICAL: queue.PriorityQueue(),
            Priority.HIGH: queue.PriorityQueue(),
            Priority.NORMAL: queue.PriorityQueue(),
            Priority.LOW: queue.PriorityQueue(),
        }

        # 任务ID -> Task 的索引
        self.tasks: Dict[str, Task] = {}

        # 会话ID -> [Task IDs]
        self.session_tasks: Dict[str, List[str]] = defaultdict(list)

        # 锁
        self.lock = threading.RLock()
        self.not_empty = threading.Condition(self.lock)

        # Round-robin 计数器（用于 NORMAL/LOW 队列的公平调度）
        self._rr_counter = 0

    def submit(self, task: Task) -> str:
        """
        提交任务到队列

        Returns: task.id
        """
        with self.lock:
            # 检查队列大小
            total_size = sum(q.qsize() for q in self.queues.values())
            if total_size >= self.max_queue_size:
                raise RuntimeError(
                    f"Task queue is full ({total_size}/{self.max_queue_size})"
                )

            # 记录任务
            self.tasks[task.id] = task
            self.session_tasks[task.session_id].append(task.id)

            # 放入相应的队列（使用时间戳确保 FIFO 顺序）
            priority_value = task.priority.value
            timestamp = time.time()
            self.queues[task.priority].put((priority_value, timestamp, task.id))

            logger.info(
                f"[QUEUE] Task submitted: {task.id} (priority={task.priority.name}, session={task.session_id})"
            )

            # 通知等待者
            self.not_empty.notify()

            return task.id

    def get_next(self, timeout: float = 1.0) -> Optional[Task]:
        """
        获取下一个可执行的任务（遵循优先级）

        优先级顺序：
        1. CRITICAL（如果有）
        2. HIGH（如果有）
        3. NORMAL 和 LOW 轮换（防止饿死）

        Returns: Task or None
        """
        with self.not_empty:
            # 检查 CRITICAL 队列
            if not self.queues[Priority.CRITICAL].empty():
                _, _, task_id = self.queues[Priority.CRITICAL].get_nowait()
                return self.tasks.pop(task_id)

            # 检查 HIGH 队列
            if not self.queues[Priority.HIGH].empty():
                _, _, task_id = self.queues[Priority.HIGH].get_nowait()
                return self.tasks.pop(task_id)

            # NORMAL 和 LOW 轮换（3:1比例，NORMAL 更多）
            self._rr_counter += 1
            if self._rr_counter % 4 == 0:  # 每4次选1次LOW
                if not self.queues[Priority.LOW].empty():
                    _, _, task_id = self.queues[Priority.LOW].get_nowait()
                    return self.tasks.pop(task_id)

            # 优先 NORMAL
            if not self.queues[Priority.NORMAL].empty():
                _, _, task_id = self.queues[Priority.NORMAL].get_nowait()
                return self.tasks.pop(task_id)

            # 如果 NORMAL 也空了，再试 LOW
            if not self.queues[Priority.LOW].empty():
                _, _, task_id = self.queues[Priority.LOW].get_nowait()
                return self.tasks.pop(task_id)

            # 所有队列都空，等待
            try:
                self.not_empty.wait(timeout=timeout)
            except Exception as e:
                logger.debug("Thread wait interrupted: %s", e)
                pass

            return None

    def cancel(self, task_id: str) -> bool:
        """取消任务"""
        with self.lock:
            if task_id not in self.tasks:
                return False

            task = self.tasks[task_id]
            task.status = TaskStatus.CANCELLED
            task.abort_event.set()

            # 从会话列表中移除
            if task.session_id in self.session_tasks:
                self.session_tasks[task.session_id].remove(task_id)

            logger.info(f"[QUEUE] Task cancelled: {task_id}")
            return True

    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务对象（不移除）"""
        with self.lock:
            return self.tasks.get(task_id)

    def get_session_tasks(self, session_id: str) -> List[Task]:
        """获取会话的所有任务"""
        with self.lock:
            return [
                self.tasks[tid]
                for tid in self.session_tasks.get(session_id, [])
                if tid in self.tasks
            ]

    def get_stats(self) -> Dict:
        """获取队列统计信息"""
        with self.lock:
            return {
                "total_tasks": len(self.tasks),
                "pending": sum(
                    1 for t in self.tasks.values() if t.status == TaskStatus.PENDING
                ),
                "running": sum(
                    1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING
                ),
                "critical": self.queues[Priority.CRITICAL].qsize(),
                "high": self.queues[Priority.HIGH].qsize(),
                "normal": self.queues[Priority.NORMAL].qsize(),
                "low": self.queues[Priority.LOW].qsize(),
            }


# ============================================================================
# 资源管理
# ============================================================================


class ResourceManager:
    """
    资源使用追踪和限制

    管理：
    - 内存使用（soft 2GB, hard 3GB）
    - API 调用率（3 calls/second）
    - 并发任务数（max 5）
    - 文件 I/O（max 2 ops/second）
    """

    def __init__(self):
        self.max_concurrent_tasks = 5
        self.memory_soft_limit_mb = 2048  # 2GB
        self.memory_hard_limit_mb = 3072  # 3GB
        self.api_calls_per_second = 3.0

        self.current_concurrent = 0
        self.api_call_tokens = self.api_calls_per_second
        self.last_api_token_refill = time.time()

        self.lock = threading.Lock()

    def get_memory_usage_mb(self) -> float:
        """获取当前内存使用（MB）"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except Exception as e:
            logger.debug("Failed to get memory usage: %s", e)
            return 0

    def get_cpu_usage_percent(self) -> float:
        """获取当前CPU使用率（%）"""
        try:
            return psutil.cpu_percent(interval=0.1)
        except Exception as e:
            logger.debug("Failed to get CPU usage: %s", e)
            return 0

    def can_start_task(self, task: Task) -> Tuple[bool, str]:
        """
        检查是否可以启动任务

        Returns: (can_start, reason)
        """
        with self.lock:
            # 检查并发数
            if self.current_concurrent >= self.max_concurrent_tasks:
                return (
                    False,
                    f"Max concurrent tasks reached ({self.current_concurrent}/{self.max_concurrent_tasks})",
                )

            # 检查内存
            mem_usage = self.get_memory_usage_mb()
            if mem_usage + task.estimated_memory_mb > self.memory_hard_limit_mb:
                return (
                    False,
                    f"Memory hard limit would exceed ({mem_usage:.0f}+{task.estimated_memory_mb} > {self.memory_hard_limit_mb}MB)",
                )

            if mem_usage > self.memory_soft_limit_mb:
                # 软限制被触发，只允许CRITICAL任务
                if task.priority != Priority.CRITICAL:
                    return (
                        False,
                        f"Memory soft limit exceeded ({mem_usage:.0f}MB > {self.memory_soft_limit_mb}MB), only CRITICAL allowed",
                    )

            # 检查API调用配额
            if task.estimated_api_calls > 0:
                if self.api_call_tokens < task.estimated_api_calls:
                    return False, f"API rate limit would exceed"

            return True, "OK"

    def acquire(self, task: Task) -> bool:
        """获取资源（启动任务时调用）"""
        with self.lock:
            can_start, reason = self.can_start_task(task)
            if not can_start:
                return False

            self.current_concurrent += 1
            self.api_call_tokens -= task.estimated_api_calls
            logger.info(
                f"[RESOURCE] Acquired for {task.id}: concurrent={self.current_concurrent}"
            )
            return True

    def release(self, task: Task):
        """释放资源（任务完成时调用）"""
        with self.lock:
            self.current_concurrent = max(0, self.current_concurrent - 1)
            logger.info(
                f"[RESOURCE] Released for {task.id}: concurrent={self.current_concurrent}"
            )

    def refill_api_tokens(self):
        """定期补充API配额（令牌桶算法）"""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_api_token_refill
            tokens_to_add = elapsed * self.api_calls_per_second
            self.api_call_tokens = min(
                self.api_calls_per_second, self.api_call_tokens + tokens_to_add
            )
            self.last_api_token_refill = now

    def get_stats(self) -> Dict:
        """获取资源统计"""
        with self.lock:
            return {
                "concurrent_tasks": self.current_concurrent,
                "max_concurrent": self.max_concurrent_tasks,
                "memory_usage_mb": self.get_memory_usage_mb(),
                "memory_soft_limit_mb": self.memory_soft_limit_mb,
                "memory_hard_limit_mb": self.memory_hard_limit_mb,
                "api_tokens": self.api_call_tokens,
                "cpu_usage_percent": self.get_cpu_usage_percent(),
            }


# ============================================================================
# 重试和熔断机制
# ============================================================================


class RetryPolicy:
    """重试策略"""

    def __init__(
        self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def get_retry_delay(self, retry_count: int) -> float:
        """
        计算重试延迟（指数退避 + jitter）

        公式: min(base * 2^count + random(0, base * 2^count), max_delay)
        """
        import random

        delay = min(self.base_delay * (2**retry_count), self.max_delay)
        jitter = random.uniform(0, delay * 0.1)  # ±10% jitter
        return delay + jitter

    def should_retry(self, task: Task, error: Exception) -> bool:
        """判断是否应该重试"""
        # 某些错误不重试（如参数错误）
        fatal_errors = (ValueError, TypeError, KeyError)
        if isinstance(error, fatal_errors):
            return False

        # 检查重试次数
        return task.retry_count < self.max_retries


class CircuitBreaker:
    """
    熔断器（防止级联故障）

    状态：
    - CLOSED: 正常工作
    - OPEN: 熔断中（拒绝请求）
    - HALF_OPEN: 尝试恢复
    """

    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        self.failure_threshold = failure_threshold  # 触发熔断的连续失败数
        self.timeout = timeout  # 熔断持续时间

        self.state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time = None
        self.lock = threading.Lock()

    def can_execute(self) -> bool:
        """检查是否可以执行"""
        with self.lock:
            if self.state == "CLOSED":
                return True

            if self.state == "OPEN":
                # 检查是否应该尝试恢复
                if time.time() - self.last_failure_time > self.timeout:
                    self.state = "HALF_OPEN"
                    logger.info("[CIRCUIT] Attempting recovery (HALF_OPEN)")
                    return True
                return False

            # HALF_OPEN - 允许执行以测试恢复
            return True

    def record_success(self):
        """记录成功，重置计数器"""
        with self.lock:
            self.failure_count = 0
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                logger.info("[CIRCUIT] Recovered (CLOSED)")

    def record_failure(self):
        """记录失败"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
                logger.warning(
                    f"[CIRCUIT] Breaker opened after {self.failure_count} failures"
                )


# ============================================================================
# 任务快照（用于恢复）
# ============================================================================


@dataclass
class TaskSnapshot:
    """任务快照（用于故障恢复）"""

    task_id: str
    session_id: str
    type: TaskType
    priority: Priority
    user_input: str
    payload: Dict[str, Any]
    status: TaskStatus
    created_at: datetime

    def to_json(self) -> str:
        """转换为JSON"""
        return json.dumps(
            {
                "task_id": self.task_id,
                "session_id": self.session_id,
                "type": self.type.value,
                "priority": self.priority.name,
                "user_input": self.user_input,
                "payload": self.payload,
                "status": self.status.value,
                "created_at": self.created_at.isoformat(),
            }
        )

    @staticmethod
    def from_task(task: Task) -> "TaskSnapshot":
        """从任务创建快照"""
        return TaskSnapshot(
            task_id=task.id,
            session_id=task.session_id,
            type=task.type,
            priority=task.priority,
            user_input=task.user_input,
            payload=task.payload,
            status=task.status,
            created_at=task.created_at,
        )


# ============================================================================
# 任务监控
# ============================================================================


class TaskMonitor:
    """
    实时任务监控

    跟踪：
    - 所有任务的当前状态
    - 队列深度和优先级分布
    - 资源使用情况
    - 性能指标（平均响应时间、吞吐量等）
    """

    def __init__(self, queue_mgr: TaskQueueManager, resource_mgr: ResourceManager):
        self.queue_mgr = queue_mgr
        self.resource_mgr = resource_mgr
        self.completed_tasks = []
        self.failed_tasks = []
        self.lock = threading.Lock()

    def record_task_complete(self, task: Task):
        """记录任务完成"""
        with self.lock:
            self.completed_tasks.append(task)
            # 只保留最近1000个
            self.completed_tasks = self.completed_tasks[-1000:]

    def record_task_failed(self, task: Task):
        """记录任务失败"""
        with self.lock:
            self.failed_tasks.append(task)
            # 只保留最近100个
            self.failed_tasks = self.failed_tasks[-100:]

    def get_dashboard(self) -> Dict:
        """获取监控仪表板数据"""
        with self.lock:
            queue_stats = self.queue_mgr.get_stats()
            resource_stats = self.resource_mgr.get_stats()

            # 计算性能指标
            if self.completed_tasks:
                avg_time = sum(t.elapsed_time for t in self.completed_tasks) / len(
                    self.completed_tasks
                )
            else:
                avg_time = 0

            return {
                "timestamp": datetime.now().isoformat(),
                "queue": queue_stats,
                "resources": resource_stats,
                "completed_tasks": len(self.completed_tasks),
                "failed_tasks": len(self.failed_tasks),
                "avg_task_time": avg_time,
                "success_rate": (
                    len(self.completed_tasks)
                    / (len(self.completed_tasks) + len(self.failed_tasks))
                    if (self.completed_tasks or self.failed_tasks)
                    else 0
                ),
            }


# ============================================================================
# 全局实例
# ============================================================================

# 单例模式
_queue_manager = None
_resource_manager = None
_task_monitor = None
_lock = threading.Lock()


def get_queue_manager() -> TaskQueueManager:
    """获取全局任务队列管理器"""
    global _queue_manager
    if _queue_manager is None:
        with _lock:
            if _queue_manager is None:
                _queue_manager = TaskQueueManager()
    return _queue_manager


def get_resource_manager() -> ResourceManager:
    """获取全局资源管理器"""
    global _resource_manager
    if _resource_manager is None:
        with _lock:
            if _resource_manager is None:
                _resource_manager = ResourceManager()
    return _resource_manager


def get_task_monitor() -> TaskMonitor:
    """获取全局任务监控器"""
    global _task_monitor
    if _task_monitor is None:
        with _lock:
            if _task_monitor is None:
                _task_monitor = TaskMonitor(get_queue_manager(), get_resource_manager())
    return _task_monitor


# 便利函数


def submit_task(
    session_id: str,
    task_type: TaskType,
    priority: Priority,
    user_input: str,
    payload: Optional[Dict] = None,
    estimated_memory: int = 100,
) -> str:
    """
    提交任务到队列

    Args:
        session_id: 所属会话
        task_type: 任务类型
        priority: 优先级
        user_input: 用户输入
        payload: 附加数据
        estimated_memory: 估计内存使用（MB）

    Returns: task_id
    """
    task = Task(
        id=f"task_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        type=task_type,
        priority=priority,
        user_input=user_input,
        payload=payload or {},
        estimated_memory_mb=estimated_memory,
    )

    return get_queue_manager().submit(task)


def get_next_task() -> Optional[Task]:
    """获取下一个可执行的任务"""
    return get_queue_manager().get_next(timeout=1.0)


def cancel_task(task_id: str) -> bool:
    """取消任务"""
    return get_queue_manager().cancel(task_id)


def get_task_status(task_id: str) -> Optional[Task]:
    """获取任务状态"""
    return get_queue_manager().get_task(task_id)


def get_session_tasks(session_id: str) -> List[Task]:
    """获取会话的所有任务"""
    return get_queue_manager().get_session_tasks(session_id)


def get_monitor_dashboard() -> Dict:
    """获取监控仪表板"""
    monitor = get_task_monitor()
    monitor.resource_mgr.refill_api_tokens()  # 定期补充API令牌
    return monitor.get_dashboard()

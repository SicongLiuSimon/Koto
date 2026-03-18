# -*- coding: utf-8 -*-
"""
🎯 Koto 任务调度器与执行引擎

负责：
1. 从队列获取任务
2. 检查资源可用性
3. 分配资源并执行
4. 处理错误和重试
5. 记录监控数据
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from .parallel_executor import (
    CircuitBreaker,
    Priority,
    RetryPolicy,
    Task,
    TaskStatus,
    TaskType,
    get_queue_manager,
    get_resource_manager,
    get_task_monitor,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 任务执行器
# ============================================================================


class TaskExecutor:
    """
    单个任务的执行器

    生命周期：
    1. 等待资源可用
    2. 获取资源
    3. 执行任务
    4. 处理结果
    5. 释放资源
    """

    def __init__(self, task: Task, execute_fn: Callable[[Task], Any]):
        """
        Args:
            task: 要执行的任务
            execute_fn: 执行函数 (task) -> result
        """
        self.task = task
        self.execute_fn = execute_fn
        self.retry_policy = RetryPolicy(max_retries=task.max_retries)
        self.circuit_breaker = CircuitBreaker()

    def execute(self) -> bool:
        """
        执行任务

        Returns: True if successful, False if failed permanently
        """
        resource_mgr = get_resource_manager()
        monitor = get_task_monitor()

        try:
            # 检查熔断器
            if not self.circuit_breaker.can_execute():
                logger.warning(
                    f"[EXECUTOR] Circuit breaker OPEN for task {self.task.id}"
                )
                self.task.status = TaskStatus.FAILED
                self.task.error = "Circuit breaker is open"
                monitor.record_task_failed(self.task)
                return False

            # 等待资源可用
            max_wait = 30  # 最多等待30秒
            waited = 0
            while waited < max_wait:
                can_acquire, reason = resource_mgr.can_start_task(self.task)
                if can_acquire:
                    break

                logger.info(f"[EXECUTOR] Waiting for resources: {reason}")
                time.sleep(1)
                waited += 1

            if waited >= max_wait:
                logger.warning(
                    f"[EXECUTOR] Task {self.task.id} timeout waiting for resources"
                )
                self.task.status = TaskStatus.FAILED
                self.task.error = "Timeout waiting for resources"
                monitor.record_task_failed(self.task)
                return False

            # 获取资源
            if not resource_mgr.acquire(self.task):
                logger.error(
                    f"[EXECUTOR] Failed to acquire resources for {self.task.id}"
                )
                self.task.status = TaskStatus.FAILED
                self.task.error = "Failed to acquire resources"
                monitor.record_task_failed(self.task)
                return False

            try:
                # 执行任务
                self.task.status = TaskStatus.RUNNING
                self.task.started_at = datetime.now()

                logger.info(
                    f"[EXECUTOR] Starting task {self.task.id} (type={self.task.type.value})"
                )

                result = self.execute_fn(self.task)

                # 成功
                self.task.status = TaskStatus.COMPLETED
                self.task.result = result
                self.task.completed_at = datetime.now()

                logger.info(
                    f"[EXECUTOR] Task {self.task.id} completed in {self.task.elapsed_time:.2f}s"
                )

                # 记录成功
                self.circuit_breaker.record_success()
                monitor.record_task_complete(self.task)

                # 调用回调
                if self.task.on_complete:
                    try:
                        self.task.on_complete(result)
                    except Exception as e:
                        logger.error(f"[EXECUTOR] Error in on_complete callback: {e}")

                return True

            except Exception as e:
                logger.error(f"[EXECUTOR] Task {self.task.id} execution error: {e}")
                logger.error(
                    f"[EXECUTOR] Traceback: {__import__('traceback').format_exc()}"
                )

                # 判断是否应该重试
                should_retry = self.retry_policy.should_retry(self.task, e)

                if should_retry:
                    self.task.retry_count += 1
                    delay = self.retry_policy.get_retry_delay(self.task.retry_count)

                    logger.info(
                        f"[EXECUTOR] Scheduling retry {self.task.retry_count}/{self.task.max_retries} after {delay:.1f}s"
                    )

                    self.task.status = TaskStatus.RETRYING
                    self.task.error = str(e)

                    # 记录失败（但不是最终失败）
                    self.circuit_breaker.record_failure()

                    # 在延迟后重新入队
                    if delay > 0:
                        time.sleep(delay)

                    queue_mgr = get_queue_manager()
                    queue_mgr.submit(self.task)  # 重新提交

                    return False  # 表示此执行失败，但任务会重试

                else:
                    # 致命错误或超过重试次数
                    self.task.status = TaskStatus.FAILED
                    self.task.completed_at = datetime.now()
                    self.task.error = str(e)

                    logger.error(
                        f"[EXECUTOR] Task {self.task.id} failed permanently: {e}"
                    )

                    self.circuit_breaker.record_failure()
                    monitor.record_task_failed(self.task)

                    # 调用错误回调
                    if self.task.on_error:
                        try:
                            self.task.on_error(e)
                        except Exception as callback_err:
                            logger.error(
                                f"[EXECUTOR] Error in on_error callback: {callback_err}"
                            )

                    return False

            finally:
                # 释放资源
                resource_mgr.release(self.task)
                logger.info(f"[EXECUTOR] Resources released for task {self.task.id}")

        except Exception as e:
            logger.error(f"[EXECUTOR] Unexpected error in execute(): {e}")
            self.task.status = TaskStatus.FAILED
            self.task.error = f"Unexpected error: {str(e)}"
            monitor = get_task_monitor()
            monitor.record_task_failed(self.task)
            return False


# ============================================================================
# 任务调度器
# ============================================================================


class TaskScheduler:
    """
    中央任务调度器

    运行在后台线程中，不断地：
    1. 从队列取任务
    2. 检查资源
    3. 创建执行器
    4. 在线程池中执行

    支持：
    - 优先级调度
    - 资源感知
    - 动态并发控制
    """

    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.queue_mgr = get_queue_manager()
        self.resource_mgr = get_resource_manager()
        self.monitor = get_task_monitor()

        self.running = False
        self.scheduler_thread = None
        self.lock = threading.Lock()

        # 任务执行函数映射
        self.executors: dict = {}

    def register_executor(self, task_type: TaskType, execute_fn: Callable):
        """注册任务类型的执行函数"""
        self.executors[task_type] = execute_fn
        logger.info(f"[SCHEDULER] Registered executor for {task_type.value}")

    def start(self):
        """启动调度器"""
        with self.lock:
            if self.running:
                logger.warning("[SCHEDULER] Scheduler already running")
                return

            self.running = True
            self.scheduler_thread = threading.Thread(
                target=self._scheduler_loop, name="TaskScheduler", daemon=False
            )
            self.scheduler_thread.start()
            logger.info("[SCHEDULER] Started")

    def stop(self):
        """停止调度器"""
        with self.lock:
            self.running = False

        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=10)

        logger.info("[SCHEDULER] Stopped")

    def _scheduler_loop(self):
        """调度器主循环"""
        logger.info("[SCHEDULER] Loop started")

        while self.running:
            try:
                # 定期补充API令牌
                self.resource_mgr.refill_api_tokens()

                # 尝试获取任务
                task = self.queue_mgr.get_next(timeout=1.0)

                if task is None:
                    # 没有任务，继续等待
                    continue

                # 检查任务是否被取消
                if task.status == TaskStatus.CANCELLED:
                    logger.info(f"[SCHEDULER] Skipping cancelled task {task.id}")
                    continue

                # 检查是否有对应的执行函数
                execute_fn = self.executors.get(task.type)
                if execute_fn is None:
                    logger.error(
                        f"[SCHEDULER] No executor for task type {task.type.value}"
                    )
                    task.status = TaskStatus.FAILED
                    task.error = f"No executor for {task.type.value}"
                    self.monitor.record_task_failed(task)
                    continue

                # 创建执行器并执行
                executor = TaskExecutor(task, execute_fn)
                executor.execute()

            except Exception as e:
                logger.error(f"[SCHEDULER] Error in scheduler loop: {e}")
                time.sleep(1)  # 避免紧密循环

        logger.info("[SCHEDULER] Loop ended")

    def get_stats(self) -> dict:
        """获取调度器统计"""
        return {
            "running": self.running,
            "max_workers": self.max_workers,
        }


# ============================================================================
# 全局调度器实例
# ============================================================================

_global_scheduler = None


def get_scheduler() -> TaskScheduler:
    """获取全局调度器"""
    global _global_scheduler
    if _global_scheduler is None:
        _global_scheduler = TaskScheduler(max_workers=5)
    return _global_scheduler


def start_dispatcher():
    """启动并行执行系统"""
    scheduler = get_scheduler()
    scheduler.start()
    logger.info("[INIT] Parallel execution system started")


def stop_dispatcher():
    """停止并行执行系统"""
    scheduler = get_scheduler()
    scheduler.stop()
    logger.info("[INIT] Parallel execution system stopped")

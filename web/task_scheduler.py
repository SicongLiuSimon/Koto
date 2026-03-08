#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务调度系统
支持定时任务、条件触发、任务队列
"""
import os
import json
import threading
import time
import schedule  # pip install schedule
from datetime import datetime
from typing import Callable, List, Dict, Any
from enum import Enum


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    """任务优先级"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


class Task:
    """任务对象"""
    
    def __init__(
        self,
        task_id: str,
        name: str,
        action: Callable,
        args: tuple = (),
        kwargs: dict = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        schedule_type: str = "once",  # once, daily, weekly, interval
        schedule_config: dict = None
    ):
        self.task_id = task_id
        self.name = name
        self.action = action
        self.args = args
        self.kwargs = kwargs or {}
        self.priority = priority
        self.schedule_type = schedule_type
        self.schedule_config = schedule_config or {}
        
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.result = None
        self.error = None
        self.retry_count = 0
        self.max_retries = 3
        
        # 新增：取消标志
        self._cancel_flag = False
    
    def is_cancelled(self) -> bool:
        """检查任务是否被取消"""
        return self._cancel_flag or self.status == TaskStatus.CANCELLED
    
    def mark_cancelled(self):
        """标记任务为已取消"""
        self._cancel_flag = True
        self.status = TaskStatus.CANCELLED
    
    def execute(self):
        """执行任务"""
        try:
            self.status = TaskStatus.RUNNING
            self.started_at = datetime.now()
            
            self.result = self.action(*self.args, **self.kwargs)
            
            self.status = TaskStatus.COMPLETED
            self.completed_at = datetime.now()
            
            return True
        except Exception as e:
            self.status = TaskStatus.FAILED
            self.error = str(e)
            self.completed_at = datetime.now()
            
            print(f"[任务] 执行失败 {self.name}: {e}")
            return False
    
    def to_dict(self):
        """转换为字典"""
        return {
            'task_id': self.task_id,
            'name': self.name,
            'priority': self.priority.name,
            'schedule_type': self.schedule_type,
            'status': self.status.name,
            'created_at': self.created_at.isoformat(),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'retry_count': self.retry_count,
            'error': self.error
        }


class TaskScheduler:
    """任务调度器"""
    
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.task_queue: List[Task] = []
        self.running = False
        self._scheduler_thread = None
        self._worker_thread = None
        
        # 持久化文件
        import sys as _sys_ts
        if getattr(_sys_ts, 'frozen', False):
            project_root = os.path.dirname(_sys_ts.executable)
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)
        tasks_dir = os.path.join(project_root, 'workspace', 'tasks')
        os.makedirs(tasks_dir, exist_ok=True)
        self.tasks_file = os.path.join(tasks_dir, 'scheduled_tasks.json')
        
        self._load_tasks()
    
    def _load_tasks(self):
        """加载持久化的任务"""
        if os.path.exists(self.tasks_file):
            try:
                with open(self.tasks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                print(f"[调度器] 已加载 {len(data)} 个任务配置")
            except Exception as e:
                print(f"[调度器] 任务加载失败: {e}")
    
    def _save_tasks(self):
        """保存任务配置"""
        try:
            data = [task.to_dict() for task in self.tasks.values()]
            with open(self.tasks_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[调度器] 任务保存失败: {e}")
    
    def add_task(self, task: Task) -> str:
        """添加任务到队列"""
        self.tasks[task.task_id] = task
        
        # 根据优先级插入队列
        inserted = False
        for i, queued_task in enumerate(self.task_queue):
            if task.priority.value > queued_task.priority.value:
                self.task_queue.insert(i, task)
                inserted = True
                break
        
        if not inserted:
            self.task_queue.append(task)
        
        print(f"[调度器] 任务已添加: {task.name} (优先级: {task.priority.name})")
        self._save_tasks()
        
        return task.task_id
    
    def schedule_task(
        self,
        name: str,
        action: Callable,
        schedule_type: str = "daily",
        time_str: str = "09:00",
        args: tuple = (),
        kwargs: dict = None
    ) -> str:
        """
        调度定时任务
        
        Args:
            name: 任务名称
            action: 要执行的函数
            schedule_type: 调度类型 (daily, weekly, interval)
            time_str: 时间字符串 (如 "09:00")
            args: 函数参数
            kwargs: 函数关键字参数
        """
        task_id = f"scheduled_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 注册到 schedule 库
        if schedule_type == "daily":
            schedule.every().day.at(time_str).do(action, *args, **(kwargs or {}))
        elif schedule_type == "weekly":
            schedule.every().week.at(time_str).do(action, *args, **(kwargs or {}))
        elif schedule_type == "hourly":
            schedule.every().hour.do(action, *args, **(kwargs or {}))
        
        task = Task(
            task_id=task_id,
            name=name,
            action=action,
            args=args,
            kwargs=kwargs,
            schedule_type=schedule_type,
            schedule_config={'time': time_str}
        )
        
        self.tasks[task_id] = task
        self._save_tasks()
        
        print(f"[调度器] 定时任务已注册: {name} ({schedule_type} at {time_str})")
        return task_id
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.mark_cancelled()
            
            # 从队列中移除
            self.task_queue = [t for t in self.task_queue if t.task_id != task_id]
            
            print(f"[调度器] 任务已取消: {task.name} (ID: {task_id})")
            self._save_tasks()
            return True
        print(f"[调度器] 未找到任务: {task_id}")
        return False
    
    def get_task(self, task_id: str) -> Task:
        """获取任务对象（用于在流式处理中检查取消状态）"""
        return self.tasks.get(task_id)
    
    def _worker_loop(self):
        """工作线程，执行队列中的任务"""
        print("[调度器] 工作线程已启动")
        
        while self.running:
            if self.task_queue:
                task = self.task_queue.pop(0)
                
                print(f"[调度器] 执行任务: {task.name}")
                success = task.execute()
                
                if not success and task.retry_count < task.max_retries:
                    task.retry_count += 1
                    task.status = TaskStatus.PENDING
                    self.task_queue.append(task)
                    print(f"[调度器] 任务重试 ({task.retry_count}/{task.max_retries}): {task.name}")
                
                self._save_tasks()
            
            time.sleep(0.5)
    
    def _scheduler_loop(self):
        """调度线程，处理定时任务"""
        print("[调度器] 调度线程已启动")
        
        while self.running:
            schedule.run_pending()
            time.sleep(1)
    
    def start(self):
        """启动调度器"""
        if self.running:
            print("[调度器] 已在运行")
            return
        
        self.running = True
        
        # 启动工作线程
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        
        # 启动调度线程
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()
        
        print("[调度器] 已启动")
    
    def stop(self):
        """停止调度器"""
        self.running = False
        
        if self._worker_thread:
            self._worker_thread.join(timeout=2)
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=2)
        
        print("[调度器] 已停止")
    
    def get_task_status(self, task_id: str) -> Dict:
        """获取任务状态"""
        if task_id in self.tasks:
            return self.tasks[task_id].to_dict()
        return None
    
    def list_tasks(self, status: TaskStatus = None) -> List[Dict]:
        """列出所有任务"""
        tasks = []
        for task in self.tasks.values():
            if status is None or task.status == status:
                tasks.append(task.to_dict())
        return tasks


# 全局实例
_task_scheduler = None


def get_task_scheduler() -> TaskScheduler:
    """获取全局任务调度器单例"""
    global _task_scheduler
    if _task_scheduler is None:
        _task_scheduler = TaskScheduler()
    return _task_scheduler


def check_task_cancelled(task_id: str) -> bool:
    """快速检查任务是否被取消（用于流式处理）"""
    scheduler = get_task_scheduler()
    task = scheduler.get_task(task_id)
    if task:
        return task.is_cancelled()
    return False

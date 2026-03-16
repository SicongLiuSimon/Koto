"""
自动执行任务引擎 - 允许系统自动执行授权的操作

功能：
1. 任务定义和注册
2. 用户授权管理
3. 安全检查和风险评估
4. 自动任务执行
5. 执行历史和回滚
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
import os
import shutil
from pathlib import Path
import threading
import time
import logging


logger = logging.getLogger(__name__)

class AutoExecutionEngine:
    """自动执行任务引擎"""
    
    # 任务风险等级
    RISK_LEVELS = {
        'safe': {
            'level': 1,
            'description': '完全安全，无数据风险',
            'require_approval': False,
            'allow_auto_execute': True
        },
        'low': {
            'level': 2,
            'description': '低风险，可逆操作',
            'require_approval': False,
            'allow_auto_execute': True
        },
        'medium': {
            'level': 3,
            'description': '中等风险，需要用户确认',
            'require_approval': True,
            'allow_auto_execute': False
        },
        'high': {
            'level': 4,
            'description': '高风险，涉及文件删除或移动',
            'require_approval': True,
            'allow_auto_execute': False
        },
        'critical': {
            'level': 5,
            'description': '严重风险，不可逆操作',
            'require_approval': True,
            'allow_auto_execute': False
        }
    }
    
    def __init__(
        self,
        db_path: str = "config/auto_execution.db",
        workspace_root: str = "workspace",
        notification_manager=None
    ):
        """初始化自动执行引擎"""
        self.db_path = db_path
        self.workspace_root = workspace_root
        self.notification_manager = notification_manager
        
        # 注册的任务处理器
        self.task_handlers: Dict[str, Callable] = {}
        
        self._init_database()
        self._register_builtin_tasks()
        
        # 定期任务检查线程
        self.running = False
        self.check_thread = None
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 任务定义表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_definitions (
                task_type TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                description TEXT,
                risk_level TEXT NOT NULL,
                handler TEXT NOT NULL,
                params_schema TEXT,
                enabled INTEGER DEFAULT 1
            )
        """)
        
        # 用户授权表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_authorizations (
                user_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                authorized INTEGER DEFAULT 0,
                auto_execute INTEGER DEFAULT 0,
                max_executions_per_day INTEGER DEFAULT 10,
                authorized_at TIMESTAMP,
                expires_at TIMESTAMP,
                PRIMARY KEY (user_id, task_type)
            )
        """)
        
        # 执行历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS execution_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_params TEXT,
                status TEXT NOT NULL,
                result TEXT,
                error_message TEXT,
                rollback_data TEXT,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                duration_ms INTEGER
            )
        """)
        
        # 待执行任务队列
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_params TEXT NOT NULL,
                priority INTEGER DEFAULT 5,
                status TEXT DEFAULT 'pending',
                scheduled_at TIMESTAMP,
                executed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 回滚操作表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rollback_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id INTEGER NOT NULL,
                operation_type TEXT NOT NULL,
                operation_data TEXT NOT NULL,
                executed INTEGER DEFAULT 0,
                executed_at TIMESTAMP,
                FOREIGN KEY (execution_id) REFERENCES execution_history (id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _register_builtin_tasks(self):
        """注册内置任务"""
        # 文件整理任务
        self.register_task(
            'organize_files',
            '整理文件',
            '将指定目录的文件按类型分类到子文件夹',
            'low',
            self._handler_organize_files
        )
        
        # 文件归档任务
        self.register_task(
            'archive_old_files',
            '归档旧文件',
            '将长期未使用的文件移动到归档目录',
            'medium',
            self._handler_archive_old_files
        )
        
        # 备份文件任务
        self.register_task(
            'backup_file',
            '备份文件',
            '创建重要文件的备份副本',
            'safe',
            self._handler_backup_file
        )
        
        # 清理重复文件
        self.register_task(
            'remove_duplicates',
            '清理重复文件',
            '删除内容完全相同的重复文件',
            'high',
            self._handler_remove_duplicates
        )
        
        # 创建文件夹
        self.register_task(
            'create_folder',
            '创建文件夹',
            '在指定位置创建新文件夹',
            'safe',
            self._handler_create_folder
        )
        
        # 重命名文件
        self.register_task(
            'rename_file',
            '重命名文件',
            '重命名指定文件',
            'low',
            self._handler_rename_file
        )
        
        # 生成报告
        self.register_task(
            'generate_report',
            '生成报告',
            '自动生成工作报告',
            'safe',
            self._handler_generate_report
        )
    
    def register_task(
        self,
        task_type: str,
        task_name: str,
        description: str,
        risk_level: str,
        handler: Callable
    ):
        """注册任务类型"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO task_definitions
            (task_type, task_name, description, risk_level, handler)
            VALUES (?, ?, ?, ?, ?)
        """, (task_type, task_name, description, risk_level, handler.__name__))
        
        conn.commit()
        conn.close()
        
        # 注册处理器
        self.task_handlers[task_type] = handler
    
    def authorize_task(
        self,
        user_id: str,
        task_type: str,
        auto_execute: bool = False,
        max_executions_per_day: int = 10,
        expires_days: int = 30
    ):
        """授权用户执行某类型任务"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO user_authorizations
            (user_id, task_type, authorized, auto_execute, max_executions_per_day, 
             authorized_at, expires_at)
            VALUES (?, ?, 1, ?, ?, CURRENT_TIMESTAMP, ?)
        """, (user_id, task_type, int(auto_execute), max_executions_per_day, expires_at))
        
        conn.commit()
        conn.close()
    
    def revoke_authorization(self, user_id: str, task_type: str):
        """撤销授权"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE user_authorizations
            SET authorized = 0, auto_execute = 0
            WHERE user_id = ? AND task_type = ?
        """, (user_id, task_type))
        
        conn.commit()
        conn.close()
    
    def can_execute(self, user_id: str, task_type: str) -> tuple[bool, str]:
        """检查是否可以执行任务"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 检查授权
        cursor.execute("""
            SELECT * FROM user_authorizations
            WHERE user_id = ? AND task_type = ? AND authorized = 1
        """, (user_id, task_type))
        
        auth = cursor.fetchone()
        if not auth:
            return False, "未授权此类型任务"
        
        # 检查是否过期
        if auth['expires_at']:
            expires_at = datetime.fromisoformat(auth['expires_at'])
            if datetime.now() > expires_at:
                return False, "授权已过期"
        
        # 检查每日执行次数限制
        today = datetime.now().date()
        cursor.execute("""
            SELECT COUNT(*) FROM execution_history
            WHERE user_id = ? AND task_type = ? AND DATE(executed_at) = ?
        """, (user_id, task_type, today))
        
        today_count = cursor.fetchone()[0]
        if today_count >= auth['max_executions_per_day']:
            return False, f"已达每日执行上限 ({auth['max_executions_per_day']}次)"
        
        conn.close()
        return True, "OK"
    
    def execute_task(
        self,
        user_id: str,
        task_type: str,
        params: Dict,
        force: bool = False
    ) -> Dict:
        """
        执行任务
        
        Returns:
            {
                'success': True/False,
                'execution_id': 123,
                'result': {...},
                'error': None
            }
        """
        start_time = time.time()
        
        # 检查任务是否存在
        if task_type not in self.task_handlers:
            return {
                'success': False,
                'error': f'未知的任务类型: {task_type}'
            }
        
        # 检查授权
        if not force:
            can_exec, reason = self.can_execute(user_id, task_type)
            if not can_exec:
                return {
                    'success': False,
                    'error': f'无法执行: {reason}'
                }
        
        # 执行任务
        try:
            handler = self.task_handlers[task_type]
            result = handler(params)
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            # 记录执行历史
            execution_id = self._save_execution_history(
                user_id, task_type, params, 'success', result, None, duration_ms
            )
            
            return {
                'success': True,
                'execution_id': execution_id,
                'result': result,
                'error': None,
                'duration_ms': duration_ms
            }
        
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            
            # 记录执行失败
            execution_id = self._save_execution_history(
                user_id, task_type, params, 'failed', None, str(e), duration_ms
            )
            
            return {
                'success': False,
                'execution_id': execution_id,
                'error': str(e),
                'duration_ms': duration_ms
            }
    
    def _save_execution_history(
        self,
        user_id: str,
        task_type: str,
        params: Dict,
        status: str,
        result: Optional[Dict],
        error: Optional[str],
        duration_ms: int
    ) -> int:
        """保存执行历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO execution_history
            (user_id, task_type, task_params, status, result, error_message, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, task_type,
            json.dumps(params),
            status,
            json.dumps(result) if result else None,
            error,
            duration_ms
        ))
        
        execution_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return execution_id
    
    def queue_task(
        self,
        user_id: str,
        task_type: str,
        params: Dict,
        priority: int = 5,
        scheduled_at: Optional[datetime] = None
    ) -> int:
        """将任务加入队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO task_queue
            (user_id, task_type, task_params, priority, scheduled_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id, task_type,
            json.dumps(params),
            priority,
            scheduled_at.isoformat() if scheduled_at else None
        ))
        
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return task_id
    
    def process_queue(self):
        """处理任务队列"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 获取待执行任务
        cursor.execute("""
            SELECT * FROM task_queue
            WHERE status = 'pending'
                AND (scheduled_at IS NULL OR scheduled_at <= CURRENT_TIMESTAMP)
            ORDER BY priority DESC, created_at ASC
            LIMIT 10
        """)
        
        tasks = cursor.fetchall()
        conn.close()
        
        for task in tasks:
            # 更新状态为执行中
            self._update_task_status(task['id'], 'processing')
            
            # 执行任务
            result = self.execute_task(
                task['user_id'],
                task['task_type'],
                json.loads(task['task_params']),
                force=False
            )
            
            # 更新最终状态
            if result['success']:
                self._update_task_status(task['id'], 'completed')
            else:
                self._update_task_status(task['id'], 'failed')
    
    def _update_task_status(self, task_id: int, status: str):
        """更新任务状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if status in ['completed', 'failed']:
            cursor.execute("""
                UPDATE task_queue
                SET status = ?, executed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, task_id))
        else:
            cursor.execute("""
                UPDATE task_queue
                SET status = ?
                WHERE id = ?
            """, (status, task_id))
        
        conn.commit()
        conn.close()
    
    def start_queue_processor(self, interval: int = 60):
        """启动队列处理器（每分钟检查）"""
        if self.running:
            return
        
        self.running = True
        self.check_thread = threading.Thread(
            target=self._queue_processing_loop,
            args=(interval,),
            daemon=True
        )
        self.check_thread.start()
        logger.info("✅ 自动执行引擎已启动")
    
    def stop_queue_processor(self):
        """停止队列处理器"""
        self.running = False
        if self.check_thread:
            self.check_thread.join(timeout=5)
        logger.info("🛑 自动执行引擎已停止")
    
    def _queue_processing_loop(self, interval: int):
        """队列处理循环"""
        while self.running:
            try:
                self.process_queue()
            except Exception as e:
                logger.info(f"队列处理出错: {e}")
            
            time.sleep(interval)
    
    # ==================== 内置任务处理器 ====================
    
    def _handler_organize_files(self, params: Dict) -> Dict:
        """整理文件处理器"""
        directory = params.get('directory', 'workspace')
        target_dir = os.path.join(self.workspace_root, directory)
        
        if not os.path.exists(target_dir):
            raise ValueError(f"目录不存在: {directory}")
        
        # 按文件扩展名分类
        file_types = {}
        for filename in os.listdir(target_dir):
            file_path = os.path.join(target_dir, filename)
            if os.path.isfile(file_path):
                ext = os.path.splitext(filename)[1].lower() or 'no_extension'
                if ext not in file_types:
                    file_types[ext] = []
                file_types[ext].append(filename)
        
        # 创建分类文件夹并移动文件
        moved_files = []
        for ext, files in file_types.items():
            if len(files) < 3:  # 少于3个文件不单独分类
                continue
            
            category_name = ext[1:] if ext.startswith('.') else ext
            category_dir = os.path.join(target_dir, f"{category_name}_files")
            os.makedirs(category_dir, exist_ok=True)
            
            for filename in files:
                src = os.path.join(target_dir, filename)
                dst = os.path.join(category_dir, filename)
                shutil.move(src, dst)
                moved_files.append({
                    'file': filename,
                    'from': directory,
                    'to': f"{directory}/{category_name}_files"
                })
        
        return {
            'directory': directory,
            'categories_created': len(file_types),
            'files_moved': len(moved_files),
            'details': moved_files
        }
    
    def _handler_archive_old_files(self, params: Dict) -> Dict:
        """归档旧文件处理器"""
        directory = params.get('directory', 'workspace')
        days_threshold = params.get('days', 90)
        
        target_dir = os.path.join(self.workspace_root, directory)
        archive_dir = os.path.join(self.workspace_root, 'archive', datetime.now().strftime('%Y%m%d'))
        os.makedirs(archive_dir, exist_ok=True)
        
        archived_files = []
        threshold_date = datetime.now() - timedelta(days=days_threshold)
        
        for filename in os.listdir(target_dir):
            file_path = os.path.join(target_dir, filename)
            if os.path.isfile(file_path):
                mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                if mtime < threshold_date:
                    dst = os.path.join(archive_dir, filename)
                    shutil.move(file_path, dst)
                    archived_files.append({
                        'file': filename,
                        'last_modified': mtime.isoformat(),
                        'archived_to': archive_dir
                    })
        
        return {
            'directory': directory,
            'days_threshold': days_threshold,
            'files_archived': len(archived_files),
            'archive_location': archive_dir,
            'details': archived_files
        }
    
    def _handler_backup_file(self, params: Dict) -> Dict:
        """备份文件处理器"""
        file_path = params.get('file_path')
        if not file_path:
            raise ValueError("缺少file_path参数")
        
        full_path = os.path.join(self.workspace_root, file_path)
        if not os.path.exists(full_path):
            raise ValueError(f"文件不存在: {file_path}")
        
        # 创建备份目录
        backup_dir = os.path.join(self.workspace_root, 'backups', datetime.now().strftime('%Y%m%d'))
        os.makedirs(backup_dir, exist_ok=True)
        
        # 备份文件
        filename = os.path.basename(file_path)
        timestamp = datetime.now().strftime('%H%M%S')
        backup_name = f"{os.path.splitext(filename)[0]}_{timestamp}{os.path.splitext(filename)[1]}"
        backup_path = os.path.join(backup_dir, backup_name)
        
        shutil.copy2(full_path, backup_path)
        
        return {
            'original_file': file_path,
            'backup_path': os.path.relpath(backup_path, self.workspace_root),
            'backup_size': os.path.getsize(backup_path),
            'timestamp': datetime.now().isoformat()
        }
    
    def _handler_remove_duplicates(self, params: Dict) -> Dict:
        """清理重复文件处理器"""
        # 这是一个高风险操作，需要谨慎实现
        return {
            'message': '重复文件检测功能待实现',
            'risk_level': 'high'
        }
    
    def _handler_create_folder(self, params: Dict) -> Dict:
        """创建文件夹处理器"""
        folder_path = params.get('folder_path')
        if not folder_path:
            raise ValueError("缺少folder_path参数")
        
        full_path = os.path.join(self.workspace_root, folder_path)
        os.makedirs(full_path, exist_ok=True)
        
        return {
            'folder_path': folder_path,
            'full_path': full_path,
            'created': True
        }
    
    def _handler_rename_file(self, params: Dict) -> Dict:
        """重命名文件处理器"""
        old_path = params.get('old_path')
        new_name = params.get('new_name')
        
        if not old_path or not new_name:
            raise ValueError("缺少old_path或new_name参数")
        
        full_old_path = os.path.join(self.workspace_root, old_path)
        if not os.path.exists(full_old_path):
            raise ValueError(f"文件不存在: {old_path}")
        
        directory = os.path.dirname(full_old_path)
        full_new_path = os.path.join(directory, new_name)
        
        os.rename(full_old_path, full_new_path)
        
        return {
            'old_path': old_path,
            'new_path': os.path.relpath(full_new_path, self.workspace_root),
            'renamed': True
        }
    
    def _handler_generate_report(self, params: Dict) -> Dict:
        """生成报告处理器"""
        report_type = params.get('type', 'weekly')
        
        # 这里应该调用insight_reporter
        return {
            'report_type': report_type,
            'generated': True,
            'message': '报告生成功能需要集成insight_reporter模块'
        }
    
    def get_execution_history(
        self, user_id: str, limit: int = 50
    ) -> List[Dict]:
        """获取执行历史"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM execution_history
            WHERE user_id = ?
            ORDER BY executed_at DESC
            LIMIT ?
        """, (user_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_statistics(self, user_id: str, days: int = 30) -> Dict:
        """获取执行统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_date = (datetime.now() - timedelta(days=days)).date()
        
        # 总执行次数
        cursor.execute("""
            SELECT COUNT(*) FROM execution_history
            WHERE user_id = ? AND DATE(executed_at) >= ?
        """, (user_id, start_date))
        total_executions = cursor.fetchone()[0]
        
        # 成功率
        cursor.execute("""
            SELECT COUNT(*) FROM execution_history
            WHERE user_id = ? AND DATE(executed_at) >= ? AND status = 'success'
        """, (user_id, start_date))
        successful = cursor.fetchone()[0]
        
        # 按任务类型统计
        cursor.execute("""
            SELECT task_type, COUNT(*) as count, 
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count
            FROM execution_history
            WHERE user_id = ? AND DATE(executed_at) >= ?
            GROUP BY task_type
        """, (user_id, start_date))
        
        by_type = {}
        for row in cursor.fetchall():
            by_type[row[0]] = {
                'total': row[1],
                'successful': row[2],
                'success_rate': (row[2] / row[1] * 100) if row[1] > 0 else 0
            }
        
        conn.close()
        
        return {
            'period_days': days,
            'total_executions': total_executions,
            'successful_executions': successful,
            'success_rate': (successful / total_executions * 100) if total_executions > 0 else 0,
            'by_task_type': by_type
        }


# 全局实例
_auto_execution_instance = None

def get_auto_execution_engine(
    db_path: str = "config/auto_execution.db",
    workspace_root: str = "workspace",
    notification_manager=None
) -> AutoExecutionEngine:
    """获取自动执行引擎实例（单例）"""
    global _auto_execution_instance
    if _auto_execution_instance is None:
        _auto_execution_instance = AutoExecutionEngine(
            db_path, workspace_root, notification_manager
        )
    return _auto_execution_instance

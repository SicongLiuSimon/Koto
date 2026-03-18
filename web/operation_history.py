#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
操作历史与回滚系统 - 文件操作记录、版本管理、撤销恢复
"""

import json
import logging
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OperationHistory:
    """操作历史管理器"""

    def __init__(self, history_dir: str = "workspace/history"):
        self.history_dir = history_dir
        self.backup_dir = os.path.join(history_dir, "backups")
        self.log_file = os.path.join(history_dir, "operations.json")

        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

        # 加载历史记录
        self.operations = self._load_history()

    def _load_history(self) -> List[Dict[str, Any]]:
        """加载历史记录"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load operation history: %s", e)
                return []
        return []

    def _save_history(self):
        """保存历史记录"""
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self.operations, f, ensure_ascii=False, indent=2)

    def record_operation(
        self, operation_type: str, file_path: str, details: Optional[Dict] = None
    ) -> str:
        """
        记录操作

        Args:
            operation_type: 操作类型 (create/edit/delete/move/copy)
            file_path: 文件路径
            details: 额外详情

        Returns:
            操作ID
        """
        # 生成操作ID
        op_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(self.operations)}"

        # 备份原文件（如果存在）
        backup_path = None
        if os.path.exists(file_path) and operation_type in ["edit", "delete", "move"]:
            backup_path = self._backup_file(file_path, op_id)

        # 记录操作
        operation = {
            "id": op_id,
            "type": operation_type,
            "file_path": file_path,
            "backup_path": backup_path,
            "timestamp": datetime.now().isoformat(),
            "details": details or {},
            "can_rollback": backup_path is not None,
        }

        self.operations.append(operation)
        self._save_history()

        return op_id

    def _backup_file(self, file_path: str, op_id: str) -> str:
        """备份文件"""
        if not os.path.exists(file_path):
            return None

        # 生成备份文件名
        filename = os.path.basename(file_path)
        backup_filename = f"{op_id}_{filename}"
        backup_path = os.path.join(self.backup_dir, backup_filename)

        try:
            shutil.copy2(file_path, backup_path)
            return backup_path
        except Exception as e:
            logger.info(f"备份文件失败: {e}")
            return None

    def rollback(self, op_id: str) -> Dict[str, Any]:
        """
        回滚操作

        Args:
            op_id: 操作ID

        Returns:
            回滚结果
        """
        # 查找操作记录
        operation = None
        for op in self.operations:
            if op["id"] == op_id:
                operation = op
                break

        if not operation:
            return {"success": False, "error": "操作记录不存在"}

        if not operation.get("can_rollback"):
            return {"success": False, "error": "此操作不支持回滚"}

        try:
            op_type = operation["type"]
            file_path = operation["file_path"]
            backup_path = operation["backup_path"]

            if op_type == "create":
                # 删除创建的文件
                if os.path.exists(file_path):
                    os.remove(file_path)
                    action = "已删除创建的文件"
                else:
                    action = "文件已不存在"

            elif op_type in ["edit", "delete"]:
                # 恢复备份文件
                if backup_path and os.path.exists(backup_path):
                    # 确保目标目录存在
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    shutil.copy2(backup_path, file_path)
                    action = "已恢复原文件"
                else:
                    return {"success": False, "error": "备份文件不存在"}

            elif op_type == "move":
                # 移动操作的回滚
                old_path = operation["details"].get("old_path")
                if old_path and backup_path and os.path.exists(backup_path):
                    os.makedirs(os.path.dirname(old_path), exist_ok=True)
                    shutil.copy2(backup_path, old_path)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    action = "已恢复到原位置"
                else:
                    return {"success": False, "error": "无法回滚移动操作"}

            else:
                return {"success": False, "error": f"不支持的操作类型: {op_type}"}

            # 标记为已回滚
            operation["rolled_back"] = True
            operation["rollback_time"] = datetime.now().isoformat()
            self._save_history()

            return {"success": True, "action": action, "operation": operation}

        except Exception as e:
            return {"success": False, "error": f"回滚失败: {str(e)}"}

    def get_history(
        self, limit: int = 50, file_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取历史记录

        Args:
            limit: 返回数量限制
            file_path: 筛选特定文件的操作

        Returns:
            操作记录列表
        """
        operations = self.operations

        if file_path:
            operations = [op for op in operations if op["file_path"] == file_path]

        # 按时间倒序
        operations = sorted(operations, key=lambda x: x["timestamp"], reverse=True)

        return operations[:limit]

    def get_operation(self, op_id: str) -> Optional[Dict[str, Any]]:
        """获取特定操作记录"""
        for op in self.operations:
            if op["id"] == op_id:
                return op
        return None

    def cleanup_old_backups(self, days: int = 30):
        """清理旧备份"""
        from datetime import timedelta

        cutoff_date = datetime.now() - timedelta(days=days)

        removed_count = 0
        for operation in self.operations:
            op_time = datetime.fromisoformat(operation["timestamp"])

            if op_time < cutoff_date:
                backup_path = operation.get("backup_path")
                if backup_path and os.path.exists(backup_path):
                    try:
                        os.remove(backup_path)
                        operation["backup_path"] = None
                        operation["can_rollback"] = False
                        removed_count += 1
                    except Exception as e:
                        logger.info(f"删除备份失败: {e}")

        if removed_count > 0:
            self._save_history()

        return {"success": True, "removed_count": removed_count}

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = len(self.operations)

        by_type = {}
        can_rollback = 0
        rolled_back = 0

        for op in self.operations:
            op_type = op["type"]
            by_type[op_type] = by_type.get(op_type, 0) + 1

            if op.get("can_rollback"):
                can_rollback += 1

            if op.get("rolled_back"):
                rolled_back += 1

        return {
            "total_operations": total,
            "by_type": by_type,
            "can_rollback": can_rollback,
            "rolled_back": rolled_back,
            "backup_size": self._get_backup_size(),
        }

    def _get_backup_size(self) -> int:
        """获取备份文件总大小"""
        total_size = 0
        for root, dirs, files in os.walk(self.backup_dir):
            for file in files:
                file_path = os.path.join(root, file)
                total_size += os.path.getsize(file_path)
        return total_size


# 便捷装饰器
def track_operation(operation_type: str):
    """操作跟踪装饰器"""

    def decorator(func):
        def wrapper(*args, **kwargs):
            # 假设第一个参数是 file_path
            file_path = args[0] if args else kwargs.get("file_path")

            # 获取全局历史记录器
            from web.app import operation_history

            # 执行操作
            result = func(*args, **kwargs)

            # 记录操作
            if file_path:
                operation_history.record_operation(operation_type, file_path)

            return result

        return wrapper

    return decorator


if __name__ == "__main__":
    history = OperationHistory()

    logger.info("=" * 60)
    logger.info("操作历史与回滚系统测试")
    logger.info("=" * 60)

    # 创建测试文件
    test_file = "test_operation.txt"

    # 记录创建操作
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("初始内容")
    op_id1 = history.record_operation("create", test_file)
    logger.info(f"✅ 记录创建操作: {op_id1}")

    # 记录编辑操作
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("修改后的内容")
    op_id2 = history.record_operation("edit", test_file)
    logger.info(f"✅ 记录编辑操作: {op_id2}")

    # 查看当前内容
    with open(test_file, "r", encoding="utf-8") as f:
        logger.info(f"当前内容: {f.read()}")

    # 回滚编辑
    logger.info("\n执行回滚...")
    rollback_result = history.rollback(op_id2)
    if rollback_result["success"]:
        logger.info(f"✅ 回滚成功: {rollback_result['action']}")

        # 查看回滚后的内容
        with open(test_file, "r", encoding="utf-8") as f:
            logger.info(f"回滚后内容: {f.read()}")

    # 查看统计信息
    stats = history.get_statistics()
    logger.info(f"\n统计信息:")
    logger.info(f"- 总操作数: {stats['total_operations']}")
    logger.info(f"- 可回滚: {stats['can_rollback']}")
    logger.info(f"- 已回滚: {stats['rolled_back']}")

    # 清理测试文件
    os.remove(test_file)

    logger.info("\n✅ 操作历史系统就绪")

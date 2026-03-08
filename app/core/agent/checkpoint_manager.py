# -*- coding: utf-8 -*-
"""
Koto Checkpoint Manager
=======================
集中管理 LangGraph 检查点后端的单例选择逻辑。

优先级：
  1. SqliteSaver  (langgraph-checkpoint-sqlite 已安装)  ← 默认，持久化跨重启
  2. MemorySaver  (fallback，仅内存，进程退出丢失)

SqliteSaver 持久化的能力：
  - 多轮对话历史跨重启不丢失（同一 thread_id 恢复上下文）
  - Agent 中途崩溃可从最近检查点续跑
  - 支持查看历史状态快照（time-travel 调试）

用法：
    from app.core.agent.checkpoint_manager import get_checkpointer, CheckpointManager

    # 获取默认检查点（自动选 SQLite 或 Memory）
    saver = get_checkpointer()

    # 查询某 session 的历史
    CheckpointManager.list_checkpoints(thread_id="session-xyz")

    # 删除某 session 的历史（用户清除对话）
    CheckpointManager.delete_thread(thread_id="session-xyz")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# ── 默认路径 ─────────────────────────────────────────────────────────────────
_DEFAULT_DB_PATH = str(
    Path(os.environ.get("KOTO_DB_DIR", Path(__file__).parent.parent.parent.parent / "config"))
    / "koto_checkpoints.sqlite"
)

# ── 单例 ──────────────────────────────────────────────────────────────────────
_checkpointer_instance: Optional[Any] = None
_checkpointer_type: str = "none"


def get_checkpointer(db_path: Optional[str] = None) -> Any:
    """
    返回全局检查点单例。
    首次调用时初始化；后续调用直接返回缓存实例。

    参数:
        db_path: SQLite 文件路径（仅首次调用有效，之后忽略）。
                 默认: config/koto_checkpoints.sqlite
    """
    global _checkpointer_instance, _checkpointer_type

    if _checkpointer_instance is not None:
        return _checkpointer_instance

    path = db_path or _DEFAULT_DB_PATH

    # 1. 尝试 SqliteSaver（推荐）
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        # 确保目录存在
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        conn = _get_sqlite_conn(path)
        saver = SqliteSaver(conn)
        _checkpointer_instance = saver
        _checkpointer_type = "sqlite"
        logger.info(f"[CheckpointManager] ✅ SqliteSaver 启用 → {path}")
        return saver
    except ImportError:
        logger.warning(
            "[CheckpointManager] langgraph-checkpoint-sqlite 未安装，"
            "将使用 MemorySaver（重启后历史丢失）\n"
            "  安装命令: pip install langgraph-checkpoint-sqlite"
        )
    except Exception as exc:
        logger.warning(f"[CheckpointManager] SqliteSaver 初始化失败: {exc}，回退到 MemorySaver")

    # 2. 回退 MemorySaver
    from langgraph.checkpoint.memory import MemorySaver
    saver = MemorySaver()
    _checkpointer_instance = saver
    _checkpointer_type = "memory"
    logger.info("[CheckpointManager] ℹ️ MemorySaver 已启用（仅内存，不持久化）")
    return saver


def get_checkpointer_type() -> str:
    """返回当前使用的检查点类型：'sqlite' | 'memory' | 'none'"""
    return _checkpointer_type


def reset_checkpointer():
    """强制重置单例（测试用）。"""
    global _checkpointer_instance, _checkpointer_type
    _checkpointer_instance = None
    _checkpointer_type = "none"


def _get_sqlite_conn(db_path: str):
    """创建带 WAL 模式的 SQLite 连接（多线程安全）。"""
    import sqlite3
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# CheckpointManager: 高层管理 API
# ─────────────────────────────────────────────────────────────────────────────

class CheckpointManager:
    """
    对外暴露检查点的管理操作：列表 / 删除 / 续跑。

    典型场景：
        - 用户清除对话 → delete_thread()
        - 调试：查看历史节点状态 → list_checkpoints()
        - 续跑：Agent 中途崩溃 → 使用相同 thread_id 重新执行 graph.invoke()
    """

    @staticmethod
    def list_checkpoints(thread_id: str) -> List[dict]:
        """
        列出某 session 的所有检查点快照（从新到旧）。

        返回: [{"checkpoint_id": ..., "created_at": ..., "step": ...}, ...]
        """
        cp = get_checkpointer()
        try:
            config = {"configurable": {"thread_id": thread_id}}
            history = list(cp.list(config))  # StateSnapshot list
            results = []
            for snap in history:
                meta = snap.metadata or {}
                results.append({
                    "checkpoint_id": snap.config.get("configurable", {}).get("checkpoint_id", ""),
                    "step": meta.get("step", 0),
                    "source": meta.get("source", ""),
                    "writes": list(meta.get("writes", {}).keys()) if meta.get("writes") else [],
                })
            return results
        except Exception as exc:
            logger.warning(f"[CheckpointManager] list_checkpoints 失败: {exc}")
            return []

    @staticmethod
    def delete_thread(thread_id: str) -> bool:
        """
        删除某 session 的全部检查点（用于用户清除对话）。
        仅 SqliteSaver 支持；MemorySaver 无状态忽略。
        """
        if _checkpointer_type != "sqlite":
            return True  # MemorySaver 不需要删除

        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            cp = get_checkpointer()
            # SqliteSaver v1.x 支持通过 conn 直接删除
            if hasattr(cp, "conn"):
                # 删除各表（表可能因版本差异不同，逐一尝试）
                for table in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
                    try:
                        cp.conn.execute(
                            f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,)
                        )
                    except Exception:
                        pass
                cp.conn.commit()
                logger.info(f"[CheckpointManager] 删除 thread_id={thread_id}")
                return True
        except Exception as exc:
            logger.warning(f"[CheckpointManager] delete_thread 失败: {exc}")
        return False

    @staticmethod
    def get_db_info() -> dict:
        """返回检查点数据库的基本信息（用于 /api/dev/checkpoint-info）。"""
        info = {
            "type": _checkpointer_type,
            "db_path": _DEFAULT_DB_PATH if _checkpointer_type == "sqlite" else None,
            "thread_count": 0,
            "total_checkpoints": 0,
        }
        if _checkpointer_type == "sqlite":
            try:
                cp = get_checkpointer()
                if hasattr(cp, "conn"):
                    # 检查 checkpoints 表是否存在
                    tables = {r[0] for r in cp.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()}
                    if "checkpoints" in tables:
                        row = cp.conn.execute(
                            "SELECT COUNT(DISTINCT thread_id), COUNT(*) FROM checkpoints"
                        ).fetchone()
                        if row:
                            info["thread_count"] = row[0]
                            info["total_checkpoints"] = row[1]
            except Exception as exc:
                logger.debug(f"[CheckpointManager] get_db_info 异常: {exc}")
        return info

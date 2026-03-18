"""
实时通知管理器 - WebSocket推送系统

功能：
1. WebSocket连接管理
2. 实时推送建议到客户端
3. 通知优先级管理
4. 通知历史记录
5. 用户订阅偏好管理
"""

import asyncio
import json
import logging
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class NotificationManager:
    """实时通知管理器"""

    # 通知类型
    NOTIFICATION_TYPES = {
        "suggestion": "智能建议",
        "insight": "洞察报告",
        "reminder": "提醒",
        "achievement": "成就",
        "greeting": "问候",
        "alert": "警告",
        "tip": "小贴士",
    }

    # 通知优先级
    PRIORITY_LEVELS = {
        "critical": {"level": 4, "color": "red", "sound": True, "popup": True},
        "high": {"level": 3, "color": "orange", "sound": True, "popup": False},
        "medium": {"level": 2, "color": "blue", "sound": False, "popup": False},
        "low": {"level": 1, "color": "gray", "sound": False, "popup": False},
    }

    def __init__(self, db_path: str = "config/notifications.db"):
        """初始化通知管理器"""
        self.db_path = db_path
        self.connections: Dict[str, Set] = defaultdict(
            set
        )  # user_id -> set of websocket connections
        self.user_preferences: Dict[str, Dict] = {}
        self._init_database()
        self._load_preferences()

    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 通知记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                type TEXT NOT NULL,
                priority TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP,
                dismissed_at TIMESTAMP,
                action_taken TEXT,
                metadata TEXT
            )
        """)

        # 用户偏好表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                enabled_types TEXT,
                quiet_hours_start TEXT,
                quiet_hours_end TEXT,
                max_daily_notifications INTEGER DEFAULT 20,
                sound_enabled INTEGER DEFAULT 1,
                popup_enabled INTEGER DEFAULT 1,
                priority_threshold TEXT DEFAULT 'low',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 通知统计表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                date DATE NOT NULL,
                type TEXT NOT NULL,
                sent_count INTEGER DEFAULT 0,
                read_count INTEGER DEFAULT 0,
                dismissed_count INTEGER DEFAULT 0,
                action_count INTEGER DEFAULT 0,
                UNIQUE(user_id, date, type)
            )
        """)

        conn.commit()
        conn.close()

    def _load_preferences(self):
        """加载用户偏好"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM user_preferences")
        rows = cursor.fetchall()

        for row in rows:
            user_id = row[0]
            self.user_preferences[user_id] = {
                "enabled_types": (
                    set(row[1].split(","))
                    if row[1]
                    else set(self.NOTIFICATION_TYPES.keys())
                ),
                "quiet_hours_start": row[2],
                "quiet_hours_end": row[3],
                "max_daily_notifications": row[4],
                "sound_enabled": bool(row[5]),
                "popup_enabled": bool(row[6]),
                "priority_threshold": row[7],
            }

        conn.close()

    def register_connection(self, user_id: str, websocket):
        """注册WebSocket连接"""
        self.connections[user_id].add(websocket)

        # 发送欢迎通知
        self.send_notification(
            user_id=user_id,
            notification_type="greeting",
            priority="low",
            title="欢迎回来！",
            message="Koto已准备好为你服务",
            auto_save=False,
        )

    def unregister_connection(self, user_id: str, websocket):
        """注销WebSocket连接"""
        if user_id in self.connections:
            self.connections[user_id].discard(websocket)
            if not self.connections[user_id]:
                del self.connections[user_id]

    def send_notification(
        self,
        user_id: str,
        notification_type: str,
        priority: str,
        title: str,
        message: str = "",
        data: Optional[Dict] = None,
        auto_save: bool = True,
        force_send: bool = False,
    ) -> Optional[int]:
        """
        发送通知

        Args:
            user_id: 用户ID
            notification_type: 通知类型
            priority: 优先级
            title: 标题
            message: 消息内容
            data: 附加数据
            auto_save: 是否自动保存到数据库
            force_send: 是否强制发送（忽略用户偏好）

        Returns:
            通知ID（如果保存）
        """
        # 检查用户偏好
        if not force_send and not self._should_send(
            user_id, notification_type, priority
        ):
            return None

        # 准备通知数据
        notification = {
            "type": notification_type,
            "priority": priority,
            "title": title,
            "message": message,
            "data": data or {},
            "timestamp": datetime.now().isoformat(),
            "config": self.PRIORITY_LEVELS.get(priority, self.PRIORITY_LEVELS["low"]),
        }

        # 保存到数据库
        notification_id = None
        if auto_save:
            notification_id = self._save_notification(user_id, notification)
            notification["id"] = notification_id

        # 发送到WebSocket客户端
        self._broadcast_to_user(user_id, notification)

        # 更新统计
        self._update_stats(user_id, notification_type, "sent")

        return notification_id

    def _should_send(self, user_id: str, notification_type: str, priority: str) -> bool:
        """检查是否应该发送通知"""
        # 获取用户偏好
        prefs = self.user_preferences.get(user_id)
        if not prefs:
            return True  # 默认发送

        # 检查类型是否已启用
        if notification_type not in prefs["enabled_types"]:
            return False

        # 检查优先级阈值
        user_threshold = self.PRIORITY_LEVELS[prefs["priority_threshold"]]["level"]
        notification_level = self.PRIORITY_LEVELS[priority]["level"]
        if notification_level < user_threshold:
            return False

        # 检查静音时段
        if self._is_quiet_hour(prefs):
            # 只有critical级别才能突破静音
            return priority == "critical"

        # 检查每日限额
        today_count = self._get_today_notification_count(user_id)
        if today_count >= prefs["max_daily_notifications"]:
            return priority in ["critical", "high"]

        return True

    def _is_quiet_hour(self, prefs: Dict) -> bool:
        """检查是否在静音时段"""
        if not prefs.get("quiet_hours_start") or not prefs.get("quiet_hours_end"):
            return False

        now = datetime.now().time()
        start = datetime.strptime(prefs["quiet_hours_start"], "%H:%M").time()
        end = datetime.strptime(prefs["quiet_hours_end"], "%H:%M").time()

        if start <= end:
            return start <= now <= end
        else:  # 跨越午夜
            return now >= start or now <= end

    def _get_today_notification_count(self, user_id: str) -> int:
        """获取今日通知数量"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        today = datetime.now().date()
        cursor.execute(
            """
            SELECT COUNT(*) FROM notifications
            WHERE user_id = ? AND DATE(created_at) = ?
        """,
            (user_id, today),
        )

        count = cursor.fetchone()[0]
        conn.close()

        return count

    def _save_notification(self, user_id: str, notification: Dict) -> int:
        """保存通知到数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO notifications (
                user_id, type, priority, title, message, data, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                user_id,
                notification["type"],
                notification["priority"],
                notification["title"],
                notification["message"],
                json.dumps(notification.get("data", {})),
                json.dumps(notification.get("config", {})),
            ),
        )

        notification_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return notification_id

    def _broadcast_to_user(self, user_id: str, notification: Dict):
        """广播通知到用户的所有连接"""
        if user_id not in self.connections:
            return

        message = json.dumps({"event": "notification", "payload": notification})

        # 同步发送（如果使用Flask-SocketIO）
        for ws in self.connections[user_id]:
            try:
                ws.send(message)
            except Exception as e:
                logger.info(f"发送通知失败: {e}")

    def _update_stats(self, user_id: str, notification_type: str, action: str):
        """更新通知统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        today = datetime.now().date()
        column = f"{action}_count"

        cursor.execute(
            f"""
            INSERT INTO notification_stats (user_id, date, type, {column})
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, date, type) DO UPDATE SET
                {column} = {column} + 1
        """,
            (user_id, today, notification_type),
        )

        conn.commit()
        conn.close()

    def mark_as_read(self, notification_id: int, user_id: str):
        """标记通知为已读"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE notifications
            SET read_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ? AND read_at IS NULL
        """,
            (notification_id, user_id),
        )

        notification_type = None
        if cursor.rowcount > 0:
            # 获取通知类型
            cursor.execute(
                "SELECT type FROM notifications WHERE id = ?", (notification_id,)
            )
            row = cursor.fetchone()
            if row:
                notification_type = row[0]

        conn.commit()
        conn.close()

        # 在连接关闭后更新统计
        if notification_type:
            self._update_stats(user_id, notification_type, "read")

    def dismiss_notification(self, notification_id: int, user_id: str):
        """忽略通知"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE notifications
            SET dismissed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ? AND dismissed_at IS NULL
        """,
            (notification_id, user_id),
        )

        notification_type = None
        if cursor.rowcount > 0:
            cursor.execute(
                "SELECT type FROM notifications WHERE id = ?", (notification_id,)
            )
            row = cursor.fetchone()
            if row:
                notification_type = row[0]

        conn.commit()
        conn.close()

        # 在连接关闭后更新统计
        if notification_type:
            self._update_stats(user_id, notification_type, "dismissed")

    def record_action(self, notification_id: int, user_id: str, action: str):
        """记录用户对通知采取的行动"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE notifications
            SET action_taken = ?
            WHERE id = ? AND user_id = ?
        """,
            (action, notification_id, user_id),
        )

        notification_type = None
        if cursor.rowcount > 0:
            cursor.execute(
                "SELECT type FROM notifications WHERE id = ?", (notification_id,)
            )
            row = cursor.fetchone()
            if row:
                notification_type = row[0]

        conn.commit()
        conn.close()

        # 在连接关闭后更新统计
        if notification_type:
            self._update_stats(user_id, notification_type, "action")

    def get_unread_notifications(self, user_id: str, limit: int = 50) -> List[Dict]:
        """获取未读通知"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM notifications
            WHERE user_id = ? AND read_at IS NULL AND dismissed_at IS NULL
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (user_id, limit),
        )

        rows = cursor.fetchall()
        conn.close()

        notifications = []
        for row in rows:
            notifications.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "priority": row["priority"],
                    "title": row["title"],
                    "message": row["message"],
                    "data": json.loads(row["data"]) if row["data"] else {},
                    "created_at": row["created_at"],
                }
            )

        return notifications

    def get_notification_stats(self, user_id: str, days: int = 7) -> Dict:
        """获取通知统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        start_date = (datetime.now() - timedelta(days=days)).date()

        # 按类型统计
        cursor.execute(
            """
            SELECT 
                type,
                SUM(sent_count) as sent,
                SUM(read_count) as read,
                SUM(dismissed_count) as dismissed,
                SUM(action_count) as acted
            FROM notification_stats
            WHERE user_id = ? AND date >= ?
            GROUP BY type
        """,
            (user_id, start_date),
        )

        type_stats = {}
        for row in cursor.fetchall():
            type_stats[row[0]] = {
                "sent": row[1],
                "read": row[2],
                "dismissed": row[3],
                "acted": row[4],
                "engagement_rate": (row[4] / row[1] * 100) if row[1] > 0 else 0,
            }

        # 总体统计
        cursor.execute(
            """
            SELECT 
                SUM(sent_count) as total_sent,
                SUM(read_count) as total_read,
                SUM(action_count) as total_acted
            FROM notification_stats
            WHERE user_id = ? AND date >= ?
        """,
            (user_id, start_date),
        )

        row = cursor.fetchone()

        conn.close()

        return {
            "period_days": days,
            "total_sent": row[0] or 0,
            "total_read": row[1] or 0,
            "total_acted": row[2] or 0,
            "read_rate": (row[1] / row[0] * 100) if row[0] and row[0] > 0 else 0,
            "action_rate": (row[2] / row[0] * 100) if row[0] and row[0] > 0 else 0,
            "by_type": type_stats,
        }

    def update_user_preferences(self, user_id: str, preferences: Dict):
        """更新用户偏好"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        enabled_types = ",".join(
            preferences.get("enabled_types", list(self.NOTIFICATION_TYPES.keys()))
        )

        cursor.execute(
            """
            INSERT OR REPLACE INTO user_preferences (
                user_id, enabled_types, quiet_hours_start, quiet_hours_end,
                max_daily_notifications, sound_enabled, popup_enabled, priority_threshold
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                user_id,
                enabled_types,
                preferences.get("quiet_hours_start"),
                preferences.get("quiet_hours_end"),
                preferences.get("max_daily_notifications", 20),
                int(preferences.get("sound_enabled", True)),
                int(preferences.get("popup_enabled", True)),
                preferences.get("priority_threshold", "low"),
            ),
        )

        conn.commit()
        conn.close()

        # 重新加载偏好
        self._load_preferences()

    def get_user_preferences(self, user_id: str) -> Dict:
        """获取用户偏好"""
        if user_id in self.user_preferences:
            prefs = self.user_preferences[user_id].copy()
            prefs["enabled_types"] = list(prefs["enabled_types"])
            return prefs

        # 返回默认偏好
        return {
            "enabled_types": list(self.NOTIFICATION_TYPES.keys()),
            "quiet_hours_start": None,
            "quiet_hours_end": None,
            "max_daily_notifications": 20,
            "sound_enabled": True,
            "popup_enabled": True,
            "priority_threshold": "low",
        }


# 全局实例（单例模式）
_notification_manager_instance = None


def get_notification_manager(
    db_path: str = "config/notifications.db",
) -> NotificationManager:
    """获取通知管理器实例（单例）"""
    global _notification_manager_instance
    if _notification_manager_instance is None:
        _notification_manager_instance = NotificationManager(db_path)
    return _notification_manager_instance

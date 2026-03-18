"""
情境感知系统 - 智能场景识别与适配

功能：
1. 自动识别用户当前工作场景
2. 根据场景调整系统行为
3. 场景历史追踪
4. 场景切换预测
5. 个性化场景配置
"""

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set


class ContextAwarenessSystem:
    """情境感知系统"""

    # 场景定义
    CONTEXT_TYPES = {
        "professional": {
            "name": "专业工作",
            "keywords": [
                "项目",
                "代码",
                "会议",
                "报告",
                "文档",
                "设计",
                "开发",
                "测试",
            ],
            "file_patterns": [r"\.py$", r"\.js$", r"\.md$", r"\.docx?$", r"\.pptx?$"],
            "time_hints": [(9, 18)],  # 工作时间
            "behavior": {
                "suggestion_frequency": "medium",
                "notification_priority_threshold": "medium",
                "focus_areas": ["productivity", "organization", "collaboration"],
            },
        },
        "learning": {
            "name": "学习研究",
            "keywords": [
                "教程",
                "笔记",
                "学习",
                "课程",
                "书籍",
                "论文",
                "研究",
                "知识",
            ],
            "file_patterns": [r"\.pdf$", r"笔记", r"学习", r"教程"],
            "time_hints": [],
            "behavior": {
                "suggestion_frequency": "low",
                "notification_priority_threshold": "low",
                "focus_areas": [
                    "knowledge_management",
                    "concept_extraction",
                    "related_content",
                ],
                "enable_features": ["knowledge_assistant", "concept_explanation"],
            },
        },
        "creative": {
            "name": "创作写作",
            "keywords": ["写作", "创作", "文章", "博客", "小说", "剧本", "诗歌"],
            "file_patterns": [r"\.txt$", r"\.md$", r"\.doc"],
            "time_hints": [],
            "behavior": {
                "suggestion_frequency": "very_low",
                "notification_priority_threshold": "high",
                "focus_areas": ["inspiration", "distraction_free"],
                "enable_features": ["inspiration_feed", "writing_stats"],
            },
        },
        "organization": {
            "name": "整理归档",
            "keywords": ["整理", "归档", "分类", "清理", "备份"],
            "file_patterns": [],
            "time_hints": [],
            "behavior": {
                "suggestion_frequency": "high",
                "notification_priority_threshold": "low",
                "focus_areas": ["file_organization", "cleanup", "optimization"],
                "enable_features": ["auto_organize", "duplicate_detection"],
            },
        },
        "casual": {
            "name": "休闲浏览",
            "keywords": ["浏览", "查看", "阅读"],
            "file_patterns": [],
            "time_hints": [(0, 9), (18, 24)],  # 非工作时间
            "behavior": {
                "suggestion_frequency": "very_low",
                "notification_priority_threshold": "high",
                "focus_areas": ["interesting_content", "tips"],
                "enable_features": ["discovery_mode"],
            },
        },
    }

    def __init__(
        self, db_path: str = "config/context_awareness.db", behavior_monitor=None
    ):
        """初始化情境感知系统"""
        self.db_path = db_path
        self.behavior_monitor = behavior_monitor
        self.current_context = None
        self.context_confidence = 0.0

        self._init_database()

    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 场景历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS context_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                context_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                indicators TEXT,
                duration_minutes INTEGER,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP
            )
        """)

        # 场景特征表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS context_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_type TEXT NOT NULL,
                feature_type TEXT NOT NULL,
                feature_value TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                user_id TEXT DEFAULT 'default'
            )
        """)

        # 场景转换表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS context_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                from_context TEXT NOT NULL,
                to_context TEXT NOT NULL,
                transition_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                trigger TEXT
            )
        """)

        # 用户场景偏好表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_context_preferences (
                user_id TEXT NOT NULL,
                context_type TEXT NOT NULL,
                preference_key TEXT NOT NULL,
                preference_value TEXT NOT NULL,
                PRIMARY KEY (user_id, context_type, preference_key)
            )
        """)

        conn.commit()
        conn.close()

    def detect_context(self, user_id: str = "default") -> Dict:
        """
        检测当前工作场景

        Returns:
            {
                'context_type': 'professional',
                'confidence': 0.85,
                'indicators': {...},
                'behavior_config': {...}
            }
        """
        indicators = self._collect_indicators(user_id)
        scores = self._calculate_context_scores(indicators)

        # 选择得分最高的场景
        if scores:
            best_context = max(scores, key=scores.get)
            confidence = scores[best_context]

            # 更新当前场景
            if confidence >= 0.5:  # 置信度阈值
                self._update_current_context(
                    user_id, best_context, confidence, indicators
                )

                return {
                    "context_type": best_context,
                    "context_name": self.CONTEXT_TYPES[best_context]["name"],
                    "confidence": confidence,
                    "indicators": indicators,
                    "behavior_config": self.CONTEXT_TYPES[best_context]["behavior"],
                    "all_scores": scores,
                }

        # 默认场景
        return {
            "context_type": "casual",
            "context_name": "未明确",
            "confidence": 0.0,
            "indicators": indicators,
            "behavior_config": self.CONTEXT_TYPES["casual"]["behavior"],
            "all_scores": scores,
        }

    def _collect_indicators(self, user_id: str) -> Dict:
        """收集场景指标"""
        indicators = {
            "current_hour": datetime.now().hour,
            "recent_files": [],
            "recent_operations": [],
            "recent_searches": [],
            "work_duration": 0,
        }

        if not self.behavior_monitor:
            return indicators

        # 获取最近1小时的活动
        recent_events = self.behavior_monitor.get_recent_events(limit=100)
        recent_events = [
            e
            for e in recent_events
            if (datetime.now() - datetime.fromisoformat(e["timestamp"])).total_seconds()
            < 3600
        ]

        # 收集文件路径
        indicators["recent_files"] = [
            e.get("file_path", "") for e in recent_events if e.get("file_path")
        ]

        # 收集操作类型
        indicators["recent_operations"] = [
            e.get("event_type", "") for e in recent_events
        ]

        # 收集搜索关键词
        search_events = [
            e for e in recent_events if e.get("event_type") == "file_search"
        ]
        for event in search_events:
            metadata = json.loads(event.get("metadata", "{}"))
            if metadata.get("search_query"):
                indicators["recent_searches"].append(metadata["search_query"])

        # 计算工作时长
        if recent_events:
            first_event = datetime.fromisoformat(recent_events[-1]["timestamp"])
            last_event = datetime.fromisoformat(recent_events[0]["timestamp"])
            indicators["work_duration"] = (
                last_event - first_event
            ).total_seconds() / 60

        return indicators

    def _calculate_context_scores(self, indicators: Dict) -> Dict[str, float]:
        """计算各场景的得分"""
        scores = {}

        for context_type, config in self.CONTEXT_TYPES.items():
            score = 0.0
            max_score = 0.0

            # 1. 关键词匹配 (权重: 0.4)
            max_score += 0.4
            keyword_matches = 0
            all_text = " ".join(
                indicators["recent_files"] + indicators["recent_searches"]
            )
            for keyword in config["keywords"]:
                if keyword in all_text:
                    keyword_matches += 1
            if config["keywords"]:
                score += (keyword_matches / len(config["keywords"])) * 0.4

            # 2. 文件模式匹配 (权重: 0.3)
            max_score += 0.3
            pattern_matches = 0
            for file_path in indicators["recent_files"]:
                for pattern in config["file_patterns"]:
                    if re.search(pattern, file_path, re.IGNORECASE):
                        pattern_matches += 1
                        break
            if indicators["recent_files"] and config["file_patterns"]:
                score += (pattern_matches / len(indicators["recent_files"])) * 0.3

            # 3. 时间段匹配 (权重: 0.2)
            max_score += 0.2
            current_hour = indicators["current_hour"]
            for time_range in config["time_hints"]:
                if time_range[0] <= current_hour < time_range[1]:
                    score += 0.2
                    break

            # 4. 操作模式匹配 (权重: 0.1)
            max_score += 0.1
            operation_counter = Counter(indicators["recent_operations"])

            # 不同场景的操作特征
            if context_type == "professional":
                # 专业工作：编辑、创建文件多
                professional_ops = operation_counter.get(
                    "file_edit", 0
                ) + operation_counter.get("file_create", 0)
                score += min(professional_ops / 10, 1.0) * 0.1

            elif context_type == "learning":
                # 学习：打开、搜索多
                learning_ops = operation_counter.get(
                    "file_open", 0
                ) + operation_counter.get("file_search", 0)
                score += min(learning_ops / 10, 1.0) * 0.1

            elif context_type == "creative":
                # 创作：长时间编辑单个文件
                if (
                    indicators["work_duration"] > 30
                    and operation_counter.get("file_edit", 0) > 5
                ):
                    score += 0.1

            elif context_type == "organization":
                # 整理：移动、删除、重命名多
                org_ops = operation_counter.get(
                    "file_organize", 0
                ) + operation_counter.get("file_delete", 0)
                score += min(org_ops / 5, 1.0) * 0.1

            # 归一化得分
            scores[context_type] = score / max_score if max_score > 0 else 0.0

        return scores

    def _update_current_context(
        self, user_id: str, context_type: str, confidence: float, indicators: Dict
    ):
        """更新当前场景"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 如果场景发生变化，结束旧场景
        if self.current_context and self.current_context != context_type:
            cursor.execute(
                """
                UPDATE context_history
                SET ended_at = CURRENT_TIMESTAMP,
                    duration_minutes = (
                        (JULIANDAY(CURRENT_TIMESTAMP) - JULIANDAY(started_at)) * 24 * 60
                    )
                WHERE user_id = ? AND ended_at IS NULL
            """,
                (user_id,),
            )

            # 记录场景转换
            cursor.execute(
                """
                INSERT INTO context_transitions (user_id, from_context, to_context, trigger)
                VALUES (?, ?, ?, ?)
            """,
                (user_id, self.current_context, context_type, json.dumps(indicators)),
            )

        # 创建新场景记录
        if self.current_context != context_type:
            cursor.execute(
                """
                INSERT INTO context_history (user_id, context_type, confidence, indicators)
                VALUES (?, ?, ?, ?)
            """,
                (user_id, context_type, confidence, json.dumps(indicators)),
            )

        conn.commit()
        conn.close()

        self.current_context = context_type
        self.context_confidence = confidence

    def get_current_context(self) -> Optional[Dict]:
        """获取当前场景"""
        if not self.current_context:
            return None

        return {
            "context_type": self.current_context,
            "context_name": self.CONTEXT_TYPES[self.current_context]["name"],
            "confidence": self.context_confidence,
            "behavior_config": self.CONTEXT_TYPES[self.current_context]["behavior"],
        }

    def get_behavior_config(self, context_type: Optional[str] = None) -> Dict:
        """获取场景行为配置"""
        if context_type is None:
            context_type = self.current_context or "casual"

        return self.CONTEXT_TYPES[context_type]["behavior"]

    def get_context_history(self, user_id: str, days: int = 7) -> List[Dict]:
        """获取场景历史"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        start_date = (datetime.now() - timedelta(days=days)).date()

        cursor.execute(
            """
            SELECT * FROM context_history
            WHERE user_id = ? AND DATE(started_at) >= ?
            ORDER BY started_at DESC
        """,
            (user_id, start_date),
        )

        rows = cursor.fetchall()
        conn.close()

        history = []
        for row in rows:
            history.append(
                {
                    "id": row["id"],
                    "context_type": row["context_type"],
                    "context_name": self.CONTEXT_TYPES[row["context_type"]]["name"],
                    "confidence": row["confidence"],
                    "indicators": (
                        json.loads(row["indicators"]) if row["indicators"] else {}
                    ),
                    "duration_minutes": row["duration_minutes"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                }
            )

        return history

    def get_context_statistics(self, user_id: str, days: int = 30) -> Dict:
        """获取场景统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        start_date = (datetime.now() - timedelta(days=days)).date()

        # 按场景类型统计时长
        cursor.execute(
            """
            SELECT 
                context_type,
                COUNT(*) as session_count,
                SUM(duration_minutes) as total_minutes,
                AVG(duration_minutes) as avg_minutes,
                AVG(confidence) as avg_confidence
            FROM context_history
            WHERE user_id = ? AND DATE(started_at) >= ? AND duration_minutes IS NOT NULL
            GROUP BY context_type
            ORDER BY total_minutes DESC
        """,
            (user_id, start_date),
        )

        type_stats = {}
        total_minutes = 0

        for row in cursor.fetchall():
            context_type = row[0]
            minutes = row[2] or 0
            total_minutes += minutes

            type_stats[context_type] = {
                "context_name": self.CONTEXT_TYPES[context_type]["name"],
                "session_count": row[1],
                "total_minutes": minutes,
                "avg_minutes": row[3],
                "avg_confidence": row[4],
                "percentage": 0,  # 稍后计算
            }

        # 计算百分比
        if total_minutes > 0:
            for stats in type_stats.values():
                stats["percentage"] = (stats["total_minutes"] / total_minutes) * 100

        # 最近的场景转换
        cursor.execute(
            """
            SELECT from_context, to_context, COUNT(*) as count
            FROM context_transitions
            WHERE user_id = ? AND DATE(transition_time) >= ?
            GROUP BY from_context, to_context
            ORDER BY count DESC
            LIMIT 10
        """,
            (user_id, start_date),
        )

        transitions = []
        for row in cursor.fetchall():
            transitions.append(
                {
                    "from": self.CONTEXT_TYPES[row[0]]["name"],
                    "to": self.CONTEXT_TYPES[row[1]]["name"],
                    "count": row[2],
                }
            )

        conn.close()

        return {
            "period_days": days,
            "total_minutes": total_minutes,
            "total_hours": round(total_minutes / 60, 1),
            "by_type": type_stats,
            "common_transitions": transitions,
            "dominant_context": (
                max(type_stats, key=lambda k: type_stats[k]["total_minutes"])
                if type_stats
                else None
            ),
        }

    def predict_next_context(self, user_id: str) -> Optional[Dict]:
        """预测下一个可能的场景"""
        if not self.current_context:
            return None

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 查询从当前场景最常转换到的场景
        cursor.execute(
            """
            SELECT to_context, COUNT(*) as count
            FROM context_transitions
            WHERE user_id = ? AND from_context = ?
            GROUP BY to_context
            ORDER BY count DESC
            LIMIT 1
        """,
            (user_id, self.current_context),
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            next_context = row[0]
            return {
                "context_type": next_context,
                "context_name": self.CONTEXT_TYPES[next_context]["name"],
                "probability": 0.6,  # 简化的概率
                "based_on": f'从{self.CONTEXT_TYPES[self.current_context]["name"]}转换',
            }

        return None

    def set_user_preference(
        self,
        user_id: str,
        context_type: str,
        preference_key: str,
        preference_value: str,
    ):
        """设置用户场景偏好"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO user_context_preferences
            (user_id, context_type, preference_key, preference_value)
            VALUES (?, ?, ?, ?)
        """,
            (user_id, context_type, preference_key, preference_value),
        )

        conn.commit()
        conn.close()

    def get_user_preferences(self, user_id: str, context_type: str) -> Dict:
        """获取用户场景偏好"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT preference_key, preference_value
            FROM user_context_preferences
            WHERE user_id = ? AND context_type = ?
        """,
            (user_id, context_type),
        )

        preferences = {}
        for row in cursor.fetchall():
            preferences[row[0]] = row[1]

        conn.close()

        return preferences


# 全局实例
_context_awareness_instance = None


def get_context_awareness_system(
    db_path: str = "config/context_awareness.db", behavior_monitor=None
) -> ContextAwarenessSystem:
    """获取情境感知系统实例（单例）"""
    global _context_awareness_instance
    if _context_awareness_instance is None:
        _context_awareness_instance = ContextAwarenessSystem(db_path, behavior_monitor)
    return _context_awareness_instance

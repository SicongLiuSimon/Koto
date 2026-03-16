"""
主动交互触发系统 - 智能决策何时需要主动交互

核心设计思路：
1. 多维度监控（行为、效率、场景、时间等）
2. 智能评分算法（紧急度 + 重要度 - 打扰成本）
3. 触发器组合（定期 + 事件 + 阈值 + 模式）
4. 自适应学习（根据用户反馈调整触发阈值）
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import threading
import time
import logging


logger = logging.getLogger(__name__)

class TriggerType(Enum):
    """触发器类型"""
    PERIODIC = "periodic"           # 定期触发
    EVENT = "event"                 # 事件触发
    THRESHOLD = "threshold"         # 阈值触发
    PATTERN = "pattern"             # 模式触发
    EMERGENCY = "emergency"         # 紧急触发


class InteractionType(Enum):
    """交互类型"""
    NOTIFICATION = "notification"   # 通知
    DIALOGUE = "dialogue"           # 对话
    ACTION = "action"               # 行动建议
    QUESTION = "question"           # 询问
    ALERT = "alert"                 # 警告


@dataclass
class TriggerCondition:
    """触发条件"""
    trigger_id: str
    trigger_type: TriggerType
    condition_func: callable
    priority: int               # 1-10，数字越大优先级越高
    cooldown_minutes: int       # 冷却时间（分钟）
    enabled: bool = True
    description: str = ""
    threshold_value: Optional[float] = None


@dataclass
class InteractionDecision:
    """交互决策"""
    should_interact: bool
    interaction_type: InteractionType
    priority: str               # critical/high/medium/low
    content: Dict
    reason: str
    urgency_score: float        # 紧急度评分 0-1
    importance_score: float     # 重要度评分 0-1
    disturbance_cost: float     # 打扰成本 0-1
    final_score: float          # 最终决策分数


class ProactiveTriggerSystem:
    """主动交互触发系统"""
    
    def __init__(
        self,
        db_path: str = "config/proactive_triggers.db",
        behavior_monitor=None,
        context_awareness=None,
        suggestion_engine=None,
        notification_manager=None,
        dialogue_engine=None
    ):
        """初始化触发系统"""
        self.db_path = db_path
        self.behavior_monitor = behavior_monitor
        self.context_awareness = context_awareness
        self.suggestion_engine = suggestion_engine
        self.notification_manager = notification_manager
        self.dialogue_engine = dialogue_engine
        
        # 触发条件注册表
        self.triggers: Dict[str, TriggerCondition] = {}
        
        # 触发器参数配置（支持动态设置阈值）
        self.trigger_params: Dict[str, Dict] = {}
        
        # 最后触发时间记录
        self.last_trigger_times: Dict[str, datetime] = {}
        
        # 用户反馈历史（用于自适应学习）
        self.feedback_history: List[Dict] = []
        
        # 运行状态
        self.running = False
        self.check_thread = None
        
        self._init_database()
        self._register_builtin_triggers()
        self._load_trigger_configs()
        self._load_trigger_params()
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 触发历史
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trigger_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decision_made INTEGER,
                interaction_type TEXT,
                urgency_score REAL,
                importance_score REAL,
                disturbance_cost REAL,
                final_score REAL,
                reason TEXT,
                user_feedback TEXT,
                feedback_at TIMESTAMP
            )
        """)
        
        # 触发规则配置
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trigger_config (
                trigger_id TEXT PRIMARY KEY,
                trigger_type TEXT NOT NULL,
                priority INTEGER DEFAULT 5,
                cooldown_minutes INTEGER DEFAULT 60,
                enabled INTEGER DEFAULT 1,
                threshold_value REAL,
                last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 触发器参数配置表（新增）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trigger_parameters (
                trigger_id TEXT PRIMARY KEY,
                parameters TEXT NOT NULL,
                last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (trigger_id) REFERENCES trigger_config(trigger_id)
            )
        """)
        
        # 兼容旧表结构
        cursor.execute("PRAGMA table_info(trigger_config)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'threshold_value' not in columns:
            cursor.execute("ALTER TABLE trigger_config ADD COLUMN threshold_value REAL")
        
        # 用户反馈统计
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trigger_effectiveness (
                trigger_id TEXT PRIMARY KEY,
                total_triggers INTEGER DEFAULT 0,
                accepted_count INTEGER DEFAULT 0,
                ignored_count INTEGER DEFAULT 0,
                dismissed_count INTEGER DEFAULT 0,
                acceptance_rate REAL DEFAULT 0,
                avg_response_time_seconds INTEGER,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _register_builtin_triggers(self):
        """注册内置触发条件"""
        
        # 1. 定期触发：每2小时检查一次未处理建议
        self.register_trigger(TriggerCondition(
            trigger_id="periodic_check_suggestions",
            trigger_type=TriggerType.PERIODIC,
            condition_func=self._check_pending_suggestions,
            priority=5,
            cooldown_minutes=120,
            description="定期检查未处理的智能建议"
        ))
        self.trigger_params["periodic_check_suggestions"] = {
            "check_interval_hours": 2,
            "min_suggestions": 1
        }
        
        # 2. 事件触发：检测到场景切换
        self.register_trigger(TriggerCondition(
            trigger_id="event_context_switch",
            trigger_type=TriggerType.EVENT,
            condition_func=self._check_context_switch,
            priority=6,
            cooldown_minutes=30,
            description="检测到工作场景切换"
        ))
        self.trigger_params["event_context_switch"] = {
            "context_change_timeout_minutes": 30
        }
        
        # 3. 阈值触发：连续工作时间过长
        self.register_trigger(TriggerCondition(
            trigger_id="threshold_work_too_long",
            trigger_type=TriggerType.THRESHOLD,
            condition_func=self._check_work_duration,
            priority=8,
            cooldown_minutes=60,
            description="连续工作时间超过阈值"
        ))
        self.trigger_params["threshold_work_too_long"] = {
            "work_duration_hours": 2,
            "urgency_per_hour": 0.1,
            "max_urgency": 1.0
        }
        
        # 4. 阈值触发：文件编辑次数过多（需要备份）
        self.register_trigger(TriggerCondition(
            trigger_id="threshold_edit_count",
            trigger_type=TriggerType.THRESHOLD,
            condition_func=self._check_edit_frequency,
            priority=7,
            cooldown_minutes=180,
            description="文件编辑次数过多，建议备份"
        ))
        self.trigger_params["threshold_edit_count"] = {
            "edit_count_threshold": 10,
            "check_recent_events": 100
        }
        
        # 5. 模式触发：检测到重复搜索同一内容
        self.register_trigger(TriggerCondition(
            trigger_id="pattern_repeated_search",
            trigger_type=TriggerType.PATTERN,
            condition_func=self._check_search_pattern,
            priority=6,
            cooldown_minutes=90,
            description="检测到重复搜索模式"
        ))
        self.trigger_params["pattern_repeated_search"] = {
            "search_threshold": 3,
            "check_recent_searches": 50
        }
        
        # 6. 模式触发：工作效率突然下降
        self.register_trigger(TriggerCondition(
            trigger_id="pattern_efficiency_drop",
            trigger_type=TriggerType.PATTERN,
            condition_func=self._check_efficiency_pattern,
            priority=7,
            cooldown_minutes=120,
            description="检测到工作效率下降"
        ))
        self.trigger_params["pattern_efficiency_drop"] = {
            "efficiency_threshold": 0.7,
            "comparison_days": 1
        }
        
        # 7. 紧急触发：检测到文件可能丢失
        self.register_trigger(TriggerCondition(
            trigger_id="emergency_file_loss_risk",
            trigger_type=TriggerType.EMERGENCY,
            condition_func=self._check_file_risk,
            priority=10,
            cooldown_minutes=15,
            description="检测到文件丢失风险"
        ))
        self.trigger_params["emergency_file_loss_risk"] = {
            "file_backup_timeout_hours": 24,
            "large_delete_threshold": 10
        }
        
        # 8. 定期触发：早晨问候
        self.register_trigger(TriggerCondition(
            trigger_id="periodic_morning_greeting",
            trigger_type=TriggerType.PERIODIC,
            condition_func=self._check_morning_time,
            priority=3,
            cooldown_minutes=720,  # 12小时
            description="早晨问候"
        ))
        self.trigger_params["periodic_morning_greeting"] = {
            "morning_start_hour": 6,
            "morning_end_hour": 10
        }
        
        # 9. 事件触发：长时间无活动后回归
        self.register_trigger(TriggerCondition(
            trigger_id="event_return_after_break",
            trigger_type=TriggerType.EVENT,
            condition_func=self._check_return_from_break,
            priority=5,
            cooldown_minutes=240,
            description="长时间无活动后回归"
        ))
        self.trigger_params["event_return_after_break"] = {
            "break_timeout_hours": 4
        }
        
        # 10. 阈值触发：杂乱文件数量过多
        self.register_trigger(TriggerCondition(
            trigger_id="threshold_unorganized_files",
            trigger_type=TriggerType.THRESHOLD,
            condition_func=self._check_unorganized_files,
            priority=4,
            cooldown_minutes=360,
            description="杂乱文件数量超过阈值"
        ))
        self.trigger_params["threshold_unorganized_files"] = {
            "organization_suggestion_threshold": 2
        }

        # 11. 事件触发：长期目标有新进展（完成 / 等待确认）
        self.register_trigger(TriggerCondition(
            trigger_id="event_goal_update",
            trigger_type=TriggerType.EVENT,
            condition_func=self._check_goal_updates,
            priority=8,
            cooldown_minutes=15,
            description="长期目标有新进展需用户关注"
        ))
        self.trigger_params["event_goal_update"] = {
            "check_completed_goals": True,
            "check_waiting_goals": True,
            "waiting_timeout_minutes": 60
        }
    
    def register_trigger(self, trigger: TriggerCondition):
        """注册触发条件"""
        self.triggers[trigger.trigger_id] = trigger
        
        # 保存到数据库
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO trigger_config
            (trigger_id, trigger_type, priority, cooldown_minutes, enabled, threshold_value)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            trigger.trigger_id,
            trigger.trigger_type.value,
            trigger.priority,
            trigger.cooldown_minutes,
            int(trigger.enabled),
            trigger.threshold_value
        ))
        
        # 如果有参数，也保存参数到数据库
        if trigger.trigger_id in self.trigger_params:
            cursor.execute("""
                INSERT OR REPLACE INTO trigger_parameters
                (trigger_id, parameters, last_modified)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (
                trigger.trigger_id,
                json.dumps(self.trigger_params[trigger.trigger_id])
            ))
        
        conn.commit()
        conn.close()

    def _load_trigger_configs(self):
        """从数据库加载触发器配置并应用"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM trigger_config")
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            trigger_id = row['trigger_id']
            if trigger_id in self.triggers:
                trigger = self.triggers[trigger_id]
                trigger.priority = row['priority']
                trigger.cooldown_minutes = row['cooldown_minutes']
                trigger.enabled = bool(row['enabled'])
                trigger.threshold_value = row['threshold_value']
    
    def _load_trigger_params(self):
        """从数据库加载触发器参数配置"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT trigger_id, parameters FROM trigger_parameters")
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            try:
                params = json.loads(row['parameters'])
                self.trigger_params[row['trigger_id']] = params
            except json.JSONDecodeError:
                continue
    
    def _save_trigger_params(self, trigger_id: str, params: Dict):
        """保存触发器参数到数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO trigger_parameters
            (trigger_id, parameters, last_modified)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (trigger_id, json.dumps(params)))
        
        conn.commit()
        conn.close()
    
    def get_trigger_params(self, trigger_id: str) -> Dict:
        """获取触发器参数"""
        return self.trigger_params.get(trigger_id, {})
    
    def update_trigger_params(self, trigger_id: str, params: Dict) -> bool:
        """更新触发器参数"""
        if trigger_id not in self.triggers:
            return False
        
        # 合并参数（保留未修改的参数）
        current_params = self.trigger_params.get(trigger_id, {})
        updated_params = {**current_params, **params}
        
        # 更新内存
        self.trigger_params[trigger_id] = updated_params
        
        # 保存到数据库
        self._save_trigger_params(trigger_id, updated_params)
        
        return True

    def list_triggers(self) -> List[Dict]:
        """列出所有触发器及配置"""
        triggers = []
        for trigger_id, trigger in self.triggers.items():
            triggers.append({
                'trigger_id': trigger_id,
                'trigger_type': trigger.trigger_type.value,
                'priority': trigger.priority,
                'cooldown_minutes': trigger.cooldown_minutes,
                'enabled': trigger.enabled,
                'threshold_value': trigger.threshold_value,
                'description': trigger.description,
                'parameters': self.trigger_params.get(trigger_id, {})
            })
        return sorted(triggers, key=lambda t: (-t['priority'], t['trigger_id']))

    def update_trigger_config(
        self,
        trigger_id: str,
        enabled: Optional[bool] = None,
        priority: Optional[int] = None,
        cooldown_minutes: Optional[int] = None,
        threshold_value: Optional[float] = None
    ) -> bool:
        """更新触发器配置"""
        if trigger_id not in self.triggers:
            return False
        
        trigger = self.triggers[trigger_id]
        if enabled is not None:
            trigger.enabled = bool(enabled)
        if priority is not None:
            trigger.priority = int(priority)
        if cooldown_minutes is not None:
            trigger.cooldown_minutes = int(cooldown_minutes)
        if threshold_value is not None:
            trigger.threshold_value = float(threshold_value)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trigger_config
            SET priority = ?, cooldown_minutes = ?, enabled = ?, threshold_value = ?,
                last_modified = CURRENT_TIMESTAMP
            WHERE trigger_id = ?
        """, (
            trigger.priority,
            trigger.cooldown_minutes,
            int(trigger.enabled),
            trigger.threshold_value,
            trigger_id
        ))
        conn.commit()
        conn.close()
        
        return True
    
    def should_trigger(self, trigger_id: str) -> bool:
        """检查触发器是否应该触发（考虑冷却时间）"""
        if trigger_id not in self.triggers:
            return False
        
        trigger = self.triggers[trigger_id]
        
        if not trigger.enabled:
            return False
        
        # 检查冷却时间
        if trigger_id in self.last_trigger_times:
            last_time = self.last_trigger_times[trigger_id]
            cooldown = timedelta(minutes=trigger.cooldown_minutes)
            if datetime.now() - last_time < cooldown:
                return False
        
        return True
    
    def evaluate_interaction_need(
        self, user_id: str = "default"
    ) -> Optional[InteractionDecision]:
        """
        评估是否需要主动交互
        
        核心算法：
        final_score = (urgency * 0.4 + importance * 0.4) - (disturbance_cost * 0.2)
        
        如果 final_score >= threshold，则触发交互
        """
        best_decision = None
        best_score = -1.0
        
        # 遍历所有触发器
        for trigger_id, trigger in self.triggers.items():
            if not self.should_trigger(trigger_id):
                continue
            
            # 执行触发条件检查
            try:
                result = trigger.condition_func(user_id)
                if result is None:
                    continue
                
                # result 应该返回 (urgency, importance, content)
                urgency, importance, content = result
                
                # 计算打扰成本
                disturbance_cost = self._calculate_disturbance_cost(user_id, trigger)
                
                # 计算最终分数
                final_score = (urgency * 0.4 + importance * 0.4) - (disturbance_cost * 0.2)
                
                # 根据分数确定优先级
                if final_score >= 0.8:
                    priority = "critical"
                elif final_score >= 0.6:
                    priority = "high"
                elif final_score >= 0.4:
                    priority = "medium"
                else:
                    priority = "low"
                
                # 确定交互类型
                interaction_type = self._determine_interaction_type(
                    trigger.trigger_type, urgency, importance
                )
                
                # 创建决策
                decision = InteractionDecision(
                    should_interact=(final_score >= 0.35),  # 阈值：0.35
                    interaction_type=interaction_type,
                    priority=priority,
                    content=content,
                    reason=trigger.description,
                    urgency_score=urgency,
                    importance_score=importance,
                    disturbance_cost=disturbance_cost,
                    final_score=final_score
                )
                
                # 保留最高分的决策
                if decision.should_interact and final_score > best_score:
                    best_score = final_score
                    best_decision = decision
                    best_decision.content['trigger_id'] = trigger_id
                    best_decision.content['trigger_type'] = trigger.trigger_type.value
                
            except Exception as e:
                logger.info(f"触发器 {trigger_id} 执行出错: {e}")
                continue
        
        # 记录决策
        if best_decision:
            self._record_trigger(best_decision)
        
        return best_decision
    
    def _calculate_disturbance_cost(
        self, user_id: str, trigger: TriggerCondition
    ) -> float:
        """
        计算打扰成本 (0-1)
        
        考虑因素：
        1. 当前场景（学习/创作 = 高成本，整理 = 低成本）
        2. 触发频率（最近触发过多次 = 高成本）
        3. 用户反馈历史（经常忽略 = 高成本）
        4. 一天中的时间（深夜/早晨 = 高成本）
        """
        cost = 0.0
        
        # 1. 场景成本
        if self.context_awareness:
            context = self.context_awareness.get_current_context()
            if context:
                context_type = context['context_type']
                if context_type in ['creative', 'learning']:
                    cost += 0.3  # 创作和学习不宜打扰
                elif context_type == 'organization':
                    cost += 0.1  # 整理时可以打扰
                else:
                    cost += 0.2
        
        # 2. 频率成本
        recent_triggers = self._get_recent_trigger_count(user_id, hours=1)
        if recent_triggers > 3:
            cost += 0.2
        elif recent_triggers > 1:
            cost += 0.1
        
        # 3. 历史反馈成本
        effectiveness = self._get_trigger_effectiveness(trigger.trigger_id)
        if effectiveness:
            if effectiveness['acceptance_rate'] < 0.3:
                cost += 0.2  # 接受率低
            elif effectiveness['acceptance_rate'] < 0.5:
                cost += 0.1
        
        # 4. 时间成本
        hour = datetime.now().hour
        if hour < 8 or hour > 22:
            cost += 0.3  # 深夜/早晨
        elif 12 <= hour <= 14:
            cost += 0.1  # 午休时间
        
        return min(cost, 1.0)
    
    def _determine_interaction_type(
        self, trigger_type: TriggerType, urgency: float, importance: float
    ) -> InteractionType:
        """确定交互类型"""
        if trigger_type == TriggerType.EMERGENCY:
            return InteractionType.ALERT
        
        if urgency >= 0.8:
            return InteractionType.ALERT
        elif urgency >= 0.6:
            return InteractionType.DIALOGUE
        elif trigger_type == TriggerType.PATTERN:
            return InteractionType.QUESTION
        elif importance >= 0.7:
            return InteractionType.ACTION
        else:
            return InteractionType.NOTIFICATION
    
    def execute_interaction(
        self, decision: InteractionDecision, user_id: str = "default"
    ):
        """执行交互"""
        trigger_id = decision.content.get('trigger_id')
        
        # 更新触发时间
        if trigger_id:
            self.last_trigger_times[trigger_id] = datetime.now()
        
        # 根据交互类型执行
        if decision.interaction_type == InteractionType.NOTIFICATION:
            if self.notification_manager:
                self.notification_manager.send_notification(
                    user_id=user_id,
                    notification_type='suggestion',
                    priority=decision.priority,
                    title=decision.content.get('title', '智能提醒'),
                    message=decision.content.get('message', ''),
                    data=decision.content
                )
        
        elif decision.interaction_type == InteractionType.DIALOGUE:
            if self.dialogue_engine:
                scene_type = decision.content.get('scene_type', 'greeting')
                self.dialogue_engine.manual_trigger(
                    user_id, scene_type, **decision.content
                )
        
        elif decision.interaction_type == InteractionType.ALERT:
            if self.notification_manager:
                self.notification_manager.send_notification(
                    user_id=user_id,
                    notification_type='alert',
                    priority='critical',
                    title=decision.content.get('title', '⚠️ 重要提醒'),
                    message=decision.content.get('message', ''),
                    data=decision.content,
                    force_send=True
                )
        
        # 其他交互类型类似...
    
    def _record_trigger(self, decision: InteractionDecision):
        """记录触发历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        trigger_id = decision.content.get('trigger_id', 'unknown')
        trigger_type = decision.content.get('trigger_type')
        if not trigger_type and trigger_id in self.triggers:
            trigger_type = self.triggers[trigger_id].trigger_type.value
        if not trigger_type:
            trigger_type = 'unknown'
        
        cursor.execute("""
            INSERT INTO trigger_history
            (trigger_id, trigger_type, decision_made, interaction_type,
             urgency_score, importance_score, disturbance_cost, final_score, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trigger_id,
            trigger_type,
            int(decision.should_interact),
            decision.interaction_type.value,
            decision.urgency_score,
            decision.importance_score,
            decision.disturbance_cost,
            decision.final_score,
            decision.reason
        ))
        
        conn.commit()
        conn.close()
    
    def record_user_feedback(
        self, trigger_id: str, feedback: str, response_time_seconds: int
    ):
        """记录用户反馈（用于自适应学习）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 更新最近触发记录的反馈
        cursor.execute("""
            UPDATE trigger_history
            SET user_feedback = ?, feedback_at = CURRENT_TIMESTAMP
            WHERE trigger_id = ?
            ORDER BY triggered_at DESC
            LIMIT 1
        """, (feedback, trigger_id))
        
        # 更新有效性统计
        cursor.execute("""
            INSERT INTO trigger_effectiveness
            (trigger_id, total_triggers, accepted_count, ignored_count, dismissed_count)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(trigger_id) DO UPDATE SET
                total_triggers = total_triggers + 1,
                accepted_count = accepted_count + ?,
                ignored_count = ignored_count + ?,
                dismissed_count = dismissed_count + ?,
                acceptance_rate = (accepted_count + ?) * 1.0 / (total_triggers + 1),
                avg_response_time_seconds = ?,
                last_updated = CURRENT_TIMESTAMP
        """, (
            trigger_id,
            1 if feedback == 'accepted' else 0,
            1 if feedback == 'ignored' else 0,
            1 if feedback == 'dismissed' else 0,
            1 if feedback == 'accepted' else 0,
            1 if feedback == 'ignored' else 0,
            1 if feedback == 'dismissed' else 0,
            1 if feedback == 'accepted' else 0,
            response_time_seconds
        ))
        
        conn.commit()
        conn.close()
        
        # 自适应调整阈值
        self._adapt_trigger_threshold(trigger_id, feedback)
    
    def _adapt_trigger_threshold(self, trigger_id: str, feedback: str):
        """自适应调整触发阈值"""
        effectiveness = self._get_trigger_effectiveness(trigger_id)
        if not effectiveness:
            return
        
        # 如果接受率低于30%，增加冷却时间
        if effectiveness['acceptance_rate'] < 0.3:
            if trigger_id in self.triggers:
                self.triggers[trigger_id].cooldown_minutes = int(
                    self.triggers[trigger_id].cooldown_minutes * 1.5
                )
        
        # 如果接受率高于70%，可以缩短冷却时间
        elif effectiveness['acceptance_rate'] > 0.7:
            if trigger_id in self.triggers:
                self.triggers[trigger_id].cooldown_minutes = max(
                    15,
                    int(self.triggers[trigger_id].cooldown_minutes * 0.8)
                )
    
    def _get_recent_trigger_count(self, user_id: str, hours: int = 1) -> int:
        """获取最近的触发次数"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        cursor.execute("""
            SELECT COUNT(*) FROM trigger_history
            WHERE triggered_at >= ?
        """, (cutoff_time.isoformat(),))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count
    
    def _get_trigger_effectiveness(self, trigger_id: str) -> Optional[Dict]:
        """获取触发器有效性统计"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM trigger_effectiveness
            WHERE trigger_id = ?
        """, (trigger_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    # ==================== 具体触发条件检查函数 ====================
    
    def _check_pending_suggestions(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查未处理建议"""
        if not self.suggestion_engine:
            return None
        
        suggestions = self.suggestion_engine.get_pending_suggestions()
        
        if len(suggestions) >= 3:
            urgency = 0.6
            importance = 0.5 + (len(suggestions) * 0.05)  # 越多越重要
            
            content = {
                'title': f'你有 {len(suggestions)} 条智能建议待处理',
                'message': f'建议查看这些建议，它们可以帮助提升工作效率。',
                'suggestion_count': len(suggestions),
                'scene_type': 'file_organization',
                'trigger_id': 'periodic_check_suggestions'
            }
            
            return (urgency, min(importance, 1.0), content)
        
        return None
    
    def _check_context_switch(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查场景切换"""
        if not self.context_awareness:
            return None
        
        # 获取参数
        params = self.get_trigger_params("event_context_switch")
        context_change_timeout = params.get("context_change_timeout_minutes", 30)
        
        # 获取最近的场景历史
        history = self.context_awareness.get_context_history(user_id, days=1)
        
        if len(history) >= 2:
            latest = history[0]
            previous = history[1]
            
            # 如果刚切换场景
            time_diff = datetime.now() - datetime.fromisoformat(latest['started_at'])
            timeout_seconds = context_change_timeout * 60
            if time_diff.total_seconds() < timeout_seconds:  # 在设定时间内
                urgency = 0.5
                importance = 0.6
                
                content = {
                    'title': f'场景切换：{previous["context_name"]} → {latest["context_name"]}',
                    'message': f'检测到你从{previous["context_name"]}切换到{latest["context_name"]}，需要调整工作模式吗？',
                    'from_context': previous['context_name'],
                    'to_context': latest['context_name'],
                    'scene_type': 'afternoon_greeting',
                    'trigger_id': 'event_context_switch'
                }
                
                return (urgency, importance, content)
        
        return None
    
    def _check_work_duration(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查工作时长"""
        if not self.behavior_monitor:
            return None
        
        # 获取参数
        params = self.get_trigger_params("threshold_work_too_long")
        work_duration_threshold = params.get("work_duration_hours", 2)
        urgency_per_hour = params.get("urgency_per_hour", 0.1)
        max_urgency = params.get("max_urgency", 1.0)
        
        # 获取今天的事件
        events = self.behavior_monitor.get_recent_events(limit=1000)
        today_events = [
            e for e in events 
            if e['timestamp'].startswith(str(datetime.now().date()))
        ]
        
        if len(today_events) > 0:
            first_event = datetime.fromisoformat(today_events[-1]['timestamp'])
            last_event = datetime.fromisoformat(today_events[0]['timestamp'])
            work_hours = (last_event - first_event).total_seconds() / 3600
            
            if work_hours >= work_duration_threshold:
                urgency = min(0.5 + (work_hours - work_duration_threshold) * urgency_per_hour, max_urgency)
                importance = 0.8
                
                content = {
                    'title': '休息提醒',
                    'message': f'你已经连续工作 {work_hours:.1f} 小时了，建议休息一下。',
                    'hours': work_hours,
                    'scene_type': 'work_too_long',
                    'trigger_id': 'threshold_work_too_long'
                }
                
                return (urgency, importance, content)
        
        return None
    
    def _check_edit_frequency(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查编辑频率"""
        if not self.behavior_monitor:
            return None
        
        # 获取参数
        params = self.get_trigger_params("threshold_edit_count")
        edit_count_threshold = params.get("edit_count_threshold", 10)
        check_recent_events = params.get("check_recent_events", 100)
        
        # 获取文件统计
        stats = self.behavior_monitor.get_statistics()
        
        # 检查编辑次数多的文件
        recent_events = self.behavior_monitor.get_recent_events(limit=check_recent_events)
        edit_events = [e for e in recent_events if e['event_type'] == 'file_edit']
        
        if edit_events:
            from collections import Counter
            file_edits = Counter(e['file_path'] for e in edit_events if e.get('file_path'))
            
            for file_path, count in file_edits.most_common(1):
                if count >= edit_count_threshold:
                    urgency = 0.7
                    importance = 0.8
                    
                    content = {
                        'title': '备份建议',
                        'message': f'{file_path} 已编辑 {count} 次，建议立即备份。',
                        'file_path': file_path,
                        'edit_count': count,
                        'scene_type': 'backup_reminder',
                        'trigger_id': 'threshold_edit_count'
                    }
                    
                    return (urgency, importance, content)
        
        return None
    
    def _check_search_pattern(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查搜索模式"""
        if not self.behavior_monitor:
            return None
        
        # 获取参数
        params = self.get_trigger_params("pattern_repeated_search")
        search_threshold = params.get("search_threshold", 3)
        check_recent_searches = params.get("check_recent_searches", 50)
        
        # 获取最近搜索
        recent_events = self.behavior_monitor.get_recent_events(limit=check_recent_searches)
        search_events = [e for e in recent_events if e['event_type'] == 'file_search']
        
        if len(search_events) >= search_threshold:
            # 简化：检查是否有相似搜索
            urgency = 0.5
            importance = 0.6
            
            content = {
                'title': '搜索优化建议',
                'message': f'检测到你最近搜索了 {len(search_events)} 次，是否需要帮助？',
                'search_count': len(search_events),
                'scene_type': 'tips',
                'trigger_id': 'pattern_repeated_search'
            }
            
            return (urgency, importance, content)
        
        return None
    
    def _check_efficiency_pattern(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查效率模式"""
        # 这里可以实现更复杂的效率分析
        # 比如对比今天和昨天的操作数量、编辑/打开比例等
        return None
    
    def _check_file_risk(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查文件风险"""
        # 检查是否有文件长时间未备份、大量删除操作等
        return None
    
    def _check_morning_time(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查是否早晨"""
        # 获取参数
        params = self.get_trigger_params("periodic_morning_greeting")
        morning_start_hour = params.get("morning_start_hour", 6)
        morning_end_hour = params.get("morning_end_hour", 10)
        
        hour = datetime.now().hour
        
        if morning_start_hour <= hour < morning_end_hour:
            urgency = 0.3
            importance = 0.4
            
            content = {
                'title': '早安问候',
                'message': '早上好！新的一天开始了，今天有什么计划吗？',
                'scene_type': 'morning_greeting',
                'trigger_id': 'periodic_morning_greeting'
            }
            
            return (urgency, importance, content)
        
        return None
    
    def _check_return_from_break(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查是否从休息回归"""
        if not self.behavior_monitor:
            return None
        
        # 获取参数
        params = self.get_trigger_params("event_return_after_break")
        break_timeout_hours = params.get("break_timeout_hours", 4)
        
        recent_events = self.behavior_monitor.get_recent_events(limit=10)
        
        if recent_events:
            last_event = datetime.fromisoformat(recent_events[0]['timestamp'])
            hours_since = (datetime.now() - last_event).total_seconds() / 3600
            
            if hours_since >= break_timeout_hours:
                urgency = 0.5
                importance = 0.5
                
                content = {
                    'title': '欢迎回来',
                    'message': f'距离上次活动已经 {hours_since:.1f} 小时了，欢迎回来！',
                    'hours': hours_since,
                    'scene_type': 'long_break_reminder',
                    'trigger_id': 'event_return_after_break'
                }
                
                return (urgency, importance, content)
        
        return None
    
    def _check_goal_updates(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查长期目标是否有需要用户关注的状态变化（完成 / 等待确认）"""
        try:
            from app.core.goal.goal_manager import get_goal_manager, GoalStatus
            gm = get_goal_manager()
            params = self.get_trigger_params("event_goal_update")
            check_completed = params.get("check_completed_goals", True)
            check_waiting = params.get("check_waiting_goals", True)
            waiting_timeout = params.get("waiting_timeout_minutes", 60)

            messages = []

            if check_waiting:
                waiting_goals = gm.list_goals(status=GoalStatus.WAITING_USER, limit=5)
                for g in waiting_goals:
                    try:
                        from datetime import datetime, timedelta
                        updated = datetime.fromisoformat(g.updated_at[:26])
                        if (datetime.now() - updated).total_seconds() > waiting_timeout * 60:
                            ctx = g.get_context()
                            reason = ctx.get("waiting_reason", "需要你的补充信息")
                            messages.append(f"《{g.title}》{reason}")
                    except Exception:
                        pass

            if check_completed:
                # 找最近 15 分钟内完成的目标（避免重复提醒）
                from datetime import datetime, timedelta
                cutoff = (datetime.now() - timedelta(minutes=15)).isoformat()
                completed_goals = gm.list_goals(status=GoalStatus.COMPLETED, limit=5)
                for g in completed_goals:
                    if g.updated_at and g.updated_at > cutoff:
                        messages.append(f"《{g.title}》已完成")

            if not messages:
                return None

            urgency = 0.75 if any("等待" in m or "需要" in m for m in messages) else 0.55
            content = {
                "title": "你委托的事情有新进展",
                "message": "  \n".join(f"• {m}" for m in messages[:3]),
                "goal_count": len(messages),
                "scene_type": "goal_update",
                "trigger_id": "event_goal_update",
            }
            return (urgency, 0.8, content)
        except Exception:
            return None

    def _check_unorganized_files(self, user_id: str) -> Optional[Tuple[float, float, Dict]]:
        """检查杂乱文件"""
        if not self.suggestion_engine:
            return None
        
        # 获取参数
        params = self.get_trigger_params("threshold_unorganized_files")
        suggestion_threshold = params.get("organization_suggestion_threshold", 2)
        
        suggestions = self.suggestion_engine.generate_suggestions()
        org_suggestions = [s for s in suggestions if s['type'] == 'organize']
        
        if len(org_suggestions) >= suggestion_threshold:
            urgency = 0.4
            importance = 0.6
            
            content = {
                'title': '文件整理建议',
                'message': f'发现 {len(org_suggestions)} 个目录需要整理，要查看建议吗？',
                'suggestion_count': len(org_suggestions),
                'scene_type': 'file_organization',
                'trigger_id': 'threshold_unorganized_files'
            }
            
            return (urgency, importance, content)
        
        return None
    
    # ==================== 监控循环 ====================
    
    def start_monitoring(self, check_interval: int = 300, user_id: str = "default"):
        """启动监控（每5分钟检查一次）"""
        if self.running:
            return
        
        self.running = True
        self.check_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(check_interval, user_id),
            daemon=True
        )
        self.check_thread.start()
        logger.info(f"✅ 主动交互触发系统已启动（检查间隔: {check_interval}秒）")
    
    def stop_monitoring(self):
        """停止监控"""
        self.running = False
        if self.check_thread:
            self.check_thread.join(timeout=5)
        logger.info("🛑 主动交互触发系统已停止")
    
    def _monitoring_loop(self, interval: int, user_id: str):
        """监控循环"""
        while self.running:
            try:
                # 评估是否需要交互
                decision = self.evaluate_interaction_need(user_id)
                
                if decision and decision.should_interact:
                    logger.info(f"\n🔔 触发主动交互:")
                    logger.info(f"  类型: {decision.interaction_type.value}")
                    logger.info(f"  优先级: {decision.priority}")
                    logger.info(f"  原因: {decision.reason}")
                    logger.info(f"  得分: {decision.final_score:.2f}")
                    logger.info(f"  (紧急:{decision.urgency_score:.2f} + 重要:{decision.importance_score:.2f} - 打扰:{decision.disturbance_cost:.2f})")
                    
                    # 执行交互
                    self.execute_interaction(decision, user_id)
                
            except Exception as e:
                logger.info(f"监控循环出错: {e}")
            
            # 等待下一次检查
            time.sleep(interval)
    
    def get_trigger_statistics(self, days: int = 7) -> Dict:
        """获取触发统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_date = (datetime.now() - timedelta(days=days)).date()
        
        # 总触发次数
        cursor.execute("""
            SELECT COUNT(*) FROM trigger_history
            WHERE DATE(triggered_at) >= ?
        """, (start_date,))
        total_triggers = cursor.fetchone()[0]
        
        # 按触发器统计
        cursor.execute("""
            SELECT trigger_id, COUNT(*) as count,
                   AVG(final_score) as avg_score,
                   AVG(urgency_score) as avg_urgency,
                   AVG(importance_score) as avg_importance
            FROM trigger_history
            WHERE DATE(triggered_at) >= ?
            GROUP BY trigger_id
            ORDER BY count DESC
        """, (start_date,))
        
        by_trigger = []
        for row in cursor.fetchall():
            by_trigger.append({
                'trigger_id': row[0],
                'count': row[1],
                'avg_score': row[2],
                'avg_urgency': row[3],
                'avg_importance': row[4]
            })
        
        # 获取有效性数据
        cursor.execute("SELECT * FROM trigger_effectiveness")
        effectiveness = []
        for row in cursor.fetchall():
            effectiveness.append({
                'trigger_id': row[0],
                'acceptance_rate': row[4],
                'total_triggers': row[1]
            })
        
        conn.close()
        
        return {
            'period_days': days,
            'total_triggers': total_triggers,
            'by_trigger': by_trigger,
            'effectiveness': effectiveness
        }


# 全局实例
_trigger_system_instance = None

def get_trigger_system(
    db_path: str = "config/proactive_triggers.db",
    behavior_monitor=None,
    context_awareness=None,
    suggestion_engine=None,
    notification_manager=None,
    dialogue_engine=None
) -> ProactiveTriggerSystem:
    """获取触发系统实例（单例）"""
    global _trigger_system_instance
    if _trigger_system_instance is None:
        _trigger_system_instance = ProactiveTriggerSystem(
            db_path,
            behavior_monitor,
            context_awareness,
            suggestion_engine,
            notification_manager,
            dialogue_engine
        )
    return _trigger_system_instance

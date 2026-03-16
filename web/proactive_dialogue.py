"""
主动对话引擎 - AI主动交互系统

功能：
1. 定期主动问候
2. 工作状态检查和提醒
3. 智能对话发起
4. 成就庆祝
5. 关怀提醒
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import random
import threading
import time
import logging


logger = logging.getLogger(__name__)

class ProactiveDialogueEngine:
    """主动对话引擎"""
    
    # 对话场景模板
    DIALOGUE_TEMPLATES = {
        'morning_greeting': [
            "☀️ 早上好！新的一天开始了，今天有什么计划吗？",
            "🌅 美好的早晨！昨天创建了 {file_count} 个文件，今天继续加油！",
            "🎯 早安！你有 {pending_suggestions} 条智能建议待查看，要现在处理吗？"
        ],
        'afternoon_greeting': [
            "☕ 下午好！工作进展如何？",
            "🌤️ 午后时光，要不要看看本周的工作总结？",
            "💡 下午好！发现了 {new_concepts} 个新概念，知识图谱正在扩展中。"
        ],
        'evening_greeting': [
            "🌙 晚上好！今天辛苦了，已完成 {today_events} 项操作。",
            "✨ 晚间时光，要不要生成今日工作报告？",
            "🎉 晚上好！你今天的生产力评分是 {productivity_score}%，表现不错！"
        ],
        'long_break_reminder': [
            "💤 你已经 {hours} 小时没有活动了，需要帮你整理一下工作吗？",
            "🔔 好久不见！有 {unread_count} 条新通知等你查看。",
            "📚 距离上次使用已经 {days} 天了，要不要看看最近的文件？"
        ],
        'work_too_long': [
            "😴 你已经连续工作 {hours} 小时了，要不要休息一下？",
            "🧘 注意休息！持续工作 {hours} 小时容易疲劳，建议稍作休息。",
            "⏰ 工作 {hours} 小时了，建议站起来活动一下，保护眼睛和身体。"
        ],
        'achievement': [
            "🏆 恭喜！你已完成 {milestone} 篇笔记，继续保持！",
            "🎊 太棒了！本周生产力提升了 {improvement}%！",
            "⭐ 成就解锁：连续 {days} 天使用Koto，坚持就是胜利！"
        ],
        'file_organization': [
            "📁 发现 workspace 目录下有 {unorganized_count} 个文件需要整理，要我帮你吗？",
            "🗂️ 有 {duplicate_count} 个可能重复的文件，要检查一下吗？",
            "📦 系统建议将 {old_files_count} 个长期未用的文件归档。"
        ],
        'related_files': [
            "🔗 正在阅读 {current_file}，发现了 {related_count} 个相关文档，要查看吗？",
            "💡 基于你的工作内容，推荐阅读：{related_files}",
            "📊 {file_name} 和其他 {count} 个文件有很高的关联度。"
        ],
        'backup_reminder': [
            "💾 {file_name} 已编辑 {edit_count} 次，建议立即备份。",
            "🔒 重要提醒：有 {critical_files} 个重要文件未备份。",
            "📤 距离上次备份已经 {days} 天了，建议现在备份。"
        ],
        'weekly_summary': [
            "📊 本周工作总结已生成，共完成 {events} 项操作，生产力 {score}%。",
            "📈 周报已出炉！本周最常用文件：{top_file}，要详细查看吗？",
            "🎯 本周亮点：创建了 {new_concepts} 个知识点，工作效率提升 {improvement}%！"
        ],
        'tips': [
            "💡 小贴士：使用知识图谱可以快速找到相关文档，试试吧！",
            "🎨 提示：拖拽图谱节点可以调整布局，让关系更清晰。",
            "⚡ 快捷技巧：Ctrl+K 快速搜索文件和概念。"
        ]
    }
    
    def __init__(
        self,
        db_path: str = "config/proactive_dialogue.db",
        notification_manager=None,
        behavior_monitor=None,
        suggestion_engine=None
    ):
        """初始化主动对话引擎"""
        self.db_path = db_path
        self.notification_manager = notification_manager
        self.behavior_monitor = behavior_monitor
        self.suggestion_engine = suggestion_engine
        
        self._init_database()
        self.running = False
        self.thread = None
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 对话历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dialogue_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                scene_type TEXT NOT NULL,
                message TEXT NOT NULL,
                context TEXT,
                user_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 对话触发规则表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trigger_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scene_type TEXT NOT NULL,
                trigger_condition TEXT NOT NULL,
                min_interval_hours INTEGER DEFAULT 24,
                enabled INTEGER DEFAULT 1,
                last_triggered TIMESTAMP
            )
        """)
        
        # 用户状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_states (
                user_id TEXT PRIMARY KEY,
                last_activity TIMESTAMP,
                session_start TIMESTAMP,
                session_duration_minutes INTEGER DEFAULT 0,
                total_sessions INTEGER DEFAULT 0,
                continuous_days INTEGER DEFAULT 0,
                last_active_date DATE
            )
        """)
        
        # 插入默认触发规则
        default_rules = [
            ('morning_greeting', 'time_of_day', 12),
            ('afternoon_greeting', 'time_of_day', 12),
            ('evening_greeting', 'time_of_day', 12),
            ('long_break_reminder', 'inactive_hours', 24),
            ('work_too_long', 'continuous_work', 2),
            ('achievement', 'milestone_reached', 72),
            ('file_organization', 'unorganized_files', 24),
            ('weekly_summary', 'weekly_report_ready', 168)
        ]
        
        for rule in default_rules:
            cursor.execute("""
                INSERT OR IGNORE INTO trigger_rules (scene_type, trigger_condition, min_interval_hours)
                VALUES (?, ?, ?)
            """, rule)
        
        conn.commit()
        conn.close()
    
    def start_monitoring(self, check_interval: int = 300):
        """启动主动监控（每5分钟检查一次）"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(
            target=self._monitoring_loop,
            args=(check_interval,),
            daemon=True
        )
        self.thread.start()
        logger.info("✅ 主动对话引擎已启动")
    
    def stop_monitoring(self):
        """停止主动监控"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("🛑 主动对话引擎已停止")
    
    def _monitoring_loop(self, interval: int):
        """监控循环"""
        while self.running:
            try:
                self.check_and_trigger_dialogues()
            except Exception as e:
                logger.info(f"主动对话检查出错: {e}")
            
            # 等待下一次检查
            time.sleep(interval)
    
    def check_and_trigger_dialogues(self, user_id: str = "default"):
        """检查并触发对话"""
        # 更新用户状态
        self._update_user_state(user_id)
        
        # 获取所有启用的规则
        rules = self._get_enabled_rules()
        
        for rule in rules:
            scene_type = rule['scene_type']
            trigger_condition = rule['trigger_condition']
            min_interval = rule['min_interval_hours']
            last_triggered = rule['last_triggered']
            
            # 检查间隔时间
            if last_triggered:
                last_time = datetime.fromisoformat(last_triggered)
                if datetime.now() - last_time < timedelta(hours=min_interval):
                    continue
            
            # 检查触发条件
            should_trigger, context = self._check_trigger_condition(
                user_id, trigger_condition, scene_type
            )
            
            if should_trigger:
                self._trigger_dialogue(user_id, scene_type, context)
                self._update_last_triggered(rule['id'])
    
    def _get_enabled_rules(self) -> List[Dict]:
        """获取所有启用的规则"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM trigger_rules
            WHERE enabled = 1
            ORDER BY min_interval_hours ASC
        """)
        
        rules = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return rules
    
    def _check_trigger_condition(
        self, user_id: str, condition: str, scene_type: str
    ) -> tuple[bool, Dict]:
        """检查触发条件"""
        context = {}
        
        if condition == 'time_of_day':
            hour = datetime.now().hour
            if scene_type == 'morning_greeting' and 6 <= hour < 12:
                if self.behavior_monitor:
                    yesterday = (datetime.now() - timedelta(days=1)).date()
                    events = self.behavior_monitor.get_recent_events(
                        limit=100, start_date=str(yesterday)
                    )
                    context['file_count'] = len(set(e.get('file_path') for e in events if e.get('file_path')))
                if self.suggestion_engine:
                    suggestions = self.suggestion_engine.get_pending_suggestions()
                    context['pending_suggestions'] = len(suggestions)
                return True, context
            
            elif scene_type == 'afternoon_greeting' and 12 <= hour < 18:
                return True, context
            
            elif scene_type == 'evening_greeting' and 18 <= hour < 24:
                if self.behavior_monitor:
                    today_events = self.behavior_monitor.get_recent_events(limit=1000)
                    today_events = [e for e in today_events if e['timestamp'].startswith(str(datetime.now().date()))]
                    context['today_events'] = len(today_events)
                return True, context
        
        elif condition == 'inactive_hours':
            state = self._get_user_state(user_id)
            if state and state.get('last_activity'):
                last_activity = datetime.fromisoformat(state['last_activity'])
                hours_inactive = (datetime.now() - last_activity).total_seconds() / 3600
                if hours_inactive >= 24:
                    context['hours'] = int(hours_inactive)
                    context['days'] = int(hours_inactive / 24)
                    return True, context
        
        elif condition == 'continuous_work':
            state = self._get_user_state(user_id)
            if state and state.get('session_start'):
                session_start = datetime.fromisoformat(state['session_start'])
                hours_working = (datetime.now() - session_start).total_seconds() / 3600
                if hours_working >= 2:
                    context['hours'] = round(hours_working, 1)
                    return True, context
        
        elif condition == 'milestone_reached':
            # 检查成就里程碑
            if self.behavior_monitor:
                stats = self.behavior_monitor.get_statistics()
                total_files = stats.get('total_files_tracked', 0)
                
                milestones = [10, 50, 100, 500, 1000]
                for milestone in milestones:
                    if total_files >= milestone and not self._achievement_sent(user_id, f'files_{milestone}'):
                        context['milestone'] = milestone
                        self._mark_achievement_sent(user_id, f'files_{milestone}')
                        return True, context
        
        elif condition == 'unorganized_files':
            if self.suggestion_engine:
                suggestions = self.suggestion_engine.generate_suggestions()
                org_suggestions = [s for s in suggestions if s['type'] == 'organize']
                if org_suggestions:
                    context['unorganized_count'] = len(org_suggestions)
                    return True, context
        
        elif condition == 'weekly_report_ready':
            # 检查是否周一且本周报告未生成
            if datetime.now().weekday() == 0:  # 周一
                return True, context
        
        return False, context
    
    def _trigger_dialogue(self, user_id: str, scene_type: str, context: Dict):
        """触发对话"""
        # 选择模板
        templates = self.DIALOGUE_TEMPLATES.get(scene_type, [])
        if not templates:
            return
        
        template = random.choice(templates)
        
        # 填充上下文
        try:
            message = template.format(**context)
        except KeyError:
            message = template
        
        # 保存对话历史
        self._save_dialogue(user_id, scene_type, message, context)
        
        # 通过通知管理器发送
        if self.notification_manager:
            # 根据场景类型确定优先级
            priority_map = {
                'morning_greeting': 'low',
                'afternoon_greeting': 'low',
                'evening_greeting': 'low',
                'long_break_reminder': 'medium',
                'work_too_long': 'high',
                'achievement': 'medium',
                'file_organization': 'medium',
                'related_files': 'medium',
                'backup_reminder': 'high',
                'weekly_summary': 'medium',
                'tips': 'low'
            }
            
            self.notification_manager.send_notification(
                user_id=user_id,
                notification_type='greeting',
                priority=priority_map.get(scene_type, 'low'),
                title=self._get_scene_title(scene_type),
                message=message,
                data={'scene_type': scene_type, 'context': context}
            )
    
    def _get_scene_title(self, scene_type: str) -> str:
        """获取场景标题"""
        titles = {
            'morning_greeting': 'Koto 问候',
            'afternoon_greeting': 'Koto 问候',
            'evening_greeting': 'Koto 问候',
            'long_break_reminder': '好久不见',
            'work_too_long': '休息提醒',
            'achievement': '成就解锁',
            'file_organization': '整理建议',
            'related_files': '相关推荐',
            'backup_reminder': '备份提醒',
            'weekly_summary': '周报已生成',
            'tips': '使用技巧'
        }
        return titles.get(scene_type, 'Koto 提醒')
    
    def _update_user_state(self, user_id: str):
        """更新用户状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.now()
        today = now.date()
        
        # 获取当前状态
        cursor.execute("SELECT * FROM user_states WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
        if row:
            last_activity = datetime.fromisoformat(row[1]) if row[1] else None
            last_date = datetime.strptime(row[6], '%Y-%m-%d').date() if row[6] else None
            continuous_days = row[5]
            
            # 检查是否连续天数
            if last_date:
                if today == last_date:
                    pass  # 同一天，不更新
                elif today - last_date == timedelta(days=1):
                    continuous_days += 1
                else:
                    continuous_days = 1
            
            cursor.execute("""
                UPDATE user_states
                SET last_activity = ?,
                    continuous_days = ?,
                    last_active_date = ?
                WHERE user_id = ?
            """, (now.isoformat(), continuous_days, str(today), user_id))
        else:
            cursor.execute("""
                INSERT INTO user_states (
                    user_id, last_activity, session_start, continuous_days, last_active_date
                ) VALUES (?, ?, ?, 1, ?)
            """, (user_id, now.isoformat(), now.isoformat(), str(today)))
        
        conn.commit()
        conn.close()
    
    def _get_user_state(self, user_id: str) -> Optional[Dict]:
        """获取用户状态"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM user_states WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def _save_dialogue(self, user_id: str, scene_type: str, message: str, context: Dict):
        """保存对话历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO dialogue_history (user_id, scene_type, message, context)
            VALUES (?, ?, ?, ?)
        """, (user_id, scene_type, message, json.dumps(context)))
        
        conn.commit()
        conn.close()
    
    def _update_last_triggered(self, rule_id: int):
        """更新规则的最后触发时间"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE trigger_rules
            SET last_triggered = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (rule_id,))
        
        conn.commit()
        conn.close()
    
    def _achievement_sent(self, user_id: str, achievement_id: str) -> bool:
        """检查成就是否已发送"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM dialogue_history
            WHERE user_id = ? AND scene_type = 'achievement'
                AND context LIKE ?
        """, (user_id, f'%{achievement_id}%'))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def _mark_achievement_sent(self, user_id: str, achievement_id: str):
        """标记成就已发送"""
        # 成就会在对话历史中记录，不需要额外标记
        pass
    
    def get_dialogue_history(self, user_id: str, limit: int = 50) -> List[Dict]:
        """获取对话历史"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM dialogue_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def manual_trigger(self, user_id: str, scene_type: str, **kwargs):
        """手动触发对话"""
        context = kwargs
        self._trigger_dialogue(user_id, scene_type, context)


# 全局实例
_proactive_dialogue_instance = None

def get_proactive_dialogue_engine(
    db_path: str = "config/proactive_dialogue.db",
    notification_manager=None,
    behavior_monitor=None,
    suggestion_engine=None
) -> ProactiveDialogueEngine:
    """获取主动对话引擎实例（单例）"""
    global _proactive_dialogue_instance
    if _proactive_dialogue_instance is None:
        _proactive_dialogue_instance = ProactiveDialogueEngine(
            db_path, notification_manager, behavior_monitor, suggestion_engine
        )
    return _proactive_dialogue_instance

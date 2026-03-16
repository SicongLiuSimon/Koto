#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地日程管理器
- 持久化到 workspace/calendar/events.json
- 支持新增/删除/查询
- 创建事件时自动触发本地提醒（使用 reminder_manager + win10toast）
"""
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from web.reminder_manager import get_reminder_manager
import logging


logger = logging.getLogger(__name__)

class CalendarManager:
    def __init__(self):
        import sys
        if getattr(sys, 'frozen', False):
            project_root = os.path.dirname(sys.executable)
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)
        self.project_root = project_root
        self.calendar_dir = os.path.join(project_root, 'workspace', 'calendar')
        os.makedirs(self.calendar_dir, exist_ok=True)
        self.events_file = os.path.join(self.calendar_dir, 'events.json')

        self.events: List[Dict] = []
        self._load()

    def _load(self):
        if os.path.exists(self.events_file):
            try:
                with open(self.events_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.events = data if isinstance(data, list) else []
                logger.info(f"[日程] 已加载 {len(self.events)} 条事件")
            except Exception as e:
                logger.info(f"[日程] 加载失败: {e}")

    def _save(self):
        try:
            with open(self.events_file, 'w', encoding='utf-8') as f:
                json.dump(self.events, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[日程] 保存失败: {e}")

    def list_events(self, limit: int = 100) -> List[Dict]:
        # 返回按开始时间排序的最近事件
        def _key(ev):
            try:
                return datetime.fromisoformat(ev.get('start'))
            except Exception:
                return datetime.max
        return sorted(self.events, key=_key)[:limit]

    def add_event(self, title: str, description: str, start: datetime, end: Optional[datetime] = None, remind_before_minutes: int = 0) -> str:
        event_id = f"event_{start.strftime('%Y%m%d_%H%M%S_%f')}"
        event = {
            'id': event_id,
            'title': title,
            'description': description,
            'start': start.isoformat(),
            'end': end.isoformat() if end else None,
            'created_at': datetime.now().isoformat()
        }
        self.events.append(event)
        self._save()

        # 创建提醒：默认在开始时间提醒，可选提前 remind_before_minutes
        try:
            remind_at = start
            if remind_before_minutes > 0:
                remind_at = start - timedelta(minutes=remind_before_minutes)
            if remind_at > datetime.now():
                mgr = get_reminder_manager()
                mgr.add_reminder(
                    title=f"日程提醒: {title}",
                    message=description or '开始时间到',
                    remind_at=remind_at,
                    icon=os.path.join(self.project_root, 'assets', 'koto_icon.ico')
                )
        except Exception as e:
            logger.warning(f"⚠️ 创建日程提醒失败: {e}")

        return event_id

    def delete_event(self, event_id: str) -> bool:
        before = len(self.events)
        self.events = [ev for ev in self.events if ev.get('id') != event_id]
        if len(self.events) != before:
            self._save()
            return True
        return False


_calendar_manager: Optional[CalendarManager] = None


def get_calendar_manager() -> CalendarManager:
    global _calendar_manager
    if _calendar_manager is None:
        _calendar_manager = CalendarManager()
    return _calendar_manager

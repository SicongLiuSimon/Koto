#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地提醒管理器
- 持久化到 workspace/reminders/reminders.json
- 通过 win10toast 在 Windows 右下角发送系统通知
- 支持一次性提醒，重启后自动恢复未来的提醒
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from web.windows_notifier import show_toast
except ImportError:
    from windows_notifier import show_toast


class ReminderManager:
    def __init__(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        self.reminders_dir = os.path.join(project_root, "workspace", "reminders")
        os.makedirs(self.reminders_dir, exist_ok=True)
        self.reminders_file = os.path.join(self.reminders_dir, "reminders.json")

        self.reminders: Dict[str, Dict] = {}
        self.timers: Dict[str, threading.Timer] = {}
        self._load()
        self._restore_pending()

    def _load(self):
        if os.path.exists(self.reminders_file):
            try:
                with open(self.reminders_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.reminders = data if isinstance(data, dict) else {}
                logger.info(f"[提醒] 已加载 {len(self.reminders)} 条提醒")
            except Exception as e:
                logger.info(f"[提醒] 加载失败: {e}")

    def _save(self):
        try:
            with open(self.reminders_file, "w", encoding="utf-8") as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[提醒] 保存失败: {e}")

    def _schedule_timer(self, reminder_id: str, delay: float):
        if delay <= 0:
            delay = 0.5

        def _fire():
            reminder = self.reminders.get(reminder_id)
            if not reminder:
                return
            title = reminder.get("title", "提醒")
            message = reminder.get("message", "")
            icon = reminder.get("icon")
            show_toast(title, message, duration=6, icon_path=icon)
            reminder["status"] = "sent"
            reminder["sent_at"] = datetime.now().isoformat()
            self._save()

        timer = threading.Timer(delay, _fire)
        timer.daemon = True
        timer.start()
        self.timers[reminder_id] = timer

    def _restore_pending(self):
        now = datetime.now()
        for rid, reminder in self.reminders.items():
            if reminder.get("status") == "sent":
                continue
            ts = reminder.get("time")
            if not ts:
                continue
            try:
                remind_at = datetime.fromisoformat(ts)
            except Exception:
                continue
            delay = (remind_at - now).total_seconds()
            if delay < -60:
                # 过期很久，标记已过期
                reminder["status"] = "expired"
            else:
                reminder["status"] = "scheduled"
                self._schedule_timer(rid, delay)
        self._save()

    def add_reminder(
        self, title: str, message: str, remind_at: datetime, icon: Optional[str] = None
    ) -> str:
        reminder_id = f"reminder_{remind_at.strftime('%Y%m%d_%H%M%S_%f')}"
        self.reminders[reminder_id] = {
            "id": reminder_id,
            "title": title,
            "message": message,
            "time": remind_at.isoformat(),
            "status": "scheduled",
            "icon": icon,
        }
        self._save()
        delay = (remind_at - datetime.now()).total_seconds()
        self._schedule_timer(reminder_id, delay)
        logger.info(f"[提醒] 已创建提醒: {title} at {remind_at}")
        return reminder_id

    def add_reminder_in(
        self,
        title: str,
        message: str,
        seconds_from_now: int,
        icon: Optional[str] = None,
    ) -> str:
        remind_at = datetime.now() + timedelta(seconds=seconds_from_now)
        return self.add_reminder(title, message, remind_at, icon)

    def cancel_reminder(self, reminder_id: str) -> bool:
        if reminder_id in self.timers:
            try:
                self.timers[reminder_id].cancel()
            except Exception:
                pass
            self.timers.pop(reminder_id, None)
        if reminder_id in self.reminders:
            self.reminders[reminder_id]["status"] = "cancelled"
            self._save()
            return True
        return False

    def list_reminders(self) -> List[Dict]:
        return list(self.reminders.values())

    def clear_expired(self):
        expired = [
            rid
            for rid, r in self.reminders.items()
            if r.get("status") in ("sent", "expired")
        ]
        for rid in expired:
            self.reminders.pop(rid, None)
        self._save()
        return len(expired)


_reminder_manager: Optional[ReminderManager] = None


def get_reminder_manager() -> ReminderManager:
    global _reminder_manager
    if _reminder_manager is None:
        _reminder_manager = ReminderManager()
    return _reminder_manager

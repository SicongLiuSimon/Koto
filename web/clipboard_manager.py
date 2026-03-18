#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
剪贴板历史管理器
监控剪贴板变化，保存历史记录
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Dict, List

import pyperclip  # pip install pyperclip

logger = logging.getLogger(__name__)


class ClipboardManager:
    """剪贴板历史管理器"""

    def __init__(self, history_file: str = None, max_items: int = 50):
        if history_file is None:
            import sys

            if getattr(sys, "frozen", False):
                project_root = os.path.dirname(sys.executable)
            else:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(script_dir)
            clipboard_dir = os.path.join(project_root, "workspace", "clipboard")
            os.makedirs(clipboard_dir, exist_ok=True)
            history_file = os.path.join(clipboard_dir, "history.json")

        self.history_file = history_file
        self.max_items = max_items
        self.history: List[Dict] = []
        self.last_content = ""
        self.running = False
        self._thread = None

        self._load_history()

    def _load_history(self):
        """加载历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    self.history = json.load(f)
                logger.info(f"[剪贴板] 已加载 {len(self.history)} 条历史")
            except Exception as e:
                logger.info(f"[剪贴板] 历史加载失败: {e}")
                self.history = []

    def _save_history(self):
        """保存历史记录"""
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[剪贴板] 历史保存失败: {e}")

    def _monitor_clipboard(self):
        """监控剪贴板线程"""
        logger.info("[剪贴板] 监控已启动")

        while self.running:
            try:
                current_content = pyperclip.paste()

                # 检查是否有新内容
                if current_content and current_content != self.last_content:
                    self._add_to_history(current_content)
                    self.last_content = current_content

                time.sleep(0.5)  # 每0.5秒检查一次

            except Exception as e:
                logger.error(f"[剪贴板] 监控错误: {e}")
                time.sleep(1)

    def _add_to_history(self, content: str):
        """添加到历史记录"""
        # 忽略太短或太长的内容
        if len(content) < 2 or len(content) > 10000:
            return

        # 检查是否已存在（避免重复）
        for item in self.history:
            if item["content"] == content:
                # 更新时间戳并移到最前
                item["timestamp"] = datetime.now().isoformat()
                self.history.remove(item)
                self.history.insert(0, item)
                self._save_history()
                return

        # 添加新记录
        item_type = self._classify_content(content)
        entities = self._extract_entities(content)
        item = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "length": len(content),
            "preview": content[:100],
            "type": item_type,
            "entities": entities,
        }

        self.history.insert(0, item)

        # 限制历史数量
        if len(self.history) > self.max_items:
            self.history = self.history[: self.max_items]

        self._save_history()
        logger.info(f"[剪贴板] 新记录: {item['preview'][:50]}...")

    def start_monitoring(self):
        """启动监控"""
        if self.running:
            logger.info("[剪贴板] 监控已在运行")
            return

        self.running = True
        self._thread = threading.Thread(target=self._monitor_clipboard, daemon=True)
        self._thread.start()

    def stop_monitoring(self):
        """停止监控"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("[剪贴板] 监控已停止")

    def get_history(self, limit: int = None) -> List[Dict]:
        """获取历史记录"""
        if limit:
            return self.history[:limit]
        return self.history

    def get_recent(self, limit: int = 10) -> List[Dict]:
        """获取最近记录"""
        return self.get_history(limit)

    def search_history(self, query: str) -> List[Dict]:
        """搜索历史记录"""
        results = []
        query_lower = query.lower()

        for item in self.history:
            if query_lower in item["content"].lower():
                results.append(item)

        return results

    def search(self, query: str) -> List[Dict]:
        """搜索历史记录（兼容旧接口）"""
        return self.search_history(query)

    def copy_from_history(self, index_or_content) -> bool:
        """从历史记录复制到剪贴板（支持索引或内容）"""
        if isinstance(index_or_content, int):
            if 0 <= index_or_content < len(self.history):
                content = self.history[index_or_content]["content"]
                pyperclip.copy(content)
                self.last_content = content  # 防止重复添加
                logger.info(f"[剪贴板] 已复制历史记录 #{index_or_content}")
                return True
            return False

        if isinstance(index_or_content, str):
            for idx, item in enumerate(self.history):
                if item["content"] == index_or_content:
                    pyperclip.copy(item["content"])
                    self.last_content = item["content"]
                    logger.info(f"[剪贴板] 已复制历史记录 #{idx}")
                    return True
            return False

        return False

    def clear_history(self):
        """清空历史记录"""
        self.history = []
        self._save_history()
        logger.info("[剪贴板] 历史已清空")

    def _classify_content(self, content: str) -> str:
        """简单内容分类"""
        if re.search(r"https?://\S+", content):
            return "url"
        if re.search(r"\b[\w\.-]+@[\w\.-]+\.[A-Za-z]{2,}\b", content):
            return "email"
        if re.search(r"\b(\+?\d[\d\-\s]{6,}\d)\b", content):
            return "phone"
        if content.strip().startswith("{") and content.strip().endswith("}"):
            return "json"
        if content.strip().startswith("[") and content.strip().endswith("]"):
            return "list"
        if "\n" in content and any(
            k in content for k in ["def ", "class ", "import ", "{", "}", ";"]
        ):
            return "code"
        return "text"

    def _extract_entities(self, content: str) -> Dict[str, List[str]]:
        """提取常见实体（邮箱/电话/链接）"""
        emails = re.findall(r"\b[\w\.-]+@[\w\.-]+\.[A-Za-z]{2,}\b", content)
        phones = re.findall(r"\b(\+?\d[\d\-\s]{6,}\d)\b", content)
        urls = re.findall(r"https?://\S+", content)

        def _dedupe(items: List[str]) -> List[str]:
            seen = set()
            result = []
            for item in items:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
            return result

        return {
            "emails": _dedupe(emails),
            "phones": _dedupe(phones),
            "urls": _dedupe(urls),
        }


# 全局实例
_clipboard_manager = None


def get_clipboard_manager() -> ClipboardManager:
    """获取全局剪贴板管理器单例"""
    global _clipboard_manager
    if _clipboard_manager is None:
        _clipboard_manager = ClipboardManager()
    return _clipboard_manager

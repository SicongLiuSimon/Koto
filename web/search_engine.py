#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
智能搜索模块
支持全局搜索、文件搜索、聊天记录搜索、笔记搜索
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class SearchEngine:
    """智能搜索引擎"""

    def __init__(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(script_dir)
        self.workspace_root = os.path.join(self.project_root, "workspace")
        self.chats_root = os.path.join(self.project_root, "chats")

    def search_all(self, query: str, max_results: int = 50) -> Dict[str, List]:
        """
        全局搜索

        Args:
            query: 搜索关键词
            max_results: 每个类别的最大结果数

        Returns:
            {
                'files': [...],
                'chats': [...],
                'notes': [...],
                'clipboard': [...]
            }
        """
        results = {
            "files": self.search_files(query, max_results),
            "chats": self.search_chats(query, max_results),
            "notes": self.search_notes(query, max_results),
            "clipboard": self.search_clipboard(query, max_results),
        }

        total = sum(len(v) for v in results.values())
        logger.info(f"[搜索] 全局搜索完成: '{query}' 找到 {total} 个结果")

        return results

    def search_files(self, query: str, max_results: int = 20) -> List[Dict]:
        """
        搜索文件内容

        Args:
            query: 搜索关键词
            max_results: 最大结果数
        """
        results = []
        query_lower = query.lower()

        # 搜索 workspace 目录
        for root, dirs, files in os.walk(self.workspace_root):
            for file in files:
                if len(results) >= max_results:
                    break

                file_path = os.path.join(root, file)

                # 文件名匹配
                if query_lower in file.lower():
                    results.append(
                        {
                            "type": "filename",
                            "path": file_path,
                            "name": file,
                            "match": file,
                        }
                    )
                    continue

                # 文件内容匹配 (只搜索文本文件)
                if file.endswith((".txt", ".md", ".py", ".json", ".csv")):
                    try:
                        with open(
                            file_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = f.read()

                            if query_lower in content.lower():
                                # 找到匹配的行
                                lines = content.split("\n")
                                match_lines = [
                                    (i + 1, line)
                                    for i, line in enumerate(lines)
                                    if query_lower in line.lower()
                                ]

                                if match_lines:
                                    line_num, match_line = match_lines[0]

                                    results.append(
                                        {
                                            "type": "content",
                                            "path": file_path,
                                            "name": file,
                                            "line": line_num,
                                            "match": match_line.strip()[:100],
                                        }
                                    )
                    except (OSError, UnicodeDecodeError) as e:
                        logger.debug(
                            "Failed to read file for search %s: %s", file_path, e
                        )
                        pass

        return results

    def search_chats(self, query: str, max_results: int = 20) -> List[Dict]:
        """
        搜索聊天记录

        Args:
            query: 搜索关键词
            max_results: 最大结果数
        """
        results = []
        query_lower = query.lower()

        if not os.path.exists(self.chats_root):
            return results

        for file in os.listdir(self.chats_root):
            if not file.endswith(".json"):
                continue

            file_path = os.path.join(self.chats_root, file)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    chat_data = json.load(f)

                    messages = chat_data.get("messages", [])

                    for msg in messages:
                        if len(results) >= max_results:
                            break

                        content = msg.get("content", "")
                        role = msg.get("role", "user")
                        timestamp = msg.get("timestamp", "")

                        if query_lower in content.lower():
                            results.append(
                                {
                                    "type": "chat",
                                    "file": file,
                                    "role": role,
                                    "content": (
                                        content[:200] + "..."
                                        if len(content) > 200
                                        else content
                                    ),
                                    "timestamp": timestamp,
                                    "match": self._extract_match_context(
                                        content, query, 100
                                    ),
                                }
                            )
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Failed to read chat file: %s", e)
                pass

        # 按时间倒序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return results

    def search_notes(self, query: str, max_results: int = 20) -> List[Dict]:
        """
        搜索笔记

        Args:
            query: 搜索关键词
            max_results: 最大结果数
        """
        results = []
        query_lower = query.lower()

        notes_dir = os.path.join(self.workspace_root, "notes")

        if not os.path.exists(notes_dir):
            return results

        # 搜索笔记索引
        index_file = os.path.join(notes_dir, "notes_index.json")

        if os.path.exists(index_file):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    notes_index = json.load(f)

                    for note_id, note_data in notes_index.items():
                        if len(results) >= max_results:
                            break

                        title = note_data.get("title", "")
                        content = note_data.get("content", "")
                        tags = note_data.get("tags", [])
                        category = note_data.get("category", "")

                        # 匹配标题、内容、标签、分类
                        if (
                            query_lower in title.lower()
                            or query_lower in content.lower()
                            or query_lower in " ".join(tags).lower()
                            or query_lower in category.lower()
                        ):

                            results.append(
                                {
                                    "type": "note",
                                    "id": note_id,
                                    "title": title,
                                    "category": category,
                                    "tags": tags,
                                    "content": (
                                        content[:200] + "..."
                                        if len(content) > 200
                                        else content
                                    ),
                                    "created_at": note_data.get("created_at", ""),
                                    "match": self._extract_match_context(
                                        content, query, 100
                                    ),
                                }
                            )
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Failed to read note file: %s", e)

        # 按创建时间倒序
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return results

    def search_clipboard(self, query: str, max_results: int = 20) -> List[Dict]:
        """
        搜索剪贴板历史

        Args:
            query: 搜索关键词
            max_results: 最大结果数
        """
        results = []
        query_lower = query.lower()

        clipboard_file = os.path.join(self.workspace_root, "clipboard", "history.json")

        if not os.path.exists(clipboard_file):
            return results

        try:
            with open(clipboard_file, "r", encoding="utf-8") as f:
                clipboard_history = json.load(f)

                for item in clipboard_history:
                    if len(results) >= max_results:
                        break

                    content = item.get("content", "")
                    content_type = item.get("type", "text")
                    timestamp = item.get("timestamp", "")

                    if query_lower in content.lower():
                        results.append(
                            {
                                "type": "clipboard",
                                "content": (
                                    content[:200] + "..."
                                    if len(content) > 200
                                    else content
                                ),
                                "content_type": content_type,
                                "timestamp": timestamp,
                                "match": self._extract_match_context(
                                    content, query, 100
                                ),
                            }
                        )
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to read clipboard history: %s", e)

        # 按时间倒序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return results

    def _extract_match_context(
        self, text: str, query: str, context_length: int = 100
    ) -> str:
        """
        提取匹配上下文

        Args:
            text: 原文本
            query: 关键词
            context_length: 上下文长度
        """
        query_lower = query.lower()
        text_lower = text.lower()

        # 找到第一个匹配位置
        index = text_lower.find(query_lower)

        if index == -1:
            return text[:context_length]

        # 计算上下文范围
        start = max(0, index - context_length // 2)
        end = min(len(text), index + len(query) + context_length // 2)

        context = text[start:end]

        # 添加省略号
        if start > 0:
            context = "..." + context
        if end < len(text):
            context = context + "..."

        return context

    def search_by_date_range(
        self, start_date: str, end_date: str, types: List[str] = None
    ) -> Dict[str, List]:
        """
        按日期范围搜索

        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            types: 搜索类型列表 ['chats', 'notes', 'clipboard']
        """
        if types is None:
            types = ["chats", "notes", "clipboard"]

        results = {}

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")

            if "chats" in types:
                results["chats"] = self._filter_by_date(
                    self.search_chats("", max_results=1000), start, end
                )

            if "notes" in types:
                results["notes"] = self._filter_by_date(
                    self.search_notes("", max_results=1000), start, end
                )

            if "clipboard" in types:
                results["clipboard"] = self._filter_by_date(
                    self.search_clipboard("", max_results=1000), start, end
                )

        except Exception as e:
            logger.info(f"[搜索] 日期范围搜索失败: {e}")

        return results

    def _filter_by_date(
        self, items: List[Dict], start: datetime, end: datetime
    ) -> List[Dict]:
        """按日期过滤结果"""
        filtered = []

        for item in items:
            timestamp_str = item.get("timestamp") or item.get("created_at", "")

            if timestamp_str:
                try:
                    # 尝试解析时间戳
                    if "T" in timestamp_str:
                        item_date = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                    else:
                        item_date = datetime.strptime(timestamp_str, "%Y-%m-%d")

                    if start <= item_date <= end:
                        filtered.append(item)
                except (ValueError, TypeError) as e:
                    logger.debug("Failed to parse timestamp '%s': %s", timestamp_str, e)

        return filtered


# 全局实例
_search_engine = None


def get_search_engine() -> SearchEngine:
    """获取全局搜索引擎单例"""
    global _search_engine
    if _search_engine is None:
        _search_engine = SearchEngine()
    return _search_engine

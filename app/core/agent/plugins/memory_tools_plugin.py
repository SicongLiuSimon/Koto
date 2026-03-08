"""
MemoryToolsPlugin — Exposes memory and context operations to UnifiedAgent.

Tools registered:
  - memory_search(query, k)      → search long-term memories
  - memory_save(content, category) → explicitly save a memory
  - context_recall(topic)        → retrieve session summaries related to a topic

These allow the Agent to actively query/write Koto's memory store during
multi-step reasoning, enabling truly context-aware behaviour.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)


class MemoryToolsPlugin(AgentPlugin):
    """Agent plugin for long-term memory access."""

    @property
    def name(self) -> str:
        return "MemoryTools"

    @property
    def description(self) -> str:
        return "Tools for reading and writing Koto's long-term memory store."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "memory_search",
                "func": self.memory_search,
                "description": (
                    "Search Koto's long-term memory for information related to a query. "
                    "Use this to recall user preferences, past decisions, or important facts "
                    "before answering a question."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "What to search for in long-term memory.",
                        },
                        "k": {
                            "type": "INTEGER",
                            "description": "Number of memories to return (default 5).",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_save",
                "func": self.memory_save,
                "description": (
                    "Save an important piece of information to long-term memory. "
                    "Use this when the user explicitly asks you to remember something, "
                    "or when you discover a key fact that should be retained."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "content": {
                            "type": "STRING",
                            "description": "The information to remember.",
                        },
                        "category": {
                            "type": "STRING",
                            "description": (
                                "Memory category. One of: user_fact, preference, "
                                "topic_summary, decision, reminder."
                            ),
                        },
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "context_recall",
                "func": self.context_recall,
                "description": (
                    "Retrieve past session summaries related to a topic. "
                    "Use this to reconnect with earlier conversations that may have been "
                    "compressed out of the current context window."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "topic": {
                            "type": "STRING",
                            "description": "Topic or theme to search past sessions for.",
                        },
                    },
                    "required": ["topic"],
                },
            },
        ]

    # ── Tool implementations ─────────────────────────────────────────────────

    def memory_search(self, query: str, k: int = 5) -> str:
        """Search long-term memories."""
        try:
            mgr = self._get_memory_manager()
            if mgr is None:
                return "[memory_search] ⚠️ 记忆系统未可用"
            hits = mgr.search_memories(query, limit=k)
            if not hits:
                return "[memory_search] 未找到相关记忆"
            lines = [f"[{h.get('category','?')}] {h.get('content','')}" for h in hits]
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[MemoryTools] memory_search error: {e}")
            return f"[memory_search] 错误: {e}"

    def memory_save(self, content: str, category: str = "user_fact") -> str:
        """Save a memory."""
        valid_categories = {
            "user_fact", "preference", "topic_summary", "decision", "reminder"
        }
        category = category if category in valid_categories else "user_fact"
        try:
            mgr = self._get_memory_manager()
            if mgr is None:
                return "[memory_save] ⚠️ 记忆系统未可用"
            result = mgr.add_memory(content=content, category=category, source="agent")
            if result:
                return f"[memory_save] ✅ 已记住: {content[:80]}"
            return "[memory_save] ⚠️ 内容过短或重复，未保存"
        except Exception as e:
            logger.debug(f"[MemoryTools] memory_save error: {e}")
            return f"[memory_save] 错误: {e}"

    def context_recall(self, topic: str) -> str:
        """Retrieve past session summaries for a topic."""
        try:
            mgr = self._get_memory_manager()
            if mgr is None:
                return "[context_recall] ⚠️ 记忆系统未可用"
            hits = mgr.search_memories(topic, limit=4)
            summaries = [
                h for h in hits
                if h.get("category") == "session_summary"
            ]
            if not summaries:
                # Fall back to any hit
                summaries = hits[:3]
            if not summaries:
                return "[context_recall] 未找到相关历史会话"
            lines = [f"• {h.get('content', '')[:300]}" for h in summaries]
            return "[context_recall] 相关历史记录：\n" + "\n".join(lines)
        except Exception as e:
            logger.debug(f"[MemoryTools] context_recall error: {e}")
            return f"[context_recall] 错误: {e}"

    # ── Internal helper ──────────────────────────────────────────────────────

    @staticmethod
    def _get_memory_manager():
        """Lazily load MemoryManager without circular imports."""
        try:
            import sys
            # Try web.app.get_memory_manager (runtime context)
            if "web.app" in sys.modules:
                fn = getattr(sys.modules["web.app"], "get_memory_manager", None)
                if fn:
                    return fn()
            # Fallback: direct instantiation
            try:
                from web.enhanced_memory_manager import EnhancedMemoryManager
            except ImportError:
                from enhanced_memory_manager import EnhancedMemoryManager
            return EnhancedMemoryManager()
        except Exception as e:
            logger.debug(f"[MemoryTools] _get_memory_manager error: {e}")
            return None

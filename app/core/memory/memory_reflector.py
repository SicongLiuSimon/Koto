"""
MemoryReflector — Deep background reflection after conversation turns.

Unlike the existing MemoryIntegration (which does simple entity extraction),
MemoryReflector runs a richer LLM prompt that extracts:
  - Explicit user facts (name, job, location, preferences)
  - Implicit preferences inferred from conversation style
  - Topic summaries worth remembering
  - Important decisions or conclusions reached
  - Action items or things the user asked Koto to remember

Results are written to EnhancedMemoryManager with category tagging and
a confidence filter  (only memories with score ≥ 0.6 are persisted).

Usage (web/app.py — called from _start_memory_extraction):
    from app.core.memory.memory_reflector import MemoryReflector
    MemoryReflector.reflect_async(user_msg, ai_msg, history,
                                  task_type, session_name, get_memory_fn, llm_fn)
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Run reflection for these task types (not just CHAT)
_REFLECT_TASK_TYPES = {"CHAT", "RESEARCH", "CODER", "FILE_GEN", "AGENT"}

_REFLECT_PROMPT = """\
请分析以下对话，提取值得长期记忆的信息。
只提取真正重要的、能帮助未来对话的信息——不要琐事。

对话：
用户: {user_msg}
Koto: {ai_msg}

请以 JSON 数组格式输出，每条记忆包含：
- "content": 记忆内容（1-2句话，中文）
- "category": 分类，从以下选择: user_fact / preference / topic_summary / decision / reminder
- "confidence": 置信度 0.0-1.0（只有真正确定的才给高分）

只输出 JSON 数组，不要有任何其他文字。示例：
[
  {{"content": "用户是一名前端工程师，偏好 React", "category": "user_fact", "confidence": 0.9}},
  {{"content": "用户对 RAG 技术感兴趣", "category": "preference", "confidence": 0.7}}
]

如果没有值得记忆的信息，输出空数组 []。
"""

_MIN_CONFIDENCE = 0.60
# Minimum content length to be worth saving
_MIN_CONTENT_LEN = 10

_TRIPLE_PROMPT = """\
从以下对话中提取知识三元组（主语-关系-宾语）。
仅提取关于用户、技术工具、系统或具体事件的明确事实，不要提取泛泛陈述。
输出JSON数组，每项格式：{{"subject":"...","relation":"...","object":"...","confidence":0.0-1.0}}
只输出JSON数组，无其他文字。如无可提取三元组则输出[]。

对话：
用户: {user_msg}
Koto: {ai_msg}
"""
_MIN_TRIPLE_CONFIDENCE = 0.50


class MemoryReflector:
    """Deep reflection — extracts structured memories from completed turns."""

    @staticmethod
    def should_reflect(user_msg: str, ai_msg: str, task_type: str) -> bool:
        """Quick heuristic filter — skip trivial or system turns."""
        if task_type not in _REFLECT_TASK_TYPES:
            return False
        combined = (user_msg + ai_msg).strip()
        if len(combined) < 30:
            return False
        # Skip very short AI responses (probably error messages)
        if len(ai_msg.strip()) < 20:
            return False
        return True

    @staticmethod
    def _extract_memories(
        user_msg: str,
        ai_msg: str,
        llm_fn: Callable[[str], str],
    ) -> List[Dict[str, Any]]:
        """Call LLM and parse JSON memory list."""
        prompt = _REFLECT_PROMPT.format(
            user_msg=user_msg[:800],
            ai_msg=ai_msg[:1200],
        )
        try:
            raw = llm_fn(prompt)
        except Exception as e:
            logger.debug(f"[Reflector] LLM call failed: {e}")
            return []

        # Strip markdown fences if present
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            memories = json.loads(raw)
            if not isinstance(memories, list):
                return []
        except json.JSONDecodeError:
            # Try to find JSON array in the response
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    memories = json.loads(match.group())
                except Exception:
                    return []
            else:
                return []

        # Validate and filter
        valid: List[Dict[str, Any]] = []
        for m in memories:
            if not isinstance(m, dict):
                continue
            content    = str(m.get("content", "")).strip()
            category   = str(m.get("category", "user_fact")).strip()
            confidence = float(m.get("confidence", 0.0))
            if (
                len(content) >= _MIN_CONTENT_LEN
                and confidence >= _MIN_CONFIDENCE
            ):
                valid.append({
                    "content":    content,
                    "category":   category,
                    "confidence": confidence,
                })
        return valid

    @staticmethod
    def _extract_triples(
        user_msg: str,
        ai_msg: str,
        llm_fn: Callable[[str], str],
    ) -> List[Dict[str, Any]]:
        """Call LLM to extract knowledge triples from conversation."""
        prompt = _TRIPLE_PROMPT.format(
            user_msg=user_msg[:600],
            ai_msg=ai_msg[:800],
        )
        try:
            raw = llm_fn(prompt)
        except Exception as e:
            logger.debug(f"[Reflector] triple LLM call failed: {e}")
            return []

        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            triples = json.loads(raw)
            if not isinstance(triples, list):
                return []
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    triples = json.loads(match.group())
                except Exception:
                    return []
            else:
                return []

        valid: List[Dict[str, Any]] = []
        for t in triples:
            if not isinstance(t, dict):
                continue
            subj = str(t.get("subject", "")).strip()
            rel  = str(t.get("relation", "")).strip()
            obj  = str(t.get("object", "")).strip()
            conf = float(t.get("confidence", 0.0))
            if subj and rel and obj and conf >= _MIN_TRIPLE_CONFIDENCE:
                valid.append({"subject": subj, "relation": rel, "object": obj, "confidence": conf})
        return valid

    @staticmethod
    def _do_reflect(
        user_msg: str,
        ai_msg: str,
        task_type: str,
        session_name: str,
        get_memory_fn: Callable,
        llm_fn: Callable[[str], str],
    ) -> int:
        """Synchronous reflection — runs inside a daemon thread."""
        memories = MemoryReflector._extract_memories(user_msg, ai_msg, llm_fn)
        if not memories:
            logger.debug(f"[Reflector] No memories extracted for {session_name}")
            return 0

        saved = 0
        try:
            mgr = get_memory_fn()
            if mgr is None:
                return 0
            for mem in memories:
                try:
                    mgr.add_memory(
                        content=mem["content"],
                        category=mem["category"],
                        source="memory_reflector",
                        metadata={
                            "tags": [f"session:{session_name}", f"task:{task_type}", "auto_reflect"],
                            "confidence": mem["confidence"],
                        },
                    )
                    saved += 1
                except Exception as e:
                    logger.debug(f"[Reflector] add_memory failed: {e}")
        except Exception as e:
            logger.debug(f"[Reflector] get_memory_fn failed: {e}")

        if saved:
            logger.info(f"[Reflector] ✅ Saved {saved} memories from {task_type} turn")

        # 3-A: Extract triples for Graph RAG
        try:
            from app.core.services.graph_rag_service import GraphRAGService
            triples = MemoryReflector._extract_triples(user_msg, ai_msg, llm_fn)
            if triples:
                n = GraphRAGService.add_triples_from_llm(
                    triples,
                    source_text=f"{user_msg[:200]} | {ai_msg[:200]}",
                    origin="reflector",
                )
                if n:
                    logger.info(f"[Reflector] ✅ Added {n} triples to KG")
        except Exception as _te:
            logger.debug(f"[Reflector] triple extraction failed: {_te}")

        return saved

    @classmethod
    def reflect_async(
        cls,
        user_msg: str,
        ai_msg: str,
        task_type: str,
        session_name: str,
        get_memory_fn: Callable,
        llm_fn: Callable[[str], str],
    ) -> None:
        """Fire-and-forget — starts reflection in a daemon thread."""
        if not cls.should_reflect(user_msg, ai_msg, task_type):
            return

        def _worker():
            try:
                cls._do_reflect(
                    user_msg, ai_msg, task_type, session_name,
                    get_memory_fn, llm_fn,
                )
            except Exception as e:
                logger.debug(f"[Reflector] background thread error: {e}")

        threading.Thread(target=_worker, daemon=True, name="koto_reflector").start()

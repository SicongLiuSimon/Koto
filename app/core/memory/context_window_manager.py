"""
ContextWindowManager — MemGPT-style context paging for Koto.

When conversation history grows too large, older turns are:
  1. Summarized with a lightweight LLM call
  2. Paged out → saved to MemoryManager (tagged with session_name)
  3. Recent turns are kept in context

On each request the manager also performs a "page-in": RAG retrieval of
relevant session summaries and long-term memories related to the current query.

Usage (web/app.py):
    from app.core.memory.context_window_manager import ContextWindowManager
    cwm_out = ContextWindowManager.manage(history, user_input,
                                          session_name, get_memory_manager)
    history        = cwm_out["history"]
    _cw_injected   = cwm_out["paged_in_context"]  # inject into system_instruction
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Threshold at which we summarize old turns (rough token estimate)
_MAX_HISTORY_TOKENS: int = 20_000
# How many recent turns to ALWAYS keep verbatim (model + user pairs)
_RECENT_KEEP: int = 12
# Minimum turns before we even consider compressing
_MIN_TURNS_BEFORE_COMPRESS: int = 16


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate — Chinese chars ~1.5, Latin ~0.75 per char."""
    return max(1, len(text) // 2)


def _history_tokens(history: list) -> int:
    total = 0
    for turn in history:
        for part in turn.get("parts", []):
            total += _estimate_tokens(str(part))
    return total


def _summarize_turns(turns: list, llm_callable: Optional[Callable]) -> str:
    """Summarize a list of history turns into a compact paragraph.

    Falls back to simple concatenation if llm_callable is None.
    """
    if not turns:
        return ""
    # Build plaintext of the turns
    lines: List[str] = []
    for t in turns:
        role = "用户" if t.get("role") == "user" else "Koto"
        text = " ".join(str(p) for p in t.get("parts", []))
        lines.append(f"{role}: {text[:400]}")
    conversation_text = "\n".join(lines)

    if llm_callable is None:
        # Simple truncation fallback
        return f"[早期对话摘要]\n{conversation_text[:1200]}"

    prompt = (
        "请将以下对话摘要为简洁的几句话，保留关键事实、用户意图和重要结论。"
        "不要包含问候语或客套话。用中文输出。\n\n"
        f"{conversation_text}"
    )
    try:
        summary = llm_callable(prompt)
        return f"[早期对话摘要]\n{summary.strip()}"
    except Exception as e:
        logger.debug(f"[CWM] summarize failed: {e}")
        return f"[早期对话摘要]\n{conversation_text[:1200]}"


class ContextWindowManager:
    """MemGPT-style context window management."""

    @staticmethod
    def manage(
        history: list,
        query: str,
        session_name: str,
        get_memory_fn: Callable,
        llm_callable: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point.  Called once per request after loading history.

        Args:
            history:       Loaded (already-trimmed) history list from SessionManager.
            query:         Current user input (used for page-in retrieval).
            session_name:  Session file stem, used to tag paged-out memories.
            get_memory_fn: Callable → EnhancedMemoryManager instance.
            llm_callable:  Optional fn(prompt: str) → str for summarization.

        Returns:
            {
              "history": List[dict],          # managed history (may be shorter)
              "paged_in_context": str,        # text to inject into system_instruction
            }
        """
        result: Dict[str, Any] = {"history": history, "paged_in_context": ""}

        # ── Step 1: Page-out if history is too long ──────────────────────────
        token_count = _history_tokens(history)
        if (
            token_count > _MAX_HISTORY_TOKENS
            and len(history) > _MIN_TURNS_BEFORE_COMPRESS
        ):
            # Keep the _RECENT_KEEP most-recent turns; summarize the rest
            old_turns   = history[:-_RECENT_KEEP]
            recent_turns = history[-_RECENT_KEEP:]

            summary_text = _summarize_turns(old_turns, llm_callable)

            # Page out to memory in background (non-blocking)
            def _page_out():
                try:
                    mgr = get_memory_fn()
                    if mgr is None:
                        return
                    tag = f"session_summary:{session_name}"
                    mgr.add_memory(
                        content=summary_text,
                        category="session_summary",
                        source="context_window_manager",
                        metadata={"tags": [tag, "auto_generated"]},
                    )
                    logger.info(f"[CWM] Paged out {len(old_turns)} turns for {session_name}")
                except Exception as ex:
                    logger.debug(f"[CWM] page_out failed: {ex}")

            threading.Thread(target=_page_out, daemon=True).start()

            # Return only the recent portion + a synthetic summary turn at the top
            managed_history = [
                {"role": "user",  "parts": ["请注意以下是之前对话的摘要："]},
                {"role": "model", "parts": [summary_text]},
            ] + recent_turns

            result["history"] = managed_history
            logger.info(
                f"[CWM] Compressed history {len(history)}→{len(managed_history)} "
                f"({token_count} → {_history_tokens(managed_history)} est. tokens)"
            )

        # ── Step 2: Page-in — retrieve relevant memories for current query ───
        try:
            mgr = get_memory_fn()
            if mgr is not None and query and len(query.strip()) > 4:
                # 优先使用向量语义检索（FAISS），关键词搜索作为备用
                hits = mgr.search_vector_memories(query, limit=4) or mgr.search_memories(query, limit=4)
                if hits:
                    lines: List[str] = []
                    for h in hits:
                        content = h.get("content", "")
                        if content and len(content) > 10:
                            lines.append(f"• {content[:300]}")
                    if lines:
                        result["paged_in_context"] = (
                            "## 🧠 相关长期记忆（自动调入）\n" + "\n".join(lines)
                        )
        except Exception as ex:
            logger.debug(f"[CWM] page_in failed: {ex}")

        return result

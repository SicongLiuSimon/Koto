import re
import time
import logging
from app.core.routing.local_model_router import LocalModelRouter

logger = logging.getLogger(__name__)

# Optional type hint only
try:
    from typing import Optional
except ImportError:
    pass


class IntentAnalyzer:
    """
    Analyzes multi-turn conversation intent for Koto.
    Rewrites ambiguous/pronoun-heavy user inputs into clear, standalone instructions
    so that the SmartDispatcher can route them correctly.
    """

    # Patterns that trigger intent rewriting
    TRIGGER_PATTERNS = [
        # Repeat-task patterns
        r'重复.*任务', r'再做一遍', r'再来一次', r're(peat|do).*last.*task', r'try.*again',
        r'重新.*做', r'重新.*来', r'再试一次', r'再跑一次',
        # Pronoun / demonstrative references
        r'刚才', r'那个', r'这个', r'上一个', r'上个', r'前面', r'上面',
        r'上述', r'之前', r'先前',
        # Modify / extend
        r'继续', r'修改', r'换成', r'改成', r'调整', r'优化一下',
        r'再写一个', r'再画一个', r'再生成一个', r'再做一个',
        r'详细', r'展开', r'举个例子', r'解释一下', r'说清楚',
        # Document / plan references
        r'这个计划', r'该计划', r'上述计划', r'上面的计划',
        r'这个方案', r'该方案', r'上述方案',
        r'这个大纲', r'该大纲',
        r'这个ppt', r'该ppt', r'这个PPT', r'该PPT',
        r'按照这个', r'根据这个', r'基于这个',
        # Ordinal references like "第3点" / "其中第二步"
        r'第[一二三四五六七八九十\d]+[点条个步]',
        r'[其另]中',
    ]

    REWRITE_PROMPT = (
        "\u4f60\u662f\u4e00\u4e2a\u667a\u80fd\u610f\u56fe\u91cd\u5199\u52a9\u624b\u3002\u4f60\u7684\u4efb\u52a1\u662f\u6839\u636e\u7528\u6237\u7684\u5386\u53f2\u5bf9\u8bdd\u4e0a\u4e0b\u6587\uff0c"
        "\u5c06\u7528\u6237\u5f53\u524d\u6a21\u7cca\u7684\u3001\u5e26\u6709\u6307\u4ee3\u8bcd\u7684\u8f93\u5165\uff0c\u91cd\u5199\u4e3a\u4e00\u4e2a\u6e05\u6670\u3001\u72ec\u7acb\u3001\u5b8c\u6574\u7684\u6307\u4ee4\u3002\n\n"
        "\u89c4\u5219\uff1a\n"
        "1. \u5982\u679c\u7528\u6237\u8981\u6c42[\u91cd\u590d\u4e0a\u4e2a\u4efb\u52a1]\uff0c\u8bf7\u63d0\u53d6\u4e0a\u4e00\u4e2a\u4efb\u52a1\u7684\u6838\u5fc3\u5185\u5bb9\u5e76\u91cd\u5199\u3002\n"
        "2. \u5982\u679c\u7528\u6237\u8981\u6c42[\u4fee\u6539/\u6362\u6210...]\uff0c\u8bf7\u7ed3\u5408\u4e0a\u4e00\u4e2a\u4efb\u52a1\u7684\u5185\u5bb9\uff0c\u751f\u6210\u5305\u542b\u4fee\u6539\u8981\u6c42\u7684\u65b0\u6307\u4ee4\u3002\n"
        "3. \u5982\u679c\u7528\u6237\u8bf4[\u7ee7\u7eed]\u6216[\u5c55\u5f00\u7b2cN\u70b9]\uff0c\u8bf7\u6839\u636e\u4e0a\u8f6e\u56de\u590d\u5185\u5bb9\u63a8\u65ad\u8981\u7ee7\u7eed/\u5c55\u5f00\u7684\u5177\u4f53\u5185\u5bb9\u3002\n"
        "4. \u5982\u679c\u7528\u6237\u7684\u8f93\u5165\u5df2\u7ecf\u5f88\u6e05\u6670\uff0c\u4e0d\u9700\u8981\u4e0a\u4e0b\u6587\u4e5f\u80fd\u7406\u89e3\uff0c\u8bf7\u76f4\u63a5\u8fd4\u56de\u539f\u8f93\u5165\u3002\n"
        "5. \u53ea\u8f93\u51fa\u91cd\u5199\u540e\u7684\u6307\u4ee4\u6587\u672c\uff0c\u4e0d\u8981\u4efb\u4f55\u89e3\u91ca\u3001\u524d\u7f00\u6216\u591a\u4f59\u7684\u8bdd\u3002\n\n"
        "\u5386\u53f2\u5bf9\u8bdd\uff1a\n{history}\n\n"
        "\u7528\u6237\u5f53\u524d\u8f93\u5165\uff1a\n{user_input}\n\n"
        "\u91cd\u5199\u540e\u7684\u72ec\u7acb\u6307\u4ee4\uff1a"
    )

    @classmethod
    def _get_content(cls, msg: dict) -> str:
        """Extract text from a history message, supporting both {parts} and {content} formats."""
        content = msg.get("content") or ""
        if not content:
            parts = msg.get("parts") or []
            content = parts[0] if parts else ""
        return str(content)

    @classmethod
    def should_analyze(cls, user_input: str) -> bool:
        """Returns True if user_input contains patterns that require intent rewriting."""
        return any(re.search(p, user_input, re.IGNORECASE) for p in cls.TRIGGER_PATTERNS)

    @classmethod
    def rewrite_intent(
        cls,
        user_input: str,
        history: list,
        tracker=None,
        memory_context: str = None,
    ) -> str:
        """
        Rewrite an ambiguous user input into a clear, standalone instruction.

        Args:
            user_input:     current user message
            history:        conversation history ({role, parts} or {role, content} format)
            tracker:        optional ConversationTracker for extra context
            memory_context: optional pre-formatted memory string (alternative to tracker)

        Returns:
            Rewritten instruction, or original user_input if no rewrite is possible.
        """
        if not history and tracker is None and not memory_context:
            return user_input

        # Build history summary text — last 10 messages (~5 turns)
        recent_lines = []
        for msg in (history or [])[-10:]:
            role = "用户" if msg.get("role") == "user" else "助手"
            content = cls._get_content(msg)
            if content:
                content = content[:300] + "..." if len(content) > 300 else content
                recent_lines.append(f"{role}: {content}")

        # Append last-response summary from tracker if available
        if tracker is not None:
            try:
                last_summary = tracker.get_last_response_summary()
                if last_summary:
                    recent_lines.append(f"[上轮回复摘要] {last_summary}")
            except Exception:
                pass

        # Append pre-formatted memory context string if provided
        if memory_context and memory_context.strip():
            recent_lines.append(f"[记忆上下文] {memory_context.strip()[:300]}")

        history_text = "\n".join(recent_lines)
        if not history_text:
            return user_input

        prompt = cls.REWRITE_PROMPT.format(history=history_text, user_input=user_input)

        # --- Try local Ollama model first ---
        if LocalModelRouter.is_ollama_available():
            if not LocalModelRouter._initialized:
                LocalModelRouter.init_model()

            if LocalModelRouter._initialized and LocalModelRouter._model_name:
                logger.info(
                    "[IntentAnalyzer] Using local model (%s) for intent rewrite",
                    LocalModelRouter._model_name,
                )
                start_time = time.time()
                result, err = LocalModelRouter.call_ollama_chat(
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个意图重写助手。只输出重写后的文本，不要解释。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    options={"temperature": 0.1, "num_predict": 200},
                    timeout=15.0,
                )
                if err:
                    logger.warning("[IntentAnalyzer] Local model call failed: %s", err)
                else:
                    cleaned = cls._clean_rewrite(result)
                    if cleaned and len(cleaned) > 2:
                        logger.info(
                            "[IntentAnalyzer] Rewrite OK (%.2fs): '%s' -> '%s'",
                            time.time() - start_time,
                            user_input[:50],
                            cleaned[:60],
                        )
                        return cleaned

        # --- Rule-based fallback: find the last non-repeat user message ---
        _repeat_pats = [
            r'^重复.*任务', r'^再做一遍', r'^再来一次',
            r'^re(peat|do).*last.*task', r'^try.*again',
        ]
        last_user_msg = None
        for msg in reversed(history or []):
            if msg.get("role") == "user":
                content = cls._get_content(msg)
                if content and not any(
                    re.search(p, content, re.IGNORECASE) for p in _repeat_pats
                ):
                    last_user_msg = content
                    break

        if last_user_msg and re.search(
            r'重复.*任务|再做一遍|再来一次|re(peat|do).*last|try.*again',
            user_input, re.IGNORECASE
        ):
            logger.info(
                "[IntentAnalyzer] Rule-based rewrite: '%s' -> '%s'",
                user_input[:50],
                last_user_msg[:50],
            )
            return last_user_msg

        return user_input

    @staticmethod
    def _clean_rewrite(text: str) -> str:
        """Strip common LLM meta-prefixes from a rewrite result."""
        if not text:
            return ""
        text = re.sub(r'^重写后的独立指令[：:]\s*', '', text)
        text = re.sub(r'^重写后的指令[：:]\s*', '', text)
        text = re.sub(r'^独立指令[：:]\s*', '', text)
        return text.strip(' "\'')
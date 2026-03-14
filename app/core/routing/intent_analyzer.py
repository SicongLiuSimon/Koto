import re
import time

from app.core.routing.local_model_router import LocalModelRouter


class IntentAnalyzer:
    """
    意图分析器：结合历史记忆和本地模型，理解用户的复杂指令（如“重复上个任务”、“把刚才那个改成...”）。
    将模糊的指代重写为清晰、独立的指令，以便后续的 SmartDispatcher 准确路由。
    """

    # 触发意图重写的关键词（包含指代词或重复/修改意图）
    TRIGGER_PATTERNS = [
        r"重复.*任务",
        r"再做一遍",
        r"再来一次",
        r"re(peat|do).*last.*task",
        r"try.*again",
        r"刚才",
        r"那个",
        r"上一个",
        r"上个",
        r"继续",
        r"修改",
        r"换成",
        r"改成",
        r"再写一个",
        r"再画一个",
        r"再生成一个",
        r"这个计划",
        r"该计划",
        r"上述计划",
        r"上面的计划",
        r"这个方案",
        r"该方案",
        r"上述方案",
        r"这个大纲",
        r"该大纲",
        r"这个ppt",
        r"该ppt",
        r"这个PPT",
        r"该PPT",
        r"按照这个",
        r"根据这个",
    ]

    REWRITE_PROMPT = """你是一个智能意图重写助手。你的任务是根据用户的历史对话上下文，将用户当前模糊的、带有指代词的输入，重写为一个清晰、独立、完整的指令。

规则：
1. 如果用户要求“重复上个任务”，请提取上一个任务的核心内容并重写。
2. 如果用户要求“修改/换成...”，请结合上一个任务的内容，生成包含修改要求的新指令。
3. 如果用户的输入已经很清晰，不需要上下文也能理解，请直接返回原输入。
4. 只输出重写后的指令文本，不要任何解释、前缀或多余的话。绝对不要输出“重写后的独立指令：”这样的前缀。
{memory_block}
历史对话：
{history}

用户当前输入：
{user_input}

重写后的独立指令："""

    @classmethod
    def should_analyze(cls, user_input: str) -> bool:
        """判断是否需要进行意图重写"""
        return any(
            re.search(p, user_input, re.IGNORECASE) for p in cls.TRIGGER_PATTERNS
        )

    @classmethod
    def rewrite_intent(cls, user_input: str, history: list, memory_context: str = "") -> str:
        """
        使用本地模型（优先）或直接返回（如果不可用）来重写意图。
        history: 格式为 [{"role": "user", "parts": ["..."]}, {"role": "model", "parts": ["..."]}]
        memory_context: 来自长期记忆/画像的用户信息（可选，用于跨 session 指代消歧）
        """
        if not history:
            return user_input

        # 提取最近的 2-3 轮对话作为上下文
        recent_history = []
        for msg in history[-6:]:  # 取最后6条消息（约3轮）
            role = "用户" if msg.get("role") == "user" else "助手"
            content = (msg.get("parts") or [""])[0]
            if content:
                # 截断过长的历史内容，保留核心信息
                content = content[:200] + "..." if len(content) > 200 else content
                recent_history.append(f"{role}: {content}")

        history_text = "\n".join(recent_history)
        _mem_block = (
            f"\n关于用户的长期记忆（辅助理解跨会话指代词）：\n{memory_context[:400]}\n"
            if memory_context else ""
        )
        prompt = cls.REWRITE_PROMPT.format(
            memory_block=_mem_block, history=history_text, user_input=user_input
        )

        # 尝试使用本地模型（通过共享 Ollama 调用工具）
        if LocalModelRouter.is_ollama_available():
            if not LocalModelRouter._initialized:
                LocalModelRouter.init_model()

            if LocalModelRouter._initialized and LocalModelRouter._model_name:
                print(
                    f"[IntentAnalyzer] 🧠 正在使用本地模型 ({LocalModelRouter._model_name}) 分析意图..."
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
                    print(f"[IntentAnalyzer] ⚠️ 本地模型调用失败: {err}")
                else:
                    # 移除可能的前缀
                    result = re.sub(r"^重写后的独立指令：\s*", "", result)
                    result = re.sub(r"^重写后的指令：\s*", "", result)
                    result = result.strip(" \"'")
                    if result and len(result) > 2:
                        print(
                            f"[IntentAnalyzer] ✅ 意图重写成功 ({time.time() - start_time:.2f}s): '{user_input}' -> '{result}'"
                        )
                        return result
                    else:
                        print(f"[IntentAnalyzer] ⚠️ 重写结果为空或过短: '{result}'")
            for msg in reversed(history):
                if msg.get("role") == "user":
                    content = (msg.get("parts") or [""])[0]
                    if not any(
                        re.search(p, content, re.IGNORECASE) for p in repeat_patterns
                    ):
                        last_user_msg = content
                        break
            if last_user_msg:
                print(
                    f"[IntentAnalyzer] 🔄 基础正则匹配成功: '{user_input}' -> '{last_user_msg}'"
                )
                return last_user_msg

        return user_input

# -*- coding: utf-8 -*-
"""
Koto LangChain Adapter
======================
将 Koto 现有的 GeminiProvider 包装为标准 LangChain BaseChatModel，
使其可直接接入 LangGraph / LangChain 生态（Chains、Agents、VectorStores…）

用法示例:
    from app.core.llm.langchain_adapter import KotoLangChainLLM

    llm = KotoLangChainLLM(model_id="gemini-2.5-flash")
    response = llm.invoke("你好")

    # 配合 LangGraph ReAct 使用
    from langgraph.prebuilt import create_react_agent
    agent = create_react_agent(llm, tools=[...])
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# ── 尝试导入 langchain-core（可选依赖，不安装时降级为 stub）───────────────────
try:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import (
        AIMessage,
        AIMessageChunk,
        BaseMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_core.tools import BaseTool

    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False
    BaseChatModel = object  # type: ignore


def _assert_langchain():
    if not _LANGCHAIN_AVAILABLE:
        raise ImportError(
            "langchain-core is required. Install with:\n"
            "  pip install langchain-core langchain-google-genai langgraph"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Message conversion helpers
# ─────────────────────────────────────────────────────────────────────────────


def _lc_messages_to_koto(
    messages: List["BaseMessage"],
) -> tuple[List[Dict], Optional[str]]:
    """
    将 LangChain BaseMessage 列表转换为 Koto history 格式。
    返回: (history_list, system_instruction)
    """
    history: List[Dict] = []
    system_instruction: Optional[str] = None

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_instruction = msg.content
        elif isinstance(msg, HumanMessage):
            history.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            history.append({"role": "model", "content": msg.content})
        elif isinstance(msg, ToolMessage):
            # 工具返回结果作为 function_response
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                }
            )
        else:
            # 其他消息类型降级处理
            history.append({"role": "user", "content": str(msg.content)})

    return history, system_instruction


def _koto_tools_to_lc(lc_tools: Sequence["BaseTool"]) -> List[Dict]:
    """将 LangChain BaseTool 列表转换为 Koto ToolRegistry 格式所期望的 JSON schema。"""
    defs = []
    for tool in lc_tools:
        schema = (
            tool.args_schema.schema()
            if tool.args_schema
            else {"type": "object", "properties": {}}
        )
        defs.append(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
            }
        )
    return defs


# ─────────────────────────────────────────────────────────────────────────────
# KotoLangChainLLM
# ─────────────────────────────────────────────────────────────────────────────

if _LANGCHAIN_AVAILABLE:

    class KotoLangChainLLM(BaseChatModel):
        """
        LangChain-compatible ChatModel backed by Koto's GeminiProvider.

        优势：
        - 复用 Koto 现有的 retry / stream / token-counting 逻辑
        - 100% 兼容 LangGraph create_react_agent / StateGraph
        - 支持 bind_tools() → 自动透传 Gemini Function Calling
        - 支持 stream() → yield AIMessageChunk

        参数:
            model_id    : Gemini 模型 ID（默认: gemini-2.5-flash）
            temperature : 生成温度（默认 0.7）
            max_tokens  : 最大输出 token 数（默认 8192）
        """

        model_id: str = "gemini-3-flash-preview"
        temperature: float = 0.7
        max_tokens: int = 8192
        _koto_provider: Any = None  # GeminiProvider 实例（私有，不序列化）

        class Config:
            arbitrary_types_allowed = True

        def __init__(self, model_id: str = "gemini-3-flash-preview",
                     temperature: float = 0.7, max_tokens: int = 8192, **kwargs):
            _assert_langchain()
            super().__init__(
                model_id=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            # 延迟初始化 GeminiProvider
            from app.core.llm.gemini import GeminiProvider

            object.__setattr__(self, "_koto_provider", GeminiProvider())

        @property
        def _llm_type(self) -> str:
            return "koto-gemini"

        # ── 核心调用 ──────────────────────────────────────────────────────────

        def _generate(
            self,
            messages: List["BaseMessage"],
            stop: Optional[List[str]] = None,
            run_manager=None,
            **kwargs,
        ) -> "ChatResult":
            history, system_instruction = _lc_messages_to_koto(messages)
            tools_def = kwargs.pop("tools", None)

            response = self._koto_provider.generate_content(
                prompt=history,
                model=self.model_id,
                system_instruction=system_instruction,
                tools=tools_def,
                stream=False,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                **kwargs,
            )

            content = response.get("content", "")
            tool_calls_raw = response.get("tool_calls", [])

            # 构造 AIMessage（含 tool_calls 若有）
            if tool_calls_raw:
                ai_msg = AIMessage(
                    content=content,
                    tool_calls=[
                        {
                            "id": tc.get("id", tc.get("name", "")),
                            "name": tc.get("name", ""),
                            "args": tc.get("args", {}),
                            "type": "tool_call",
                        }
                        for tc in tool_calls_raw
                    ],
                )
            else:
                ai_msg = AIMessage(content=content)

            return ChatResult(generations=[ChatGeneration(message=ai_msg)])

        def _stream(
            self,
            messages: List["BaseMessage"],
            stop: Optional[List[str]] = None,
            run_manager=None,
            **kwargs,
        ) -> Iterator["ChatGenerationChunk"]:
            history, system_instruction = _lc_messages_to_koto(messages)

            gen = self._koto_provider.generate_content(
                prompt=history,
                model=self.model_id,
                system_instruction=system_instruction,
                tools=kwargs.pop("tools", None),
                stream=True,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                **kwargs,
            )

            for chunk in gen:
                text = chunk.get("content", "")
                if text:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))

        # ── Token Counting ────────────────────────────────────────────────────

        def get_num_tokens(self, text: str) -> int:
            try:
                history = [{"role": "user", "content": text}]
                return self._koto_provider.get_token_count(history, self.model_id)
            except Exception:
                return len(text) // 4  # 粗略估计

        # ── Tool Binding (function calling) ───────────────────────────────────

        def bind_tools(
            self,
            tools: Sequence[Union["BaseTool", Dict]],
            **kwargs,
        ) -> "KotoLangChainLLM":
            """
            返回绑定了工具 schema 的 LLM 实例副本。
            与 LangGraph create_react_agent 完全兼容。
            """
            raw_tools = []
            for t in tools:
                if isinstance(t, dict):
                    raw_tools.append(t)
                else:
                    raw_tools.append(
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": (
                                t.args_schema.schema()
                                if t.args_schema
                                else {"type": "object", "properties": {}}
                            ),
                        }
                    )
            return self.bind(tools=raw_tools, **kwargs)

else:
    # LangChain 未安装时提供一个占位符
    class KotoLangChainLLM:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "langchain-core is not installed. "
                "Run: pip install langchain-core langchain-google-genai langgraph"
            )

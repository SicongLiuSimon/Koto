# -*- coding: utf-8 -*-
"""
Koto LangGraph ReAct Agent
==========================
用 LangGraph StateGraph 重新实现 UnifiedAgent 的 while 循环，
获得：
  ✅ 明确的节点 / 边 状态机（可视化、可调试）
  ✅ SqliteSaver 检查点 → 跨重启持久化，多轮会话 / 断点续跑
  ✅ 原生流式 token 推送
  ✅ 并发 tool 调用（ThreadPoolExecutor fanout）
  ✅ 内置超时 / 最大步数限制
  ✅ 保留 Koto 现有的 PII 脱敏 + 输出验收护栏

图结构
------
              ┌──────────┐
   start ────>│  reason  │◄──────────────────────────────────┐
              └────┬─────┘                                   │
                   │                                         │
          has_tools│                no_tools                 │
           ┌───────┴────────┐    ┌──────────────┐            │
           │  call_tools    │    │   validate   │            │
           └───────┬────────┘    └──────┬───────┘            │
                   │ results            │                    │
                   │              pass  │  retry             │
                   │            ┌───────┴─┐  ┌──────────┐   │
                   │            │ respond │  │ re_prompt ├───┘
                   │            └─────────┘  └──────────┘
                   └──────────────────────────────────────────> finish

用法
----
    from app.core.agent.langgraph_agent import LangGraphAgent, build_graph
    from app.core.agent.tool_registry import ToolRegistry

    registry = ToolRegistry()
    # ... register tools ...

    agent = LangGraphAgent(registry=registry)
    for chunk in agent.stream("帮我查今天的天气"):
        print(chunk)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Generator, List, Literal, Optional, Sequence

logger = logging.getLogger(__name__)

# ── LangGraph / LangChain 可选依赖 ─────────────────────────────────────────
try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
        BaseMessage,
    )
    from langchain_core.tools import BaseTool, tool as lc_tool
    from typing_extensions import TypedDict, Annotated
    import operator

    _LG_AVAILABLE = True
except ImportError:
    _LG_AVAILABLE = False


def _assert_langgraph():
    if not _LG_AVAILABLE:
        raise ImportError(
            "langgraph is required. Install with:\n"
            "  pip install langgraph langchain-core langchain-google-genai"
        )


# ─────────────────────────────────────────────────────────────────────────────
# State Definition
# ─────────────────────────────────────────────────────────────────────────────

if _LG_AVAILABLE:

    class AgentState(TypedDict):
        messages: Annotated[List[BaseMessage], operator.add]  # message 累积追加
        # ── Koto-specific 字段 ────────────────────────────────────────────────
        skill_id: Optional[str]
        task_type: Optional[str]
        session_id: Optional[str]
        steps_taken: int
        validation_retries: int
        pii_mask_result: Optional[Any]  # PIIMaskResult 实例
        original_input: str  # 原始 (未脱敏) 的 user input
        final_answer: Optional[str]
        error: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Node functions
# ─────────────────────────────────────────────────────────────────────────────

MAX_STEPS = 15
MAX_VAL_RETRIES = 1


def _make_nodes(
    llm,
    registry,
    system_instruction: str,
    enable_pii: bool,
    enable_validation: bool,
    restore_pii: bool,
):
    """闭包工厂：生成绑定了 llm / registry 的节点函数。"""

    # ── 工具映射（name → callable）────────────────────────────────────────
    tool_map: Dict[str, Any] = {}
    try:
        for td in registry.get_definitions():
            name = td["name"]
            tool_map[name] = lambda _n=name, **kw: registry.execute(_n, kw)
    except Exception:
        pass

    def node_reason(state: "AgentState") -> Dict:
        """LLM 推理节点：调用 Gemini 决定下一步（工具调用 or 最终答案）。"""
        messages = state["messages"]
        steps = state.get("steps_taken", 0)

        if steps >= MAX_STEPS:
            logger.warning("[LangGraphAgent] 达到最大步数限制")
            return {
                "final_answer": "⚠️ 任务超出最大推理步数，已中止。",
                "error": "MAX_STEPS_EXCEEDED",
            }

        # 注入 system instruction 为第一条 SystemMessage（若尚无）
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=system_instruction)] + list(messages)

        try:
            response = llm.invoke(messages)
        except Exception as exc:
            logger.error(f"[LangGraphAgent] LLM 调用失败: {exc}")
            return {"error": str(exc), "final_answer": f"❌ 推理错误：{exc}"}

        return {
            "messages": [response],
            "steps_taken": steps + 1,
        }

    def node_call_tools(state: "AgentState") -> Dict:
        """工具执行节点：使用 ThreadPoolExecutor 并发执行所有 tool_calls。"""
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return {}

        from concurrent.futures import ThreadPoolExecutor

        def _run_one(tc) -> ToolMessage:
            t_name = tc["name"]
            t_args = tc.get("args", {})
            t_id = tc.get("id", t_name)
            try:
                result = registry.execute(t_name, t_args)
                result_str = (
                    json.dumps(result, ensure_ascii=False)
                    if not isinstance(result, str)
                    else result
                )
            except Exception as exc:
                result_str = f"[工具错误] {t_name}: {exc}"
                logger.warning(f"[LangGraphAgent] 工具 {t_name} 执行失败: {exc}")
            return ToolMessage(tool_call_id=t_id, content=result_str)

        tool_calls = last.tool_calls
        with ThreadPoolExecutor(max_workers=min(len(tool_calls), 4)) as executor:
            # 提交全部任务，按原始顺序收集结果
            futures = [executor.submit(_run_one, tc) for tc in tool_calls]
            tool_messages = [f.result() for f in futures]

        return {"messages": tool_messages}

    def node_validate(state: "AgentState") -> Dict:
        """输出验收节点：对最终答案执行质量检查 + PII 还原。"""
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return {}

        content = last.content or ""
        if not content:
            return {}

        val_retries = state.get("validation_retries", 0)
        mask_result = state.get("pii_mask_result")
        original_input = state.get("original_input", "")
        skill_id = state.get("skill_id")

        validated_text = content

        # ── 输出质量验收 ─────────────────────────────────────────────────────
        if enable_validation:
            try:
                from app.core.security.output_validator import OutputValidator

                val_result = OutputValidator.validate(
                    text=content,
                    skill_id=skill_id,
                    original_prompt=original_input,
                )
                if val_result.is_blocked:
                    logger.warning(
                        f"[LangGraphAgent] 输出被安全护栏拦截: {val_result.reasons}"
                    )
                    return {
                        "final_answer": val_result.text,
                        "error": "OUTPUT_BLOCKED",
                    }
                elif val_result.needs_retry and val_retries < MAX_VAL_RETRIES:
                    retry_prompt = (
                        f"你上一次的回答存在问题：{'; '.join(val_result.reasons)}。"
                        f"请重新回答，严格按照要求输出。"
                    )
                    return {
                        "messages": [HumanMessage(content=retry_prompt)],
                        "validation_retries": val_retries + 1,
                    }
                else:
                    validated_text = val_result.text
            except Exception as exc:
                logger.warning(f"[LangGraphAgent] 输出验收异常（跳过）: {exc}")

        # ── PII 还原 ──────────────────────────────────────────────────────────
        final = validated_text
        if restore_pii and mask_result and getattr(mask_result, "has_pii", False):
            try:
                final = mask_result.restore(validated_text)
            except Exception as exc:
                logger.warning(f"[LangGraphAgent] PII 还原失败: {exc}")

        return {"final_answer": final}

    return node_reason, node_call_tools, node_validate


# ── 路由函数 ─────────────────────────────────────────────────────────────────


def _route_after_reason(
    state: "AgentState",
) -> Literal["call_tools", "validate", "__end__"]:
    """reason 节点后的路由：有工具调用 → call_tools；否则 → validate。"""
    if state.get("error"):
        return END
    last = state["messages"][-1] if state["messages"] else None
    if last and isinstance(last, AIMessage) and last.tool_calls:
        return "call_tools"
    return "validate"


def _route_after_validate(state: "AgentState") -> Literal["reason", "__end__"]:
    """validate 后的路由：需要重试 → 回到 reason；否则 END。"""
    if state.get("validation_retries", 0) > 0 and not state.get("final_answer"):
        return "reason"
    return END


# ─────────────────────────────────────────────────────────────────────────────
# Graph Builder
# ─────────────────────────────────────────────────────────────────────────────


def build_graph(
    registry,
    model_id: str = "gemini-3-flash-preview",
    system_instruction: Optional[str] = None,
    enable_pii: bool = True,
    enable_validation: bool = True,
    restore_pii: bool = True,
    checkpointer=None,
) -> Any:
    """
    构建并编译 LangGraph StateGraph。

    参数:
        registry            : Koto ToolRegistry 实例
        model_id            : Gemini 模型 ID
        system_instruction  : 系统提示词（覆盖默认）
        enable_pii          : 是否启用 PII 脱敏
        enable_validation   : 是否启用输出验收
        restore_pii         : 是否在输出时还原 PII
        checkpointer        : LangGraph 检查点（默认 MemorySaver）

    返回: 编译好的 CompiledGraph（可直接 .invoke() / .stream()）
    """
    _assert_langgraph()

    from app.core.llm.langchain_adapter import KotoLangChainLLM

    _sys = system_instruction or (
        "You are Koto, an intelligent AI assistant. "
        "Use tools when needed. Think step by step. "
        "When asked about local system status, call system info tools first."
    )

    llm = KotoLangChainLLM(model_id=model_id)

    # 将 ToolRegistry 工具绑定到 LLM（function calling）
    tool_defs = registry.get_definitions()
    if tool_defs:
        llm = llm.bind_tools(tool_defs)

    node_reason, node_call_tools, node_validate = _make_nodes(
        llm=llm,
        registry=registry,
        system_instruction=_sys,
        enable_pii=enable_pii,
        enable_validation=enable_validation,
        restore_pii=restore_pii,
    )

    # ── 构建 StateGraph ───────────────────────────────────────────────────────
    graph = StateGraph(AgentState)

    graph.add_node("reason", node_reason)
    graph.add_node("call_tools", node_call_tools)
    graph.add_node("validate", node_validate)

    graph.set_entry_point("reason")

    # reason → call_tools 或 validate（根据是否有工具调用）
    graph.add_conditional_edges(
        "reason",
        _route_after_reason,
        {"call_tools": "call_tools", "validate": "validate", END: END},
    )

    # call_tools 执行完 → 回到 reason 继续推理
    graph.add_edge("call_tools", "reason")

    # validate → END 或 重试回到 reason
    graph.add_conditional_edges(
        "validate",
        _route_after_validate,
        {"reason": "reason", END: END},
    )

    if checkpointer is None:
        # 优先使用 SqliteSaver（持久化），回退 MemorySaver
        from app.core.agent.checkpoint_manager import get_checkpointer

        checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)


# ─────────────────────────────────────────────────────────────────────────────
# High-level LangGraphAgent wrapper（兼容 UnifiedAgent 接口）
# ─────────────────────────────────────────────────────────────────────────────


class LangGraphAgent:
    """
    高层 Agent 封装，接口与 UnifiedAgent 兼容，但内部使用 LangGraph StateGraph。

    改进点（对比 UnifiedAgent）：
    ─────────────────────────────
    1. 状态机替代 while 循环 → 可可视化 / 可调试
    2. MemorySaver 检查点 → 多轮会话 / 断点续跑
    3. 工具节点并行扇出 → 更快的多工具执行
    4. 原生 LangGraph streaming → token 级别推送
    5. 图结构可导出 Mermaid 图

    用法:
        agent = LangGraphAgent(registry=registry)
        for event in agent.stream("今天北京天气怎么样"):
            print(event)
        # 或阻塞式:
        result = agent.invoke("今天北京天气怎么样")
    """

    def __init__(
        self,
        registry=None,
        model_id: str = "gemini-3-flash-preview",
        system_instruction: Optional[str] = None,
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        enable_pii_filter: bool = True,
        enable_output_validation: bool = True,
        restore_pii_in_output: bool = True,
    ):
        _assert_langgraph()

        from app.core.agent.tool_registry import ToolRegistry

        self.registry = registry or ToolRegistry()
        self.model_id = model_id
        self.system_instruction = system_instruction
        self.skill_id = skill_id
        self.task_type = task_type
        self.enable_pii = enable_pii_filter
        self.enable_validation = enable_output_validation
        self.restore_pii = restore_pii_in_output

        self._graph = build_graph(
            registry=self.registry,
            model_id=model_id,
            system_instruction=system_instruction,
            enable_pii=enable_pii_filter,
            enable_validation=enable_output_validation,
            restore_pii=restore_pii_in_output,
        )

    def _build_initial_state(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        session_id: Optional[str] = None,
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> Dict:
        _assert_langgraph()

        _session_id = session_id or f"koto-{int(time.time())}"
        _skill_id = skill_id or self.skill_id
        _task_type = task_type or self.task_type

        # ── PII 脱敏 ─────────────────────────────────────────────────────────
        mask_result = None
        safe_input = input_text
        if self.enable_pii:
            try:
                from app.core.security.pii_filter import PIIFilter

                mask_result = PIIFilter.mask(input_text)
                if mask_result.has_pii:
                    safe_input = mask_result.masked_text
                    logger.info(
                        f"[LangGraphAgent] 🔒 PII 脱敏: {len(mask_result.mask_map)} 处"
                    )
            except Exception as exc:
                logger.warning(f"[LangGraphAgent] PII 过滤异常: {exc}")

        # ── 历史消息转 LangChain Messages ───────────────────────────────────
        messages: List[BaseMessage] = []
        for h in history or []:
            role = h.get("role", "user")
            content = h.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role in ("model", "assistant"):
                messages.append(AIMessage(content=content))

        messages.append(HumanMessage(content=safe_input))

        return {
            "messages": messages,
            "skill_id": _skill_id,
            "task_type": _task_type,
            "session_id": _session_id,
            "steps_taken": 0,
            "validation_retries": 0,
            "pii_mask_result": mask_result,
            "original_input": input_text,
            "final_answer": None,
            "error": None,
        }

    def invoke(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        """阻塞式调用，返回最终答案字符串。"""
        state = self._build_initial_state(input_text, history, session_id, **kwargs)
        config = {"configurable": {"thread_id": state["session_id"]}}
        result = self._graph.invoke(state, config=config)
        return result.get("final_answer") or (
            result["messages"][-1].content if result["messages"] else ""
        )

    def stream(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> Generator[Dict, None, None]:
        """
        流式调用。每个 yield 是一个事件字典：
            {"type": "token"|"tool_call"|"tool_result"|"answer"|"step_status", "content": "..."}

        step_status 事件（Copilot 风格）：
            {"type": "step_status", "content": "⏳ 正在搜索网络...", "phase": "before"|"after"}
        """
        # ── 工具名 → 中文描述 ──────────────────────────────────────────────
        _TOOL_ACTION_LABELS = {
            "web_search": "🌐 正在搜索网络",
            "search_web": "🌐 正在搜索网络",
            "get_weather": "☁️ 正在获取天气数据",
            "read_file": "📂 正在读取文件",
            "write_file": "💾 正在写入文件",
            "patch_file": "✏️ 正在修改文件",
            "replace_text": "✏️ 正在替换文件内容",
            "list_files": "📁 正在列出文件",
            "execute_code": "⚡ 正在执行代码",
            "run_python": "🐍 正在运行 Python",
            "run_command": "💻 正在执行命令",
            "open_application": "🚀 正在打开应用",
            "get_system_info": "🖥️ 正在获取系统信息",
            "get_cpu_info": "📊 正在获取 CPU 状态",
            "get_memory_info": "📊 正在获取内存状态",
            "get_disk_info": "💽 正在获取磁盘状态",
            "get_network_info": "🔌 正在获取网络状态",
            "get_processes": "📋 正在获取进程列表",
            "take_screenshot": "📸 正在截图",
            "search_files": "🔍 正在全盘搜索文件",
            "create_document": "📄 正在创建文档",
            "generate_image": "🎨 正在生成图像",
            "memory_search": "🧠 正在检索记忆",
            "fetch_url": "🌍 正在获取网页内容",
            "calculate": "🔢 正在计算",
            "translate": "🌐 正在翻译",
        }
        _TOOL_DONE_LABELS = {
            "web_search": "✅ 搜索完成",
            "search_web": "✅ 搜索完成",
            "get_weather": "✅ 天气数据已获取",
            "read_file": "✅ 文件读取完成",
            "write_file": "✅ 文件写入完成",
            "patch_file": "✅ 文件修改完成",
            "replace_text": "✅ 内容替换完成",
            "list_files": "✅ 文件列举完成",
            "execute_code": "✅ 代码执行完成",
            "run_python": "✅ Python 运行完成",
            "run_command": "✅ 命令执行完成",
            "open_application": "✅ 应用已打开",
            "get_system_info": "✅ 系统信息已获取",
            "get_cpu_info": "✅ CPU 状态已获取",
            "get_memory_info": "✅ 内存状态已获取",
            "get_disk_info": "✅ 磁盘状态已获取",
            "get_network_info": "✅ 网络状态已获取",
            "get_processes": "✅ 进程列表已获取",
            "take_screenshot": "✅ 截图完成",
            "search_files": "✅ 文件搜索完成",
            "create_document": "✅ 文档已创建",
            "generate_image": "✅ 图像生成完成",
            "memory_search": "✅ 记忆检索完成",
            "fetch_url": "✅ 网页内容已获取",
            "calculate": "✅ 计算完成",
            "translate": "✅ 翻译完成",
        }

        state = self._build_initial_state(input_text, history, session_id, **kwargs)
        config = {
            "configurable": {"thread_id": state["session_id"]},
            "stream_mode": "messages",
        }

        # 追踪上一个 tool_call 的名称（用于生成 tool_result 时的完成通知）
        _last_tool_names: List[str] = []

        try:
            for event in self._graph.stream(
                state, config=config, stream_mode="updates"
            ):
                for node_name, node_update in event.items():
                    msgs = node_update.get("messages", [])
                    for msg in msgs:
                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    t_name = tc["name"]
                                    t_args = tc.get("args", {})
                                    _last_tool_names.append(t_name)
                                    # Copilot 风格：生成操作前的状态通知
                                    _action_label = _TOOL_ACTION_LABELS.get(
                                        t_name, f"⚙️ 正在调用 {t_name}"
                                    )
                                    _arg_hint = ""
                                    for _key in (
                                        "query",
                                        "keyword",
                                        "path",
                                        "filename",
                                        "url",
                                        "command",
                                        "code",
                                    ):
                                        if _key in t_args:
                                            _val = str(t_args[_key])[:40]
                                            _arg_hint = f"：{_val}"
                                            break
                                    yield {
                                        "type": "step_status",
                                        "content": _action_label + _arg_hint,
                                        "phase": "before",
                                        "tool": t_name,
                                        "args": t_args,
                                    }
                                    yield {
                                        "type": "tool_call",
                                        "content": t_name,
                                        "args": t_args,
                                    }
                            elif msg.content:
                                yield {"type": "token", "content": msg.content}
                        elif isinstance(msg, ToolMessage):
                            # 工具结果到达时：先发送完成通知，再发结果
                            _done_tool = (
                                _last_tool_names.pop(0) if _last_tool_names else ""
                            )
                            _done_label = _TOOL_DONE_LABELS.get(
                                _done_tool,
                                (
                                    f"✅ {_done_tool} 执行完成"
                                    if _done_tool
                                    else "✅ 工具执行完成"
                                ),
                            )
                            yield {
                                "type": "step_status",
                                "content": _done_label,
                                "phase": "after",
                                "tool": _done_tool,
                            }
                            yield {"type": "tool_result", "content": msg.content}

                    if node_update.get("final_answer"):
                        yield {"type": "answer", "content": node_update["final_answer"]}

        except Exception as exc:
            logger.error(f"[LangGraphAgent] stream 异常: {exc}", exc_info=True)
            yield {"type": "error", "content": str(exc)}

    def get_graph_mermaid(self) -> str:
        """导出图结构为 Mermaid 格式（用于可视化调试）。"""
        try:
            return self._graph.get_graph().draw_mermaid()
        except Exception as exc:
            return f"# Error generating graph: {exc}"

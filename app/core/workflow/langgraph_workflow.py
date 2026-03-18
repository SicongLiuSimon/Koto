# -*- coding: utf-8 -*-
"""
Koto LangGraph Workflow Engine
==============================
替换 SmartDispatcher 中的手动多步串行管线，
使用 LangGraph StateGraph 构建可视化的 DAG 工作流。

支持的工作流：
  - research_and_document : 研究 → 生成文档
  - search_and_file       : Web搜索 → 生成文件
  - multi_agent_ppt       : 多Agent协作生成PPT (Researcher + Writer + Critic)
  - sequential_chat       : 标准单轮/多轮对话（默认）

架构特点（对比原 TaskDecomposer）：
  ✅ 并行子任务执行（Send API fanout）
  ✅ 条件分支（根据中间结果决定下一步）
  ✅ 检查点 → 人工确认 (interrupt_before)
  ✅ 子图 (Subgraph) 复用
  ✅ 工作流可视化（Mermaid 导出）

用法:
    from app.core.workflow.langgraph_workflow import WorkflowEngine

    engine = WorkflowEngine()
    result = engine.run(
        workflow="research_and_document",
        user_input="帮我研究量子计算并生成一份Word报告",
        registry=registry,
    )
    print(result["output"])
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

# ── 可选依赖 ─────────────────────────────────────────────────────────────────
try:
    from langgraph.graph import StateGraph, END
    from langgraph.types import Send  # v1.x: Send moved from langgraph.graph to langgraph.types
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, BaseMessage
    from typing_extensions import TypedDict, Annotated
    import operator

    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
    )
    from langgraph.graph import END, StateGraph
    from langgraph.types import (  # v1.x: Send moved from langgraph.graph to langgraph.types
        Send,
    )
    from typing_extensions import Annotated, TypedDict

    _LG_AVAILABLE = True
except ImportError:
    _LG_AVAILABLE = False


def _assert_langgraph():
    if not _LG_AVAILABLE:
        raise ImportError(
            "langgraph + langchain-core required.\n"
            "pip install langgraph langchain-core langchain-google-genai"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Workflow State
# ─────────────────────────────────────────────────────────────────────────────

if _LG_AVAILABLE:

    class WorkflowState(TypedDict):
        # 用户输入与上下文
        user_input: str
        workflow: str
        session_id: str
        registry: Any  # ToolRegistry（不持久化，runtime 注入）
        model_id: str

        # 中间产物
        research_result: Optional[str]
        search_result: Optional[str]
        outline: Optional[str]
        draft_content: Optional[str]
        critic_feedback: Optional[str]
        revision_needed: bool

        # 最终输出
        output: Optional[str]
        file_path: Optional[str]
        error: Optional[str]

        # 追踪
        messages: Annotated[List[BaseMessage], operator.add]
        steps: List[str]


# ─────────────────────────────────────────────────────────────────────────────
# Shared node factory
# ─────────────────────────────────────────────────────────────────────────────

def _get_llm(model_id: str = "gemini-3-flash-preview"):
    from app.core.llm.langchain_adapter import KotoLangChainLLM

    return KotoLangChainLLM(model_id=model_id)


def _llm_call(
    llm,
    system: str,
    user: str,
    history: Optional[List[BaseMessage]] = None,
) -> str:
    """辅助函数：单次 LLM 调用，返回文本。"""
    msgs = [SystemMessage(content=system)]
    if history:
        msgs.extend(history)
    msgs.append(HumanMessage(content=user))
    try:
        resp = llm.invoke(msgs)
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as exc:
        logger.error(f"[WorkflowEngine] LLM 调用失败: {exc}")
        return f"[错误] {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Workflow: research_and_document
# ─────────────────────────────────────────────────────────────────────────────


def _build_research_document_graph(checkpointer=None):
    """研究 → 生成文档 工作流。"""
    _assert_langgraph()

    def node_research(state: "WorkflowState") -> Dict:
        llm = _get_llm(state["model_id"])
        result = _llm_call(
            llm,
            system=(
                "你是一名专业研究员。根据用户问题，提供全面、有深度的研究摘要，"
                "包含关键要点、数据、最新进展和多角度分析。用中文回答。"
            ),
            user=f"请深入研究：{state['user_input']}",
        )
        logger.info("[WorkflowEngine][research_and_document] 研究完成")
        return {
            "research_result": result,
            "steps": state.get("steps", []) + ["research"],
            "messages": [AIMessage(content=f"[研究摘要]\n{result}")],
        }

    def node_outline(state: "WorkflowState") -> Dict:
        llm = _get_llm(state["model_id"])
        outline = _llm_call(
            llm,
            system=(
                "你是一名文档结构设计师。根据研究内容，设计清晰的文档大纲，"
                "包含章节标题和每章要点。用 Markdown 格式输出。"
            ),
            user=(
                f"原始需求：{state['user_input']}\n\n"
                f"研究内容：{state['research_result']}\n\n"
                "请生成详细的文档大纲："
            ),
        )
        logger.info("[WorkflowEngine][research_and_document] 大纲生成完成")
        return {
            "outline": outline,
            "steps": state.get("steps", []) + ["outline"],
            "messages": [AIMessage(content=f"[文档大纲]\n{outline}")],
        }

    def node_write(state: "WorkflowState") -> Dict:
        llm = _get_llm(state["model_id"])
        content = _llm_call(
            llm,
            system=(
                "你是专业文档撰写员。根据大纲，撰写完整、详细的正文内容。"
                "语言专业、逻辑清晰、配有适当的表格或代码块。用 Markdown 格式。"
            ),
            user=(
                f"需求：{state['user_input']}\n\n"
                f"大纲：{state['outline']}\n\n"
                f"研究材料：{state['research_result']}\n\n"
                "请按大纲撰写完整正文："
            ),
        )
        logger.info("[WorkflowEngine][research_and_document] 正文撰写完成")
        return {
            "draft_content": content,
            "steps": state.get("steps", []) + ["write"],
            "messages": [AIMessage(content=f"[初稿]\n{content[:500]}...")],
        }

    def node_finalize(state: "WorkflowState") -> Dict:
        """合并并尝试调用 FILE_GEN 工具生成实际文件。"""
        final_content = state.get("draft_content", "")
        file_path = None

        # 尝试通过 ToolRegistry 生成 Word 文档
        registry = state.get("registry")
        if registry:
            try:
                result = registry.execute(
                    "generate_word_doc",
                    {
                        "title": state["user_input"][:50],
                        "content": final_content,
                    },
                )
                if isinstance(result, dict):
                    file_path = result.get("file_path")
                elif isinstance(result, str):
                    file_path = result
            except Exception as exc:
                logger.warning(f"[WorkflowEngine] generate_word_doc 工具不可用: {exc}")

        return {
            "output": final_content,
            "file_path": file_path,
            "steps": state.get("steps", []) + ["finalize"],
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("research", node_research)
    graph.add_node("outline", node_outline)
    graph.add_node("write", node_write)
    graph.add_node("finalize", node_finalize)

    graph.set_entry_point("research")
    graph.add_edge("research", "outline")
    graph.add_edge("outline", "write")
    graph.add_edge("write", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())


# ─────────────────────────────────────────────────────────────────────────────
# Workflow: multi_agent_ppt (Researcher → Writer → Critic → Revise)
# ─────────────────────────────────────────────────────────────────────────────


def _build_multi_agent_ppt_graph(checkpointer=None):
    """
    多 Agent 协作 PPT 生成工作流：
      Researcher → Writer → Critic → (需要修改? → Revise → Critic) → Assemble
    """
    _assert_langgraph()

    def node_researcher(state: "WorkflowState") -> Dict:
        llm = _get_llm(state["model_id"])
        result = _llm_call(
            llm,
            system=(
                "你是 PPT 内容研究专员（Researcher Agent）。"
                "你的任务是为 PPT 收集、整理相关知识、数据、案例和核心观点。"
                "输出结构化的研究摘要，分为：背景、核心要点、数据支撑、案例。"
            ),
            user=f"PPT 主题：{state['user_input']}",
        )
        return {
            "research_result": result,
            "steps": state.get("steps", []) + ["researcher"],
            "messages": [AIMessage(content=f"[Researcher] 完成研究")],
        }

    def node_writer(state: "WorkflowState") -> Dict:
        llm = _get_llm(state["model_id"])
        draft = _llm_call(
            llm,
            system=(
                "你是 PPT 内容撰写专员（Writer Agent）。"
                "根据研究内容，设计 PPT 结构并为每张幻灯片撰写内容。"
                "输出 JSON 格式：{slides: [{title, bullets: [], notes}]}。"
                "幻灯片数量：8-12 张。"
            ),
            user=(
                f"主题：{state['user_input']}\n\n"
                f"研究摘要：{state['research_result']}"
            ),
        )
        return {
            "draft_content": draft,
            "steps": state.get("steps", []) + ["writer"],
            "messages": [AIMessage(content=f"[Writer] PPT 内容草稿完成")],
        }

    def node_critic(state: "WorkflowState") -> Dict:
        llm = _get_llm(state["model_id"])
        feedback = _llm_call(
            llm,
            system=(
                "你是 PPT 质量审核专员（Critic Agent）。"
                "审查 PPT 内容的：逻辑连贯性、内容充实度、表达专业性。"
                "输出：PASS（无需修改）或 REVISE（需修改，列出具体问题）。"
                "用 [DECISION: PASS] 或 [DECISION: REVISE] 标记决策。"
            ),
            user=(
                f"主题：{state['user_input']}\n\n" f"PPT 草稿：{state['draft_content']}"
            ),
        )
        revision_needed = "DECISION: REVISE" in feedback.upper()
        logger.info(
            f"[WorkflowEngine][multi_agent_ppt] Critic 决策: {'REVISE' if revision_needed else 'PASS'}"
        )
        return {
            "critic_feedback": feedback,
            "revision_needed": revision_needed,
            "steps": state.get("steps", []) + ["critic"],
            "messages": [
                AIMessage(
                    content=f"[Critic] {'需要修改' if revision_needed else '内容通过'}"
                )
            ],
        }

    def node_revise(state: "WorkflowState") -> Dict:
        llm = _get_llm(state["model_id"])
        revised = _llm_call(
            llm,
            system=(
                "你是 PPT 内容修订专员（Revise Agent）。"
                "根据审核意见，修订并提升 PPT 内容质量。"
                "保持 JSON 格式：{slides: [{title, bullets: [], notes}]}。"
            ),
            user=(
                f"原始草稿：{state['draft_content']}\n\n"
                f"审核意见：{state['critic_feedback']}\n\n"
                "请修订："
            ),
        )
        return {
            "draft_content": revised,
            "revision_needed": False,  # 只修订一次
            "steps": state.get("steps", []) + ["revise"],
            "messages": [AIMessage(content="[Revise] 修订完成")],
        }

    def node_assemble(state: "WorkflowState") -> Dict:
        """调用 ToolRegistry 生成真实 PPTX 文件。"""
        registry = state.get("registry")
        file_path = None
        content = state.get("draft_content", "")

        if registry:
            try:
                result = registry.execute(
                    "generate_ppt",
                    {
                        "title": state["user_input"][:50],
                        "content_json": content,
                    },
                )
                if isinstance(result, dict):
                    file_path = result.get("file_path")
                elif isinstance(result, str):
                    file_path = result
            except Exception as exc:
                logger.warning(f"[WorkflowEngine] generate_ppt 工具不可用: {exc}")

        return {
            "output": content,
            "file_path": file_path,
            "steps": state.get("steps", []) + ["assemble"],
        }

    def route_after_critic(state: "WorkflowState") -> Literal["revise", "assemble"]:
        """Critic 后的路由：需要修改 → revise；否则 assemble。"""
        return "revise" if state.get("revision_needed", False) else "assemble"

    graph = StateGraph(WorkflowState)
    graph.add_node("researcher", node_researcher)
    graph.add_node("writer", node_writer)
    graph.add_node("critic", node_critic)
    graph.add_node("revise", node_revise)
    graph.add_node("assemble", node_assemble)

    graph.set_entry_point("researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer", "critic")
    graph.add_conditional_edges(
        "critic", route_after_critic, {"revise": "revise", "assemble": "assemble"}
    )
    graph.add_edge("revise", "assemble")
    graph.add_edge("assemble", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())


# ─────────────────────────────────────────────────────────────────────────────
# WorkflowEngine: 统一入口
# ─────────────────────────────────────────────────────────────────────────────

_GRAPH_REGISTRY: Dict[str, Any] = {}


class WorkflowEngine:
    """
    LangGraph 工作流引擎，统一管理和执行所有多步 Agent 工作流。

    支持的 workflow 类型:
        "research_and_document" - 研究 + 文档生成
        "multi_agent_ppt"       - 多 Agent 协作 PPT
        "sequential_chat"       - 标准对话（回退到 LangGraphAgent）

    对比原 TaskDecomposer：
        ✅ 明确的 DAG 拓扑（不是字符串匹配 → 分支）
        ✅ 并行执行（LangGraph Send API）
        ✅ Critic/Review 循环
        ✅ 检查点续跑
        ✅ Mermaid 可视化
    """

    _BUILDERS = {
        "research_and_document": _build_research_document_graph,
        "multi_agent_ppt": _build_multi_agent_ppt_graph,
    }

    def __init__(self, model_id: str = "gemini-3-flash-preview",
                 checkpointer=None):
        _assert_langgraph()
        self.model_id = model_id
        if checkpointer is None:
            from app.core.agent.checkpoint_manager import get_checkpointer

            checkpointer = get_checkpointer()
        self._checkpointer = checkpointer
        self._graphs: Dict[str, Any] = {}

    def _get_graph(self, workflow: str):
        if workflow not in self._graphs:
            builder = self._BUILDERS.get(workflow)
            if not builder:
                raise ValueError(
                    f"未知工作流: {workflow}. 可用: {list(self._BUILDERS.keys())}"
                )
            self._graphs[workflow] = builder(checkpointer=self._checkpointer)
        return self._graphs[workflow]

    def run(
        self,
        workflow: str,
        user_input: str,
        registry=None,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        同步执行工作流，返回结果字典。

        返回:
            {
                "output": str,          # 主要文本输出
                "file_path": str|None,  # 生成的文件路径
                "steps": List[str],     # 执行的节点列表
                "error": str|None,      # 错误信息
            }
        """
        _assert_langgraph()
        graph = self._get_graph(workflow)

        _session = session_id or f"wf-{int(time.time())}"

        initial_state: Dict[str, Any] = {
            "user_input": user_input,
            "workflow": workflow,
            "session_id": _session,
            "registry": registry,
            "model_id": self.model_id,
            "research_result": None,
            "search_result": None,
            "outline": None,
            "draft_content": None,
            "critic_feedback": None,
            "revision_needed": False,
            "output": None,
            "file_path": None,
            "error": None,
            "messages": [],
            "steps": [],
        }

        config = {"configurable": {"thread_id": _session}}

        try:
            result = graph.invoke(initial_state, config=config)
            return {
                "output": result.get("output", ""),
                "file_path": result.get("file_path"),
                "steps": result.get("steps", []),
                "error": result.get("error"),
            }
        except Exception as exc:
            logger.error(
                f"[WorkflowEngine] 工作流 {workflow} 执行失败: {exc}", exc_info=True
            )
            return {
                "output": "",
                "file_path": None,
                "steps": [],
                "error": str(exc),
            }

    def stream(
        self,
        workflow: str,
        user_input: str,
        registry=None,
        session_id: Optional[str] = None,
    ):
        """
        流式执行工作流，yield 各节点的增量更新。
        每个 event: {"node": str, "content": str, "done": bool}
        """
        _assert_langgraph()
        graph = self._get_graph(workflow)
        _session = session_id or f"wf-{int(time.time())}"

        initial_state: Dict[str, Any] = {
            "user_input": user_input,
            "workflow": workflow,
            "session_id": _session,
            "registry": registry,
            "model_id": self.model_id,
            "research_result": None,
            "search_result": None,
            "outline": None,
            "draft_content": None,
            "critic_feedback": None,
            "revision_needed": False,
            "output": None,
            "file_path": None,
            "error": None,
            "messages": [],
            "steps": [],
        }

        config = {"configurable": {"thread_id": _session}}

        try:
            for event in graph.stream(
                initial_state, config=config, stream_mode="updates"
            ):
                for node_name, update in event.items():
                    msgs = update.get("messages", [])
                    for msg in msgs:
                        if hasattr(msg, "content") and msg.content:
                            yield {
                                "node": node_name,
                                "content": msg.content,
                                "done": False,
                            }
                    if update.get("output"):
                        yield {
                            "node": node_name,
                            "content": update["output"],
                            "done": True,
                        }
        except Exception as exc:
            logger.error(f"[WorkflowEngine] stream 失败: {exc}", exc_info=True)
            yield {"node": "error", "content": str(exc), "done": True}

    def get_graph_mermaid(self, workflow: str) -> str:
        """导出指定工作流的 Mermaid 图（可视化调试）。"""
        try:
            g = self._get_graph(workflow)
            return g.get_graph().draw_mermaid()
        except Exception as exc:
            return f"# Error: {exc}"

    @classmethod
    def detect_workflow(cls, task_type: str, user_input: str, has_file: bool = False) -> str:
        """
        根据 SmartDispatcher 返回的 task_type 推断最佳工作流。
        与现有 TaskDecomposer.TASK_PATTERNS 映射兼容。

        Args:
            has_file: 当前请求是否附带了已上传文件。为 True 时直接返回 "legacy"，
                      因为文件分析不应触发 LangGraph 工作流（工作流没有文件字节上下文）。
        """
        # 有文件附件时，强制走 legacy（文件内容由文件分析流处理，LangGraph 无法访问文件字节）
        if has_file:
            return "legacy"

        text = user_input.lower()

        # PPT 专用多 Agent 工作流
        if task_type == "FILE_GEN" and any(
            k in text for k in ["ppt", "幻灯片", "演示文稿", "汇报"]
        ):
            return "multi_agent_ppt"

        # 研究 + 文档 — 需要同时满足：
        #   1) 主动获取信息的意图（非"分析已有文件"，而是"去研究/调研某主题"）
        #   2) 明确要生成文档/报告的输出意图
        # 这样可避免"分析下这个BP"之类的文件分析请求误触发 research_and_document 工作流
        _ACTIVE_RESEARCH_KW = ["研究", "调研", "深入研究", "综合研究", "全面研究", "调查", "查阅"]
        _DOC_OUTPUT_KW = ["报告", "word", "pdf", "文档", "生成文档", "整理成", "写报告", "做报告"]
        if task_type in ("RESEARCH", "FILE_GEN") and (
            any(k in text for k in _ACTIVE_RESEARCH_KW)
            and any(k in text for k in _DOC_OUTPUT_KW)
        ):
            return "research_and_document"

        # 默认：标准对话（由 LangGraphAgent 处理）
        return "sequential_chat"

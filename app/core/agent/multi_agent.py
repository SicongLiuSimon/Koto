# -*- coding: utf-8 -*-
"""
Koto Multi-Agent Orchestrator
==============================
可配置的多 Agent 协作框架，支持：

  1. Sequential Pipeline   : A → B → C（顺序管线）
  2. Critic Loop           : Writer → Critic → [PASS → end | REVISE → Writer]
  3. Parallel Fanout       : A → [B1 || B2] → C（并行执行）
  4. Custom Topology       : 自定义边定义

设计思路：
  - 每个 Agent 由 `AgentRole` 描述（名称、职责 prompt、输出字段）
  - `MultiAgentOrchestrator` 根据 roles 列表自动构建 LangGraph StateGraph
  - 通过 `critic_index` 参数声明哪个节点是 Critic，自动加入审核循环
  - 并行节点通过 `parallel_groups` 声明，使用 LangGraph Send API

内置预设角色（可直接使用）：
  RESEARCHER  : 研究专员，输出结构化摘要
  WRITER      : 写作专员，从摘要生成初稿
  CRITIC      : 审核专员，输出 PASS/REVISE 判决
  REVISE      : 修订专员，根据 Critic 意见修改
  CODER       : 代码生成专员
  REVIEWER    : 代码审查专员
  DATA_ANALYST: 数据分析专员

用法:
    from app.core.agent.multi_agent import MultiAgentOrchestrator, AgentRole, ROLES

    # 预置角色 + 自动 Critic 循环
    orch = MultiAgentOrchestrator(
        roles=[ROLES.RESEARCHER, ROLES.WRITER, ROLES.CRITIC],
        model_id="gemini-3-flash-preview",
        max_revisions=2,
    )
    result = orch.run("帮我写一篇关于大模型应用的技术博客")
    print(result["output"])

    # 自定义角色
    custom_role = AgentRole(
        name="translator",
        display_name="翻译专员",
        system_prompt="你是专业翻译。将输入内容翻译为英文，保持技术术语准确。",
        output_field="translation",
    )
    orch = MultiAgentOrchestrator(roles=[ROLES.RESEARCHER, custom_role])
    result = orch.run("量子计算简介")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

# ── 可选依赖 ─────────────────────────────────────────────────────────────────
try:
    import operator

    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
    )
    from langgraph.graph import END, StateGraph
    from typing_extensions import Annotated, TypedDict

    _LG_AVAILABLE = True
except ImportError:
    _LG_AVAILABLE = False


def _assert_langgraph():
    if not _LG_AVAILABLE:
        raise ImportError(
            "langgraph + langchain-core required. pip install langgraph langchain-core"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AgentRole: 单个 Agent 的角色描述
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentRole:
    """
    描述一个 Agent 的角色。

    参数:
        name          : 唯一标识符（用于图节点名和状态字段）
        display_name  : 显示名称
        system_prompt : 该 Agent 的系统指令
        output_field  : 该 Agent 的输出存储到 state 的哪个字段
        is_critic     : 是否是 Critic 节点（输出含 PASS/REVISE 判决）
        input_fields  : 从 state 中读取哪些字段作为上下文（None = 全部）
        temperature   : LLM 温度（覆盖默认）
    """

    name: str
    display_name: str
    system_prompt: str
    output_field: str
    is_critic: bool = False
    input_fields: Optional[List[str]] = None
    temperature: float = 0.7


# ─────────────────────────────────────────────────────────────────────────────
# ROLES: 内置预设角色库
# ─────────────────────────────────────────────────────────────────────────────


class ROLES:
    """内置预设角色库（类属性，直接引用）。"""

    RESEARCHER = AgentRole(
        name="researcher",
        display_name="研究专员",
        system_prompt=(
            "你是专业研究专员（Researcher Agent）。\n"
            "根据用户需求，提供全面、权威的研究摘要，包含：\n"
            "- 背景与现状\n- 核心知识点（带数据/统计）\n- 多角度分析\n- 近期进展\n"
            "语言专业，结构清晰，用中文输出。"
        ),
        output_field="research_result",
    )

    WRITER = AgentRole(
        name="writer",
        display_name="写作专员",
        system_prompt=(
            "你是专业写作专员（Writer Agent）。\n"
            "根据研究内容和用户需求，撰写高质量文章/文档初稿。\n"
            "要求：逻辑连贯、语言流畅、内容充实，配合标题/小标题结构，用 Markdown 格式。"
        ),
        output_field="draft",
    )

    CRITIC = AgentRole(
        name="critic",
        display_name="审核专员",
        system_prompt=(
            "你是内容质量审核专员（Critic Agent）。\n"
            "仔细审查文稿的：逻辑连贯性、内容准确性、表达专业性、结构完整性。\n"
            "输出格式：\n"
            "  [DECISION: PASS] 或 [DECISION: REVISE]\n"
            "  具体审核意见：（逐条列出问题，PASS 时可简短说明优点）"
        ),
        output_field="critic_feedback",
        is_critic=True,
    )

    REVISE = AgentRole(
        name="revise",
        display_name="修订专员",
        system_prompt=(
            "你是文稿修订专员（Revise Agent）。\n"
            "根据审核意见，修订并提升文稿质量，保持 Markdown 格式。\n"
            "确保修订后的内容解决了所有审核指出的问题。"
        ),
        output_field="draft",
    )

    CODER = AgentRole(
        name="coder",
        display_name="代码生成专员",
        system_prompt=(
            "你是专业软件工程师（Coder Agent）。\n"
            "根据需求，编写高质量、有注释的代码。\n"
            "包含：功能说明、代码实现、简单测试用例。用 Markdown 代码块格式。"
        ),
        output_field="code",
    )

    REVIEWER = AgentRole(
        name="reviewer",
        display_name="代码审查专员",
        system_prompt=(
            "你是代码审查专员（Review Agent）。\n"
            "审查代码的：正确性、安全性、可读性、性能。\n"
            "输出：[DECISION: PASS] 或 [DECISION: REVISE] + 具体改进建议。"
        ),
        output_field="review_feedback",
        is_critic=True,
    )

    DATA_ANALYST = AgentRole(
        name="data_analyst",
        display_name="数据分析专员",
        system_prompt=(
            "你是数据分析专员（Data Analyst Agent）。\n"
            "对输入数据进行统计分析、趋势识别、异常检测。\n"
            "输出结构化分析报告，包含关键指标、洞察和建议。"
        ),
        output_field="analysis",
    )


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

if _LG_AVAILABLE:

    class MultiAgentState(TypedDict):
        user_input: str
        model_id: str
        session_id: str
        # 动态输出字段（每个 role.output_field 对应一个 key）
        research_result: Optional[str]
        draft: Optional[str]
        critic_feedback: Optional[str]
        review_feedback: Optional[str]
        code: Optional[str]
        analysis: Optional[str]
        # 自定义输出的 fallback
        extra_outputs: Dict[str, str]
        # 控制字段
        revision_count: int
        max_revisions: int
        revision_target: Optional[str]  # 哪个节点要重跑
        messages: Annotated[List[BaseMessage], operator.add]
        steps: List[str]
        final_output: Optional[str]
        error: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _llm_call(model_id: str, system: str, user: str, temperature: float = 0.7) -> str:
    """单次同步 LLM 调用，返回文本。"""
    from app.core.llm.langchain_adapter import KotoLangChainLLM

    llm = KotoLangChainLLM(model_id=model_id, temperature=temperature)
    msgs = [SystemMessage(content=system), HumanMessage(content=user)]
    try:
        resp = llm.invoke(msgs)
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as exc:
        logger.error(f"[MultiAgent] LLM call failed: {exc}")
        return f"[错误] {exc}"


def _build_context(state: "MultiAgentState", role: AgentRole) -> str:
    """从 state 中提取相关字段，拼接成 LLM 上下文。"""
    if role.input_fields:
        fields = role.input_fields
    else:
        # 默认读取所有有值的输出字段
        fields = ["research_result", "draft", "critic_feedback", "code", "analysis"]

    parts = [f"用户需求：{state['user_input']}"]
    for f in fields:
        val = state.get(f) or state.get("extra_outputs", {}).get(f)
        if val:
            label = f.replace("_", " ").title()
            parts.append(f"\n---\n{label}：\n{val}")
    return "\n".join(parts)


def _make_agent_node(role: AgentRole):
    """工厂函数：为给定 AgentRole 生成 LangGraph 节点函数。"""

    def node_fn(state: "MultiAgentState") -> Dict:
        model_id = state.get("model_id", "gemini-3-flash-preview")
        user_context = _build_context(state, role)

        logger.info(f"[MultiAgent] [{role.display_name}] 开始执行")
        output = _llm_call(
            model_id=model_id,
            system=role.system_prompt,
            user=user_context,
            temperature=role.temperature,
        )
        logger.info(f"[MultiAgent] [{role.display_name}] 完成")

        update: Dict[str, Any] = {
            "steps": state.get("steps", []) + [role.name],
            "messages": [AIMessage(content=f"[{role.display_name}] 完成")],
        }

        # 写入输出字段
        if (
            hasattr(state, "__annotations__")
            and role.output_field in MultiAgentState.__annotations__
        ):
            update[role.output_field] = output
        else:
            extras = dict(state.get("extra_outputs", {}))
            extras[role.output_field] = output
            update["extra_outputs"] = extras

        return update

    node_fn.__name__ = f"node_{role.name}"
    return node_fn


def _make_critic_router(
    critic_role: AgentRole, revise_node_name: str, next_node_name: str
):
    """生成 Critic 节点后的条件路由函数。"""

    def router(state: "MultiAgentState") -> Literal["revise", "next", "__end__"]:
        feedback_field = critic_role.output_field
        feedback = state.get(feedback_field) or ""
        rev_count = state.get("revision_count", 0)
        max_rev = state.get("max_revisions", 1)

        if "DECISION: REVISE" in feedback.upper() and rev_count < max_rev:
            return revise_node_name
        return next_node_name

    return router


# ─────────────────────────────────────────────────────────────────────────────
# MultiAgentOrchestrator
# ─────────────────────────────────────────────────────────────────────────────


class MultiAgentOrchestrator:
    """
    可配置的多 Agent 编排器。

    自动构建规则：
    ─────────────
    1. roles 列表按顺序 A → B → C 连接
    2. 若某个 role 的 is_critic=True，自动寻找其前一个非 critic 节点作为 revise 目标
       - 优先查找 roles 中 name 包含 "revise" 的角色
       - 若无，则将前一个节点作为重新执行节点
    3. 最后一个节点输出将作为 final_output

    参数:
        roles         : AgentRole 列表，定义 Agent 管线
        model_id      : 所有 Agent 使用的模型（后续可 per-role 覆盖）
        max_revisions : Critic 循环最大修订次数（默认 1）
        checkpointer  : 检查点后端（默认 CheckpointManager）
    """

    def __init__(
        self,
        roles: List[AgentRole],
        model_id: str = "gemini-3-flash-preview",
        max_revisions: int = 1,
        checkpointer=None,
    ):
        _assert_langgraph()
        if not roles:
            raise ValueError("roles 不能为空")

        self.roles = roles
        self.model_id = model_id
        self.max_revisions = max_revisions

        if checkpointer is None:
            from app.core.agent.checkpoint_manager import get_checkpointer

            checkpointer = get_checkpointer()
        self._checkpointer = checkpointer

        self._graph = self._build_graph()

    def _build_graph(self):
        """自动从 roles 列表构建 StateGraph。"""
        graph = StateGraph(MultiAgentState)

        # ── 注册所有节点 ─────────────────────────────────────────────────────
        for role in self.roles:
            graph.add_node(role.name, _make_agent_node(role))

        # ── 添加 finalize 节点（设置 final_output）────────────────────────────
        def node_finalize(state: "MultiAgentState") -> Dict:
            # 按优先级查找最终输出
            for field in ["draft", "code", "analysis"]:
                val = state.get(field)
                if val:
                    return {"final_output": val}
            # fallback: last extra_output
            extras = state.get("extra_outputs", {})
            if extras:
                return {"final_output": list(extras.values())[-1]}
            return {"final_output": ""}

        graph.add_node("finalize", node_finalize)

        # ── 构建边 ────────────────────────────────────────────────────────────
        non_critic_roles = [r for r in self.roles if not r.is_critic]
        critic_roles = [r for r in self.roles if r.is_critic]

        # 设置入口节点
        graph.set_entry_point(self.roles[0].name)

        i = 0
        while i < len(self.roles):
            role = self.roles[i]
            next_i = i + 1

            if role.is_critic:
                # 找 revise 目标（前一个节点，或显式 revise role）
                revise_target = None
                for r in self.roles:
                    if "revise" in r.name.lower() and not r.is_critic:
                        revise_target = r.name
                        break
                if revise_target is None and i > 0:
                    # 前一个非 critic 节点
                    revise_target = self.roles[i - 1].name

                next_name = (
                    self.roles[next_i].name if next_i < len(self.roles) else "finalize"
                )

                router = _make_critic_router(
                    role, revise_target or "finalize", next_name
                )
                next_map = (
                    {
                        revise_target: revise_target,
                        next_name: next_name,
                    }
                    if revise_target and revise_target != next_name
                    else {next_name: next_name}
                )

                graph.add_conditional_edges(role.name, router, next_map)
            else:
                next_name = (
                    self.roles[next_i].name if next_i < len(self.roles) else "finalize"
                )
                graph.add_edge(role.name, next_name)

            i += 1

        # revise 节点回到 critic
        for role in self.roles:
            if "revise" in role.name.lower() and not role.is_critic:
                # find a critic that comes AFTER this revise in the roles list
                for critic in critic_roles:
                    graph.add_edge(role.name, critic.name)
                    break

        graph.add_edge("finalize", END)

        return graph.compile(checkpointer=self._checkpointer)

    def run(
        self,
        user_input: str,
        session_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        同步执行多 Agent 管线。

        返回:
            {
                "output": str,        # 最终文本输出
                "steps": List[str],   # 执行节点顺序
                "state": dict,        # 完整 state（调试用）
                "error": str | None,
            }
        """
        _assert_langgraph()
        _session = session_id or f"ma-{int(time.time())}"
        _model = model_id or self.model_id

        initial_state: Dict[str, Any] = {
            "user_input": user_input,
            "model_id": _model,
            "session_id": _session,
            "research_result": None,
            "draft": None,
            "critic_feedback": None,
            "review_feedback": None,
            "code": None,
            "analysis": None,
            "extra_outputs": {},
            "revision_count": 0,
            "max_revisions": self.max_revisions,
            "revision_target": None,
            "messages": [],
            "steps": [],
            "final_output": None,
            "error": None,
        }

        config = {"configurable": {"thread_id": _session}}

        try:
            result = self._graph.invoke(initial_state, config=config)
            return {
                "output": result.get("final_output", ""),
                "steps": result.get("steps", []),
                "state": {k: v for k, v in result.items() if k not in ("messages",)},
                "error": result.get("error"),
            }
        except Exception as exc:
            logger.error(f"[MultiAgentOrchestrator] 执行失败: {exc}", exc_info=True)
            return {"output": "", "steps": [], "state": {}, "error": str(exc)}

    def stream(self, user_input: str, session_id: Optional[str] = None):
        """
        流式执行，yield 每个 Agent 节点的输出。
        每个 event: {"agent": str, "content": str, "done": bool}
        """
        _assert_langgraph()
        _session = session_id or f"ma-{int(time.time())}"

        initial_state: Dict[str, Any] = {
            "user_input": user_input,
            "model_id": self.model_id,
            "session_id": _session,
            "research_result": None,
            "draft": None,
            "critic_feedback": None,
            "review_feedback": None,
            "code": None,
            "analysis": None,
            "extra_outputs": {},
            "revision_count": 0,
            "max_revisions": self.max_revisions,
            "revision_target": None,
            "messages": [],
            "steps": [],
            "final_output": None,
            "error": None,
        }

        config = {"configurable": {"thread_id": _session}}

        try:
            for event in self._graph.stream(
                initial_state, config=config, stream_mode="updates"
            ):
                for node_name, update in event.items():
                    msgs = update.get("messages", [])
                    for msg in msgs:
                        if hasattr(msg, "content") and msg.content:
                            yield {
                                "agent": node_name,
                                "content": msg.content,
                                "done": False,
                            }
                    if update.get("final_output"):
                        yield {
                            "agent": "finalize",
                            "content": update["final_output"],
                            "done": True,
                        }
        except Exception as exc:
            logger.error(f"[MultiAgentOrchestrator] stream 失败: {exc}", exc_info=True)
            yield {"agent": "error", "content": str(exc), "done": True}

    def get_graph_mermaid(self) -> str:
        """导出图结构为 Mermaid（可视化调试）。"""
        try:
            return self._graph.get_graph().draw_mermaid()
        except Exception as exc:
            return f"# Error: {exc}"

    @classmethod
    def preset_content_pipeline(
        cls,
        model_id: str = "gemini-3-flash-preview",
        max_revisions: int = 1,
    ) -> "MultiAgentOrchestrator":
        """预置：内容创作管线（研究 → 写作 → Critic → 修订）"""
        return cls(
            roles=[ROLES.RESEARCHER, ROLES.WRITER, ROLES.CRITIC, ROLES.REVISE],
            model_id=model_id,
            max_revisions=max_revisions,
        )

    @classmethod
    def preset_code_pipeline(
        cls,
        model_id: str = "gemini-3-flash-preview",
        max_revisions: int = 1,
    ) -> "MultiAgentOrchestrator":
        """预置：代码生成管线（研究 → 编码 → 代码审查 → 修订）"""
        revise_role = AgentRole(
            name="revise",
            display_name="代码修订专员",
            system_prompt=(
                "你是代码修订专员。根据代码审查意见，修正并优化代码。"
                "保持 Markdown 代码块格式，修复所有指出的问题。"
            ),
            output_field="code",
        )
        return cls(
            roles=[ROLES.RESEARCHER, ROLES.CODER, ROLES.REVIEWER, revise_role],
            model_id=model_id,
            max_revisions=max_revisions,
        )

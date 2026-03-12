import logging
import os
from typing import Optional

# 所有重型导入延迟到工厂函数内部，避免启动时加载 google.genai (~4.7s)

logger = logging.getLogger(__name__)


def _resolve_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """统一读取 API Key，兼容项目内所有环境变量命名。"""
    return (
        api_key
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )


def _build_registry(api_key: Optional[str] = None, full: bool = True) -> "ToolRegistry":
    """
    构建共用的 ToolRegistry 并注册插件。

    Args:
        api_key: Gemini API Key（已解析）。
        full:    True → 注册全量插件（UnifiedAgent 用）；
                 False → 仅注册核心插件（LangGraphAgent 轻量模式）。

    Returns:
        配置好的 ToolRegistry 实例。
    """
    from app.core.agent.plugins.basic_tools_plugin import BasicToolsPlugin
    from app.core.agent.plugins.file_editor_plugin import FileEditorPlugin
    from app.core.agent.plugins.search_plugin import SearchPlugin
    from app.core.agent.plugins.system_info_plugin import SystemInfoPlugin
    from app.core.agent.plugins.system_tools_plugin import SystemToolsPlugin
    from app.core.agent.tool_registry import ToolRegistry

    registry = ToolRegistry()

    # ── 核心插件（全量 & 轻量模式均加载） ──────────────────────────────
    registry.register_plugin(BasicToolsPlugin())
    registry.register_plugin(FileEditorPlugin())
    registry.register_plugin(SearchPlugin(api_key=api_key))
    registry.register_plugin(SystemToolsPlugin())
    registry.register_plugin(SystemInfoPlugin())

    # ── 可选生产力插件（两种模式均尝试加载，失败则跳过） ─────────────
    for plugin_path, name in [
        ("app.core.agent.plugins.productivity_plugin", "ProductivityPlugin"),
        ("app.core.agent.plugins.web_tools_bridge_plugin", "WebToolsBridgePlugin"),
        ("app.core.agent.plugins.memory_tools_plugin", "MemoryToolsPlugin"),
    ]:
        try:
            import importlib

            mod = importlib.import_module(plugin_path)
            cls = getattr(mod, name)
            registry.register_plugin(cls())
        except Exception as _e:
            logger.debug(f"[_build_registry] {name} 跳过: {_e}")

    if not full:
        return registry

    # ── 全量插件（仅 UnifiedAgent 使用） ───────────────────────────────
    from app.core.agent.plugins.alerting_plugin import AlertingPlugin
    from app.core.agent.plugins.auto_remediation_plugin import AutoRemediationPlugin
    from app.core.agent.plugins.configuration_plugin import ConfigurationPlugin
    from app.core.agent.plugins.data_process_plugin import DataProcessPlugin
    from app.core.agent.plugins.image_process_plugin import ImageProcessPlugin
    from app.core.agent.plugins.network_plugin import NetworkPlugin
    from app.core.agent.plugins.performance_analysis_plugin import (
        PerformanceAnalysisPlugin,
    )
    from app.core.agent.plugins.script_generation_plugin import ScriptGenerationPlugin
    from app.core.agent.plugins.system_event_monitoring_plugin import (
        SystemEventMonitoringPlugin,
    )
    from app.core.agent.plugins.trend_analysis_plugin import TrendAnalysisPlugin

    registry.register_plugin(DataProcessPlugin())
    registry.register_plugin(NetworkPlugin())
    registry.register_plugin(ImageProcessPlugin())
    registry.register_plugin(PerformanceAnalysisPlugin())
    registry.register_plugin(SystemEventMonitoringPlugin())
    registry.register_plugin(ScriptGenerationPlugin())
    registry.register_plugin(AlertingPlugin())
    registry.register_plugin(AutoRemediationPlugin())
    registry.register_plugin(TrendAnalysisPlugin())
    registry.register_plugin(ConfigurationPlugin())

    # ── Word 模板技能工具 ──────────────────────────────────────────────
    try:
        from app.core.agent.plugins.template_fill_plugin import TemplateFillPlugin

        registry.register_plugin(TemplateFillPlugin())
    except Exception as _e:
        logger.debug(f"[_build_registry] TemplateFillPlugin 跳过: {_e}")

    # ── 文档生成工具（Word / PDF / Excel / PPT） ──────────────────────
    try:
        from app.core.agent.plugins.doc_gen_plugin import DocGenPlugin

        registry.register_plugin(DocGenPlugin())
    except Exception as _e:
        logger.debug(f"[_build_registry] DocGenPlugin 跳过: {_e}")

    return registry


def create_agent(
    api_key: Optional[str] = None, model_id: str = "gemini-3-flash-preview"
) -> "UnifiedAgent":
    """
    创建全量配置的 UnifiedAgent（ReAct while 循环实现）。

    特性：PII 脱敏、Skill 注入、ToolRouter 过滤、OutputValidator 验收、
         TaskLedger 记录、ShadowTracer 异步学习。
    所有重型依赖均在此函数内懒加载，不影响启动速度。
    """
    from app.core.agent.unified_agent import UnifiedAgent
    from app.core.llm.gemini import GeminiProvider

    usage_api_key = _resolve_api_key(api_key)
    if not usage_api_key:
        logger.warning("No API Key provided for Agent. Agent will fail at generation.")

    llm_provider = GeminiProvider(api_key=usage_api_key)
    registry = _build_registry(api_key=usage_api_key, full=True)

    return UnifiedAgent(
        llm_provider=llm_provider,
        tool_registry=registry,
        model_id=model_id,
        use_tool_router=True,
        tool_router_max=20,
    )


def create_local_agent(model: str = None, base_url: str = None) -> "UnifiedAgent":
    """
    创建以本地 Ollama 为 LLM 后端的 UnifiedAgent。

    与 create_agent() 行为完全一致（ReAct + 工具调用 + Skill 注入），
    但底层 LLM 为本地 Ollama 模型，无需 API Key。
    Skills 通过 UnifiedAgent.run() 中的 inject_into_prompt() 自动注入。
    """
    from app.core.agent.unified_agent import UnifiedAgent
    from app.core.llm.ollama_llm_provider import OllamaLLMProvider
    from app.core.routing.local_model_router import LocalModelRouter

    if not model:
        try:
            LocalModelRouter.init_model()
            model = (
                getattr(LocalModelRouter, "_model_name", None)
                or LocalModelRouter.pick_best_chat_model()
            )
        except Exception:
            pass  # model 保持 None → OllamaLLMProvider 在调用时自动解析

    llm_kwargs = {}
    if base_url:
        llm_kwargs["base_url"] = base_url
    llm_provider = OllamaLLMProvider(model=model, **llm_kwargs)
    registry = _build_registry(api_key=None, full=True)
    logger.info(f"[create_local_agent] 使用本地模型: {model}")

    return UnifiedAgent(
        llm_provider=llm_provider,
        tool_registry=registry,
        model_id=model,
        use_tool_router=True,
        tool_router_max=15,
    )


def create_langgraph_agent(
    api_key: Optional[str] = None,
    model_id: str = "gemini-3-flash-preview",
    enable_pii_filter: bool = True,
    enable_output_validation: bool = True,
) -> "LangGraphAgent":
    """
    创建基于 LangGraph StateGraph 的 ReAct Agent。

    对比 create_agent()（UnifiedAgent）的优势：
      ✅ 状态机替代 while 循环 → 可可视化 / 可调试
      ✅ MemorySaver 检查点 → 多轮对话不丢失上下文
      ✅ 工具节点并行执行
      ✅ 原生 LangGraph streaming
      ✅ 图结构可导出 Mermaid

    当 langgraph 未安装时抛出 ImportError（明确提示安装方式）。
    """
    try:
        from app.core.agent.langgraph_agent import LangGraphAgent
    except ImportError as exc:
        raise ImportError(
            "LangGraph Agent 需要额外依赖：\n"
            "  pip install langgraph langchain-core langchain-google-genai\n"
            f"原始错误: {exc}"
        ) from exc

    _key = _resolve_api_key(api_key)
    registry = _build_registry(api_key=_key, full=False)

    return LangGraphAgent(
        registry=registry,
        model_id=model_id,
        enable_pii_filter=enable_pii_filter,
        enable_output_validation=enable_output_validation,
    )


def create_multi_agent(
    api_key: Optional[str] = None,
    model_id: str = "gemini-2.5-flash-preview-05-20",
    max_revisions: int = 1,
) -> "MultiAgentOrchestrator":
    """
    创建多 Agent 协作编排器（Researcher → Writer → Critic 三角协作）。

    支持三种拓扑：
      - sequential  : 顺序管道，每步输出作为下步输入
      - critic_loop : Writer 输出经 Critic 审核，可回退修改（默认最多 max_revisions 轮）
      - parallel    : 并行执行多个 Agent，汇总结果

    使用示例::

        orchestrator = create_multi_agent()
        result = orchestrator.run(
            task="研究并撰写一篇关于量子计算的深度报告",
        )
        print(result["final_output"])

    需要安装：pip install langgraph langchain-core langchain-google-genai
    """
    from app.core.agent.multi_agent import ROLES, MultiAgentOrchestrator

    _key = _resolve_api_key(api_key)
    if _key:
        os.environ.setdefault("GEMINI_API_KEY", _key)

    return MultiAgentOrchestrator(
        roles=[ROLES.RESEARCHER, ROLES.WRITER, ROLES.CRITIC, ROLES.REVISE],
        model_id=model_id,
        max_revisions=max_revisions,
    )

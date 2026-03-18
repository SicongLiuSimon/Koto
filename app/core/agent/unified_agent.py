import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Union

from app.core.agent.base import Agent
from app.core.agent.tool_registry import ToolRegistry
from app.core.agent.types import AgentAction, AgentResponse, AgentStep, AgentStepType
from app.core.config_defaults import DEFAULT_MODEL
from app.core.llm.base import LLMProvider


# ── 任务管理子系统（懒加载，避免循环依赖和启动开销）──────────────────────────
def _get_task_ledger():
    from app.core.tasks.task_ledger import get_ledger

    return get_ledger()


def _get_progress_bus():
    from app.core.tasks.progress_bus import ProgressEvent, get_progress_bus

    return get_progress_bus(), ProgressEvent


# ── 阶段一护栏模块（懒加载，避免启动时开销）──────────────────────────
def _get_pii_filter():
    from app.core.security.pii_filter import PIIConfig, PIIFilter

    return PIIFilter, PIIConfig


def _get_output_validator():
    from app.core.security.output_validator import OutputValidator

    return OutputValidator


def _get_shadow_tracer():
    from app.core.learning.shadow_tracer import ShadowTracer

    return ShadowTracer


logger = logging.getLogger(__name__)


class UnifiedAgent(Agent):
    """
    Unified Agent that supports ReAct loop and tool execution.
    Replaces web/agent_loop.py and web/adaptive_agent.py

    v2 新增
    ───────
    - PII 脱敏护栏：input 发往云端前自动掩码，answer 返回后还原
    - 输出质量验收：最终答案经 OutputValidator 检查，必要时触发重试或格式化
    - Shadow Tracing 接口：通过 report_feedback() 触发影子记录
    """

    MAX_STEPS = 15
    # 输出验收最大重试次数（RETRY action 触发时）
    MAX_VALIDATION_RETRIES = 1
    
    def __init__(
        self,
        llm_provider: LLMProvider,
        tool_registry: Optional[ToolRegistry] = None,
        model_id: str = "gemini-2.5-flash",
        system_instruction: Optional[str] = None,
        # ── v2 参数 ──────────────────────────────────────────────
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        enable_pii_filter: bool = True,
        enable_output_validation: bool = True,
        restore_pii_in_output: bool = True,
        # ── v3 参数 ──────────────────────────────────────────────
        use_tool_router: bool = True,
        tool_router_max: int = 20,
    ):
        super().__init__(llm_provider)
        self.registry = tool_registry or ToolRegistry()
        self.model_id = model_id
        self.base_system_instruction = system_instruction or (
            "You are Koto, an intelligent AI assistant. "
            "You can use tools to answer user questions. "
            "Think step-by-step. "
            "When the user asks about local machine status (CPU, memory, disk, network, processes, Python environment), "
            "prioritize calling available system info tools first, then explain results concisely. "
            "Do not guess live system metrics when tools are available.\n"
            "## File editing rules (MUST follow)\n"
            "- To modify part of a file: use replace_text (single change) or patch_file (multiple changes). "
            "Do NOT read the file before calling these — they handle the read internally.\n"
            "- To apply several unrelated changes to the same file: use patch_file with all patches in ONE call "
            "instead of calling replace_text multiple times.\n"
            "- write_file is ONLY for creating a new file or completely rewriting a file from scratch. "
            "Never use write_file just to change a few lines.\n"
            "## 实时数据规则（必须遵守）\n"
            "- 当用户询问任何实时/当前/外部数据（金价、油价、汇率、天气、股票行情、新闻、最新价格等），"
            "必须调用 web_search 工具获取真实答案。\n"
            "- 以下类型的查询同样必须调用 web_search，不得依赖训练数据作答：\n"
            "  * 特定企业/公司的最新状况、经营情况、产品发布、人事变动\n"
            "  * 融资/投资/上市/估值/股权等资本市场动态\n"
            "  * 行业市场规模、竞争格局、最新政策法规\n"
            "  * 任何以「最新/最近/当前/目前/现在/今年/本月」等时间词修饰的具体事实\n"
            "  * 特定人物的最新动态、职位或公开言论\n"
            "- 【训练截止规则】本模型的训练数据存在截止日期，当前系统时间已注入在上方。"
            "若用户询问的信息可能发生在训练截止日期之后，必须先调用 web_search 核实，"
            "不得凭训练数据直接作答或编造细节。若无法确定是否超出截止日期，默认调用 web_search。\n"
            "- 严禁以「我没有联网接口」、「我无法获取实时数据」作为回复，也严禁生成 Python 代码片段（如 yfinance/akshare/requests）作为替代答案。\n"
            "- 如果 web_search 工具可用，直接调用；如不可用，则明确告知用户「当前环境下网络工具不可用，以下信息来自训练数据，可能不是最新情况」。\n"
            "## P2: 记忆工具主动使用规则\n"
            "- 当用户明确要求「记住」「不要忘记」某事，立即调用 memory_save 工具保存。\n"
            "- 当你在对话中发现用户的重要偏好、习惯、决定或个人事实（如：用户偏好简短回答、用户是 Python 开发者），"
            "主动调用 memory_save 保存，无需等待用户要求。\n"
            "- 当用户询问关于自身的历史偏好、过去的决策、或之前讨论过的内容时，"
            "先调用 memory_search 检索相关记忆，再结合检索结果回答，不要凭空猜测。\n"
            "- memory_save 的 category 参数：user_fact（用户事实）、preference（偏好）、"
            "decision（决策）、reminder（提醒）、topic_summary（主题摘要）。"
        )
        # v2
        self.skill_id = skill_id
        self.task_type = task_type
        self.enable_pii_filter = enable_pii_filter
        self.enable_output_validation = enable_output_validation
        self.restore_pii_in_output = restore_pii_in_output
        # v3 ToolRouter
        self.use_tool_router = use_tool_router
        if use_tool_router:
            try:
                from app.core.routing.tool_router import get_tool_router

                self._tool_router = get_tool_router(max_tools=tool_router_max)
            except Exception as _e:
                logger.warning(
                    f"[UnifiedAgent] ToolRouter 初始化失败（降级为全量）: {_e}"
                )
                self._tool_router = None
        else:
            self._tool_router = None

    def run(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        session_id: Optional[str] = None,
        # 运行时可覆盖 skill_id / task_type
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        # v4: 多轮对话语义上下文注入（来自 ConversationTracker / CWM）
        system_context: Optional[str] = None,
    ) -> Generator[AgentStep, None, None]:
        """
        Executes the agent loop. Yields AgentStep objects to track progress.

        新增流程（v2）
        ─────────────
        1. PII 脱敏  → masked_input 发往 LLM
        2. ReAct 循环（不变）
        3. 最终答案输出验收 → PASS/REFORMAT/RETRY/BLOCK
        4. PII 还原  → 用户看到含原始信息的答案

        v4 新增
        ───────
        - system_context: 来自 ConversationTracker 和 ContextWindowManager 的
          对话上下文摘要，追加到 system_instruction 末尾，增强多轮语义连贯性
        """
        _skill_id = skill_id or self.skill_id
        _task_type = task_type or self.task_type
        _session_id = session_id or ""

        # ── 任务台账集成 ──────────────────────────────────────────────────────
        _task_id: str = str(uuid.uuid4())
        _ledger = None
        try:
            _ledger = _get_task_ledger()
            _task_rec = _ledger.create(
                session_id=_session_id,
                user_input=input_text[:500],
                task_type=_task_type or "agent",
                skill_id=_skill_id,
                source="agent",
            )
            _task_id = _task_rec.task_id
            _ledger.mark_running(_task_id)
        except Exception as _ledger_err:
            logger.debug(f"[UnifiedAgent] TaskLedger 初始化跳过: {_ledger_err}")

        def _pub(
            step_type: str,
            content: str,
            tool_name=None,
            tool_args=None,
            observation=None,
            progress: int = 0,
        ):
            """向 TaskLedger 追加步骤并广播到 ProgressBus。"""
            try:
                if _ledger:
                    _ledger.add_step(
                        _task_id,
                        step_type=step_type,
                        content=content,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        observation=observation,
                    )
                _bus, _EvtCls = _get_progress_bus()
                _bus.publish_step(
                    _task_id,
                    _session_id,
                    step_type,
                    content[:300],
                    progress=progress,
                    tool_name=tool_name,
                )
            except Exception:
                pass

        # ── 1. PII 脱敏 ─────────────────────────────────────────────
        mask_result = None
        safe_input = input_text
        if self.enable_pii_filter:
            try:
                PIIFilter, PIIConfig = _get_pii_filter()
                mask_result = PIIFilter.mask(input_text)
                if mask_result.has_pii:
                    safe_input = mask_result.masked_text
                    logger.info(
                        f"[UnifiedAgent] 🔒 PII 脱敏完成，共 {len(mask_result.mask_map)} 处，"
                        f"统计: {mask_result.stats}"
                    )
            except Exception as e:
                logger.warning(f"[UnifiedAgent] PII 过滤异常（跳过）: {e}")
                safe_input = input_text

        steps_taken = 0
        current_history = list(history) if history else []
        current_history.append({"role": "user", "content": safe_input})

        final_answer: Optional[str] = None
        validation_retries = 0
        # P0: 记录本次会话中 LLM 原生调用的 skill_* 工具（不含用户手动激活的 skill）
        _native_skill_calls: list = []

        # ── Skill 注入：将启用的 Skills 注入到 system_instruction ──────────────
        _effective_instruction = self.base_system_instruction
        _auto_skill_ids: list = []   # 提前初始化保证后续规划步骤可引用
        try:
            from app.core.skills.skill_manager import SkillManager

            # 自动匹配：当用户没有手动启用适合本轮任务的 Skill 时，
            # 使用本地模型（或规则兜底）推荐最合适的临时 Skill
            _auto_skill_ids: list = []
            try:
                from app.core.skills.skill_auto_matcher import SkillAutoMatcher

                _auto_skill_ids = SkillAutoMatcher.match(
                    user_input=safe_input,
                    task_type=_task_type or "CHAT",
                )
                if _auto_skill_ids:
                    logger.info(
                        f"[UnifiedAgent] 🤖 AutoMatcher 推荐: "
                        f"{SkillAutoMatcher.describe_matched(_auto_skill_ids)}"
                    )
            except Exception as _ame:
                logger.debug(f"[UnifiedAgent] AutoMatcher 跳过: {_ame}")

            # ── TriggerBinding 补充匹配：将用户配置的意图绑定合并进来 ──────────
            try:
                from app.core.skills.skill_trigger_binding import SkillBindingManager
                _binding_mgr = SkillBindingManager()
                _binding_mgr.ensure_recommended_bindings()
                _binding_ids = _binding_mgr.match_intent(safe_input)
                if _binding_ids:
                    # 去重并合并，AutoMatcher 推荐优先，Binding 补充在后
                    _merged = list(dict.fromkeys(_auto_skill_ids + _binding_ids))
                    _auto_skill_ids = _merged[:6]
                    logger.info(
                        f"[UnifiedAgent] 🔗 TriggerBinding 补充: "
                        f"{_binding_ids} → 合并后: {_auto_skill_ids}"
                    )
            except Exception as _tbe:
                logger.debug(f"[UnifiedAgent] TriggerBinding 跳过: {_tbe}")

            _effective_instruction = SkillManager.inject_into_prompt(
                self.base_system_instruction,
                task_type=_task_type,
                user_input=safe_input,
                temp_skill_ids=_auto_skill_ids,
            )

            # 显式 skill_id 允许在单次请求中强制注入一个未启用的技能。
            if _skill_id:
                runtime_state = getattr(SkillManager, "_registry", {}).get(
                    _skill_id, {}
                )
                runtime_enabled = bool(runtime_state.get("enabled", False))
                runtime_skill = SkillManager.get_definition(_skill_id)
                if runtime_skill and not runtime_enabled:
                    _is_domain = (
                        getattr(runtime_skill.category, "value", runtime_skill.category) == "domain"
                    )
                    runtime_prompt = runtime_skill.render_prompt(
                        with_examples=_is_domain,
                        with_output_spec=_is_domain,
                    ).strip()
                    if runtime_prompt:
                        _effective_instruction = (
                            _effective_instruction
                            + "\n\n─────────────────────────────────────────"
                            + "\n## 🎯 本轮请求命中的运行时 Skill\n"
                            + runtime_prompt
                        )
        except Exception as _se:
            logger.debug(f"[UnifiedAgent] Skill 注入跳过: {_se}")

        # ── executor_tools 过滤：收集显式激活 Skill 的工具白名单 ─────────────
        # 只对用户显式指定的 _skill_id 做强制过滤（精确任务场景）
        # auto-matched 的临时 skill 不做强制限制（保持完整工具集以支持通用对话）
        _executor_tool_whitelist: Optional[set] = None
        if _skill_id:
            try:
                from app.core.skills.skill_manager import SkillManager as _SM_et
                _active_def = _SM_et.get_definition(_skill_id)
                if _active_def and getattr(_active_def, "executor_tools", None):
                    _executor_tool_whitelist = set(_active_def.executor_tools)
                    logger.info(
                        f"[UnifiedAgent] 🔧 executor_tools 过滤激活 ({_skill_id}): "
                        f"{sorted(_executor_tool_whitelist)}"
                    )
            except Exception as _ete:
                logger.debug(f"[UnifiedAgent] executor_tools 收集跳过: {_ete}")

        # ── 注入本地时间（每次请求动态注入，确保模型感知当前时间）──────────────
        _now = datetime.now()
        _weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][_now.weekday()]
        _time_prefix = (
            f"当前本地时间：{_now.strftime('%Y年%m月%d日 %H:%M')}（{_weekday}）\n"
            f"⚠️ 本模型训练数据有截止日期，当前系统时间即为真实参考时间。"
            f"对于所有涉及"最新/目前/当前"状态的具体事实查询，请优先调用 web_search 工具获取最新信息，"
            f"不要依赖可能已过时的训练数据。\n\n"
        )
        _effective_instruction = _time_prefix + _effective_instruction

        # ── v4: 多轮对话上下文注入（来自 ConversationTracker / CWM paged-in）──
        if system_context and system_context.strip():
            _effective_instruction = (
                _effective_instruction
                + "\n\n"
                + system_context.strip()
            )
            logger.debug("[UnifiedAgent] 注入对话上下文 (%d chars)", len(system_context))

        # ── 0. 规划反馈：立即向用户报告初始化状态 ────────────────────────────────
        try:
            _tool_count = len(self.registry.get_definitions()) if self.registry else 0
            # 尝试获取技能显示名称
            _skill_display: list = []
            if _auto_skill_ids:
                try:
                    from app.core.skills.skill_manager import SkillManager as _SM_p
                    _SM_p._ensure_init()
                    for _sid in _auto_skill_ids:
                        _entry = _SM_p._registry.get(_sid, {})
                        _icon = _entry.get("icon", "")
                        _name = _entry.get("name", _sid)
                        _skill_display.append(f"{_icon}{_name}" if _icon else _name)
                except Exception:
                    _skill_display = list(_auto_skill_ids)
            _skill_part = ("，技能: " + " · ".join(_skill_display)) if _skill_display else ""
            _tool_part = (f"，{_tool_count} 个工具可用") if _tool_count else ""
            _planning_msg = "正在分析请求" + _skill_part + _tool_part
            _pub("THOUGHT", _planning_msg)
        except Exception:
            pass

        while steps_taken < self.MAX_STEPS:
            steps_taken += 1

            all_tools_def = self.registry.get_definitions()
            # v3: 用 ToolRouter 过滤工具，减少 token 消耗
            if self._tool_router and all_tools_def:
                tools_def = self._tool_router.select(all_tools_def, safe_input)
            else:
                tools_def = all_tools_def
            # v3.1: executor_tools 白名单过滤（Skill 显式声明时生效）
            if _executor_tool_whitelist and tools_def:
                filtered = [t for t in tools_def if t.get("name") in _executor_tool_whitelist]
                if filtered:  # 非空才应用，防止白名单与 ToolRouter 结果完全不重叠时工具断供
                    tools_def = filtered
                    logger.debug(
                        f"[UnifiedAgent] 工具白名单过滤后: {[t.get('name') for t in tools_def]}"
                    )

            # v4: skill executor_tools 精确工具集（优先级高于 ToolRouter）
            # 当 Skill 声明了 executor_tools，只向 LLM 暴露该子集，
            # 避免无关工具稀释 context 并让模型精确使用设计好的工具。
            if _skill_id:
                try:
                    from app.core.skills.skill_manager import SkillManager as _SM
                    _sk_def = _SM.get_definition(_skill_id)
                    if _sk_def and _sk_def.executor_tools:
                        _allowed = set(_sk_def.executor_tools)
                        _et_filtered = [t for t in tools_def if t.get("name") in _allowed]
                        if _et_filtered:
                            tools_def = _et_filtered
                            logger.debug(
                                "[UnifiedAgent] 🎯 Skill '%s' executor_tools 过滤: %d → %d 工具",
                                _skill_id, len(all_tools_def), len(tools_def),
                            )
                except Exception as _ste:
                    logger.debug("[UnifiedAgent] executor_tools 过滤跳过: %s", _ste)

            try:
                # 使用 ModelFallbackExecutor：首选 self.model_id，失败时自动降级
                try:
                    from app.core.llm.model_fallback import get_fallback_executor
                    _executor = get_fallback_executor()
                    response = _executor.generate_with_fallback(
                        provider=self.llm,
                        prompt=current_history,
                        preferred_model=self.model_id,
                        task_type=_task_type or self.task_type or "CHAT",
                        system_instruction=_effective_instruction,
                        tools=tools_def if tools_def else None,
                        stream=False,
                    )
                    # 如果执行器选了不同的模型，同步更新当前 model_id
                    # （不修改 self.model_id，避免影响外部状态）
                except ImportError:
                    response = self.llm.generate_content(
                        prompt=current_history,
                        model=self.model_id,
                        system_instruction=_effective_instruction,
                        tools=tools_def if tools_def else None,
                        stream=False,
                    )
                
                content_text = response.get("content", "")
                tool_calls = response.get("tool_calls", [])

                if content_text:
                    yield AgentStep(
                        step_type=AgentStepType.THOUGHT, 
                        content=content_text
                    )
                    _pub("THOUGHT", content_text)
                    current_history.append({"role": "model", "content": content_text})

                if not tool_calls:
                    # ── 2. 输出质量验收 ──────────────────────────────
                    validated_text = content_text
                    if self.enable_output_validation and content_text:
                        try:
                            OutputValidator = _get_output_validator()
                            val_result = OutputValidator.validate(
                                text=content_text,
                                skill_id=_skill_id,
                                original_prompt=input_text,
                            )
                            if val_result.is_blocked:
                                logger.warning(
                                    f"[UnifiedAgent] 🚫 输出被安全护栏拦截: {val_result.reasons}"
                                )
                                yield AgentStep(
                                    step_type=AgentStepType.ANSWER,
                                    content=val_result.text,
                                    metadata={
                                        "validation_action": "BLOCK",
                                        "reasons": val_result.reasons,
                                    },
                                )
                                return

                            elif (
                                val_result.needs_retry
                                and validation_retries < self.MAX_VALIDATION_RETRIES
                            ):
                                validation_retries += 1
                                logger.info(
                                    f"[UnifiedAgent] 🔄 输出质量不合格，触发重试 "
                                    f"({validation_retries}/{self.MAX_VALIDATION_RETRIES}): {val_result.reasons}"
                                )
                                # 向 LLM 追加验收失败原因，要求重新输出
                                # 如果 validator 提供了自定义修复提示则使用它，否则用通用提示
                                if val_result.text and val_result.text != content_text:
                                    retry_prompt = val_result.text
                                else:
                                    retry_prompt = (
                                        f"你上一次的回答存在问题：{'; '.join(val_result.reasons)}。"
                                        f"请重新回答，严格按照要求的格式输出。"
                                    )
                                current_history.append(
                                    {"role": "user", "content": retry_prompt}
                                )
                                continue  # 重新进入循环

                            else:
                                # PASS / WARN / REFORMAT
                                validated_text = val_result.text
                                if val_result.action == "REFORMAT":
                                    logger.info(
                                        f"[UnifiedAgent] 🔧 输出已自动格式化: {val_result.reasons}"
                                    )

                        except Exception as e:
                            logger.warning(f"[UnifiedAgent] 输出验收异常（跳过）: {e}")

                    # ── 2b. P1: Skill OutputSpec 格式验收 ─────────────
                    # 当激活了某个 Skill 且其 output_spec 有非空约束时，
                    # 验证输出是否符合格式要求，不符合则注入修正提示并重试。
                    _spec_skill_id = _skill_id or (
                        _auto_skill_ids[0] if _auto_skill_ids else None
                    )
                    if (
                        validated_text
                        and _spec_skill_id
                        and validation_retries < self.MAX_VALIDATION_RETRIES
                    ):
                        try:
                            from app.core.skills.skill_manager import (
                                SkillManager as _SM_spec,
                            )

                            _sk_def = _SM_spec.get_definition(_spec_skill_id)
                            if _sk_def and _sk_def.output_spec:
                                _spec_ok, _spec_reason = _sk_def.output_spec.validate(
                                    validated_text
                                )
                                if not _spec_ok:
                                    validation_retries += 1
                                    logger.info(
                                        "[UnifiedAgent] 📋 OutputSpec 格式检查未通过 "
                                        "(%s): %s — 触发重试 %d/%d",
                                        _spec_skill_id,
                                        _spec_reason,
                                        validation_retries,
                                        self.MAX_VALIDATION_RETRIES,
                                    )
                                    current_history.append(
                                        {
                                            "role": "user",
                                            "content": (
                                                f"你的回答格式不符合「{_sk_def.name}」技能要求："
                                                f"{_spec_reason}。请严格按照要求重新输出。"
                                            ),
                                        }
                                    )
                                    continue
                        except Exception as _spec_err:
                            logger.debug(
                                "[UnifiedAgent] OutputSpec 检查跳过: %s", _spec_err
                            )

                    # ── 3. PII 还原 ──────────────────────────────────
                    final_answer = validated_text
                    if (
                        self.restore_pii_in_output
                        and mask_result
                        and mask_result.has_pii
                    ):
                        try:
                            final_answer = mask_result.restore(validated_text)
                        except Exception as e:
                            logger.warning(f"[UnifiedAgent] PII 还原异常（跳过）: {e}")
                            final_answer = validated_text

                    # ── 4. Skill 感知提示：告知用户 LLM 调用了他未手动开启的 Skill ──
                    # 条件：本次会话中有 skill_* 工具被 LLM 原生调用，
                    # 且该 skill 未被用户在技能面板中手动启用（无论 _skill_id 是否存在）
                    if _native_skill_calls:
                        try:
                            from app.core.skills.skill_manager import (
                                SkillManager as _SM_notify,
                            )
                            _SM_notify._ensure_init()
                            _skill_tags: list = []
                            for _stn in _native_skill_calls:
                                _sid_notify = _stn[len("skill_"):]
                                _entry = _SM_notify._registry.get(_sid_notify, {})
                                _icon = _entry.get("icon", "")
                                _name = _entry.get("name", _sid_notify)
                                _skill_tags.append(
                                    f"{_icon}**{_name}**" if _icon else f"**{_name}**"
                                )
                            if _skill_tags:
                                _tags_str = "、".join(_skill_tags)
                                _notice = (
                                    f"\n\n---\n💡 *本次回答自动启用了技能：{_tags_str}"
                                    f"。你也可以在技能面板中手动开启它们以持续生效。*"
                                )
                                final_answer = (final_answer or "") + _notice
                        except Exception as _nfy_err:
                            logger.debug(
                                "[UnifiedAgent] Skill 感知提示跳过: %s", _nfy_err
                            )

                    yield AgentStep(
                        step_type=AgentStepType.ANSWER,
                        content=final_answer,
                        metadata={
                            "session_id": _session_id,
                            "skill_id": _skill_id,
                            "task_type": _task_type,
                            "pii_masked": mask_result.has_pii if mask_result else False,
                            "task_id": _task_id,
                            "native_skills_used": _native_skill_calls or None,
                        },
                    )
                    _pub("ANSWER", (final_answer or "")[:500], progress=100)
                    try:
                        if _ledger:
                            _ledger.mark_completed(
                                _task_id, result_summary=(final_answer or "")[:500]
                            )
                    except Exception:
                        pass
                    break

                # ── 取消 / 打断检查（在工具调用前）──────────────────────────────
                try:
                    if _ledger:
                        if _ledger.is_cancel_requested(_task_id):
                            logger.info(
                                f"[UnifiedAgent] ✖ 任务 {_task_id[:8]} 收到取消请求"
                            )
                            _ledger.mark_cancelled(_task_id)
                            yield AgentStep(
                                step_type=AgentStepType.ERROR,
                                content="任务已被用户取消",
                            )
                            return
                        if _ledger.is_interrupt_requested(_task_id):
                            import time as _time_mod

                            logger.info(
                                f"[UnifiedAgent] ⏸ 任务 {_task_id[:8]} 打断，等待恢复"
                            )
                            yield AgentStep(
                                step_type=AgentStepType.THOUGHT,
                                content="⏸ 任务已暂停，等待用户确认后继续...",
                            )
                            # 阻塞直到解除打断或取消（最多等 5 分钟）
                            _wait_start = _time_mod.monotonic()
                            while _ledger.is_interrupt_requested(_task_id):
                                if _time_mod.monotonic() - _wait_start > 300:
                                    _ledger.mark_cancelled(_task_id)
                                    yield AgentStep(
                                        step_type=AgentStepType.ERROR,
                                        content="等待超时，任务已取消",
                                    )
                                    return
                                _time_mod.sleep(1)
                            if _ledger.is_cancel_requested(_task_id):
                                _ledger.mark_cancelled(_task_id)
                                yield AgentStep(
                                    step_type=AgentStepType.ERROR,
                                    content="任务已被用户取消",
                                )
                                return
                except Exception as _ctrl_err:
                    logger.debug(f"[UnifiedAgent] 控制检查跳过: {_ctrl_err}")

                # ── 工具调用（支持并行执行）────────────────────────────────────
                # 1. 先顺序 yield 所有 ACTION（生成器本身不能并行 yield）
                for tool_call in tool_calls:
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("args", {})

                    # 追踪 LLM 原生调用的 skill_* 工具中，用户 *未手动启用* 的部分
                    # 判断「用户是否启用」：SkillManager._registry[sid].enabled == True
                    if (
                        tool_name
                        and tool_name.startswith("skill_")
                        and tool_name not in _native_skill_calls
                    ):
                        try:
                            from app.core.skills.skill_manager import SkillManager as _SM_tr
                            _sid_tr = tool_name[len("skill_"):]
                            _entry_tr = _SM_tr._registry.get(_sid_tr, {})
                            _user_enabled = bool(_entry_tr.get("enabled", False))
                        except Exception:
                            _user_enabled = False
                        if not _user_enabled:
                            _native_skill_calls.append(tool_name)

                    action_obj = AgentAction(
                        tool_name=tool_name, tool_args=tool_args, tool_call_id=None
                    )
                    yield AgentStep(
                        step_type=AgentStepType.ACTION,
                        content=f"Calling tool: {tool_name}",
                        action=action_obj
                    )
                    _pub("ACTION", f"Calling tool: {tool_name}",
                         tool_name=tool_name, tool_args=tool_args)
                    current_history.append({
                        "role": "model",
                        "content": "",
                        "tool_calls": [tool_call]
                    })

                # 2. 并行执行所有工具（多工具时可大幅减少等待时间）
                def _exec_one(tc):
                    _n = tc.get("name")
                    _a = tc.get("args", {})
                    try:
                        return _n, str(self.registry.execute(_n, _a))
                    except Exception as _e:
                        return _n, f"Error: {_e}"

                if len(tool_calls) > 1:
                    observations: Dict[str, str] = {}
                    with ThreadPoolExecutor(
                        max_workers=min(len(tool_calls), 6)
                    ) as _pool:
                        _futures = {
                            _pool.submit(_exec_one, tc): tc for tc in tool_calls
                        }
                        for _fut in as_completed(_futures):
                            _n, _obs = _fut.result()
                            observations[_n] = _obs
                    logger.debug(f"[UnifiedAgent] ⚡ {len(tool_calls)} 个工具并行完成")
                    for tool_call in tool_calls:
                        _n = tool_call.get("name")
                        observation = observations.get(_n, "Error: result missing")
                        yield AgentStep(
                            step_type=AgentStepType.OBSERVATION,
                            content=observation,
                            observation=observation
                        )
                        _pub("OBSERVATION", observation[:500],
                             tool_name=_n, observation=observation[:500])
                        current_history.append({
                            "role": "function",
                            "name": _n,
                            "content": observation
                        })
                else:
                    # 单工具直接执行
                    _n, observation = _exec_one(tool_calls[0])
                    yield AgentStep(
                        step_type=AgentStepType.OBSERVATION,
                        content=observation,
                        observation=observation
                    )
                    _pub("OBSERVATION", observation[:500],
                         tool_name=_n, observation=observation[:500])
                    current_history.append({
                        "role": "function",
                        "name": _n,
                        "content": observation
                    })
            
            except Exception as e:
                logger.error(f"Agent loop error: {e}", exc_info=True)
                yield AgentStep(
                    step_type=AgentStepType.ERROR,
                    content=f"An error occurred: {str(e)}",
                )
                _pub("ERROR", str(e))
                try:
                    if _ledger:
                        _ledger.mark_failed(_task_id, error=str(e))
                except Exception:
                    pass
                break

    # ─── v2 新增公开方法 ────────────────────────────────────────────────

    def report_feedback(
        self,
        feedback_type: str,
        session_id: str,
        user_input: str,
        ai_response: str,
        model_used: str = "",
        latency_ms: Optional[int] = None,
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        向 ShadowTracer 上报用户反馈，触发影子记录。

        Args:
            feedback_type: "approved"（点赞）| "adopted"（采纳）
            session_id   : 对话 session ID
            user_input   : 用户的原始输入（ShadowTracer 内部会再次脱敏）
            ai_response  : AI 的最终响应
            model_used   : 使用的模型名称
            latency_ms   : 响应延迟毫秒数
            skill_id     : 对应 Skill ID（None 表示未关联 Skill）
            task_type    : 任务分类
            metadata     : 额外元数据

        Returns:
            trace_id (str) 或 None
        """
        _skill_id = skill_id or self.skill_id
        _task_type = task_type or self.task_type
        try:
            ShadowTracer = _get_shadow_tracer()
            if feedback_type == "approved":
                return ShadowTracer.record_approved(
                    session_id=session_id,
                    user_input=user_input,
                    ai_response=ai_response,
                    skill_id=_skill_id,
                    task_type=_task_type,
                    model_used=model_used,
                    latency_ms=latency_ms,
                    metadata=metadata,
                )
            elif feedback_type == "adopted":
                return ShadowTracer.record_adopted(
                    session_id=session_id,
                    user_input=user_input,
                    ai_response=ai_response,
                    skill_id=_skill_id,
                    task_type=_task_type,
                    model_used=model_used,
                    latency_ms=latency_ms,
                    metadata=metadata,
                )
            else:
                logger.warning(f"[UnifiedAgent] 未知反馈类型: {feedback_type}")
                return None
        except Exception as e:
            logger.error(f"[UnifiedAgent] 上报反馈失败: {e}")
            return None

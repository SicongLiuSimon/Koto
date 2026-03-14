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
            "- 严禁以「我没有联网接口」、「我无法获取实时数据」作为回复，也严禁生成 Python 代码片段（如 yfinance/akshare/requests）作为替代答案。\n"
            "- 如果 web_search 工具可用，直接调用；如不可用，则明确告知用户「当前环境下网络工具不可用」。"
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
    ) -> Generator[AgentStep, None, None]:
        """
        Executes the agent loop. Yields AgentStep objects to track progress.

        新增流程（v2）
        ─────────────
        1. PII 脱敏  → masked_input 发往 LLM
        2. ReAct 循环（不变）
        3. 最终答案输出验收 → PASS/REFORMAT/RETRY/BLOCK
        4. PII 还原  → 用户看到含原始信息的答案
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

        # ── Skill 注入：将启用的 Skills 注入到 system_instruction ──────────────
        _effective_instruction = self.base_system_instruction
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

        # ── 注入本地时间（每次请求动态注入，确保模型感知当前时间）──────────────
        _now = datetime.now()
        _weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][_now.weekday()]
        _time_prefix = (
            f"当前本地时间：{_now.strftime('%Y年%m月%d日 %H:%M')}（{_weekday}）\n\n"
        )
        _effective_instruction = _time_prefix + _effective_instruction

        while steps_taken < self.MAX_STEPS:
            steps_taken += 1

            all_tools_def = self.registry.get_definitions()
            # v3: 用 ToolRouter 过滤工具，减少 token 消耗
            if self._tool_router and all_tools_def:
                tools_def = self._tool_router.select(all_tools_def, safe_input)
            else:
                tools_def = all_tools_def

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
                        task_type=getattr(self, "_task_type", "CHAT") or "CHAT",
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

                    yield AgentStep(
                        step_type=AgentStepType.ANSWER,
                        content=final_answer,
                        metadata={
                            "session_id": _session_id,
                            "skill_id": _skill_id,
                            "task_type": _task_type,
                            "pii_masked": mask_result.has_pii if mask_result else False,
                            "task_id": _task_id,
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

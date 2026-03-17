# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   Koto  ─  SkillPipeline（技能顺序执行链）                        ║
╚══════════════════════════════════════════════════════════════════╝

让多个 Skill 按声明顺序依次执行，前一步的输出自动传递给下一步。

核心概念
────────
PipelineStep
    单个管道步骤，绑定一个 skill_id 和可选的 output→input 映射规则。
    - ``output_key``   : 本步骤结果存入 context 的键名
    - ``input_from``   : 从 context 中读取哪些键注入到本步骤（dict: context_key → skill参数名）
    - ``pass_full_ctx``: True 时把整个 context 作为 ``context`` 参数传给 callable

SkillPipeline
    编排多个 PipelineStep，按顺序调用 SkillCapabilityRegistry.dispatch()，
    汇总每步骤结果到共享 context。

典型用法
────────
    from app.core.skills.skill_pipeline import SkillPipeline, PipelineStep

    result = SkillPipeline(steps=[
        PipelineStep("workspace_context", output_key="project_ctx"),
        PipelineStep("debug_python",      input_from={"project_ctx": "context"}),
        PipelineStep("write_unit_tests",  input_from={"debug_python": "source_code"}),
    ]).run(user_input="帮我调试这段代码并补全单元测试", context={"file_path": "app/foo.py"})

    # result 是最后一步的输出，result.context 包含所有步骤的中间结果

SkillChain（快捷方式）
────────────────────
    from app.core.skills.skill_pipeline import SkillChain

    # 直接从 skill 的 chains_to 字段自动构建流水线
    chain = SkillChain.from_chains_to("debug_python", depth=2)
    chain.run(user_input="...", context={})
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════


@dataclass
class PipelineStep:
    """管道中的一个执行步骤。"""

    skill_id: str
    """要执行的 Skill ID（须在 SkillCapabilityRegistry 注册了 callable）"""

    output_key: Optional[str] = None
    """本步骤结果存入共享 context 的键名。
    None = 自动用 skill_id 作为 key。"""

    input_from: Dict[str, str] = field(default_factory=dict)
    """从 context 中取哪些键注入到本步骤的调用参数。
    格式: {context_key: callable_param_name}
    例: {"project_ctx": "context"} → context["project_ctx"] 作为 callable 的 context 参数传入"""

    pass_full_ctx: bool = False
    """True: 将整个 context dict 作为 ``context`` 参数透传给 callable。"""

    skip_on_error: bool = True
    """True: 本步骤抛异常时记录日志并继续下一步，而非中止整个 pipeline。"""

    @property
    def effective_output_key(self) -> str:
        return self.output_key or self.skill_id


@dataclass
class PipelineResult:
    """SkillPipeline.run() 的返回值"""

    final_output: Any
    """最后一步的输出"""

    context: Dict[str, Any]
    """包含所有步骤输出的共享上下文"""

    steps_executed: List[str]
    """成功执行的 step.skill_id 列表（按顺序）"""

    steps_skipped: List[str]
    """因报错被跳过的 step.skill_id 列表"""

    elapsed_ms: float
    """总耗时（毫秒）"""

    @property
    def success(self) -> bool:
        return bool(self.steps_executed) and not self.steps_skipped


# ══════════════════════════════════════════════════════════════════
# SkillPipeline
# ══════════════════════════════════════════════════════════════════


class SkillPipeline:
    """
    按顺序执行多个 Skill，前一步输出自动传递给下一步。

    每个步骤调用 SkillCapabilityRegistry.dispatch()，
    该方法会根据 entry_point 或代码注册的 callable 执行真正的逻辑。
    对于只有 prompt 而没有 entry_point 的 Skill，dispatch 会返回 None，
    pipeline 会跳过该步骤的结果（但不报错）。
    """

    def __init__(self, steps: List[PipelineStep]):
        if not steps:
            raise ValueError("SkillPipeline 需要至少一个 PipelineStep")
        self.steps = steps

    def run(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> PipelineResult:
        """
        顺序执行所有步骤。

        Parameters
        ----------
        user_input : 传递给每个步骤的用户原始输入（不可变，各步骤共享）
        context    : 初始共享上下文 dict，会被各步骤的输出不断扩充

        Returns
        -------
        PipelineResult
        """
        from app.core.skills.skill_capability import SkillCapabilityRegistry

        ctx: Dict[str, Any] = dict(context or {})
        steps_executed: List[str] = []
        steps_skipped: List[str] = []
        last_output: Any = None
        t0 = time.perf_counter()

        for step in self.steps:
            # 构建本步骤的调用参数
            call_ctx: Dict[str, Any] = {"skill_id": step.skill_id, **ctx}

            if step.pass_full_ctx:
                call_ctx["context"] = dict(ctx)
            else:
                for ctx_key, param_name in step.input_from.items():
                    if ctx_key in ctx:
                        call_ctx[param_name] = ctx[ctx_key]

            logger.debug(
                "[SkillPipeline] ▶ step: %s | ctx_keys=%s",
                step.skill_id,
                list(call_ctx.keys()),
            )

            try:
                output = SkillCapabilityRegistry.dispatch(
                    step.skill_id,
                    user_input=user_input,
                    context=call_ctx,
                )
                if output is not None:
                    ctx[step.effective_output_key] = output
                    last_output = output
                steps_executed.append(step.skill_id)
                logger.info("[SkillPipeline] ✅ step done: %s", step.skill_id)

            except Exception as exc:
                logger.warning(
                    "[SkillPipeline] ⚠️ step failed: %s — %s", step.skill_id, exc
                )
                if step.skip_on_error:
                    steps_skipped.append(step.skill_id)
                else:
                    elapsed = (time.perf_counter() - t0) * 1000
                    return PipelineResult(
                        final_output=last_output,
                        context=ctx,
                        steps_executed=steps_executed,
                        steps_skipped=steps_skipped,
                        elapsed_ms=elapsed,
                    )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "[SkillPipeline] 完成 | steps=%d skipped=%d elapsed=%.1fms",
            len(steps_executed),
            len(steps_skipped),
            elapsed_ms,
        )
        return PipelineResult(
            final_output=last_output,
            context=ctx,
            steps_executed=steps_executed,
            steps_skipped=steps_skipped,
            elapsed_ms=elapsed_ms,
        )


# ══════════════════════════════════════════════════════════════════
# SkillChain — 从 chains_to 字段自动构建 pipeline
# ══════════════════════════════════════════════════════════════════


class SkillChain:
    """
    从某个 Skill 的 ``chains_to`` 声明自动构建 SkillPipeline 的工厂类。

    用法示例
    --------
        # 从 debug_python 开始，自动扩展 chains_to（最深 2 层）
        chain = SkillChain.from_chains_to("debug_python", depth=2)
        result = chain.run(user_input="调试这段代码", context={"file_path": "app/foo.py"})
    """

    @classmethod
    def from_chains_to(
        cls,
        root_skill_id: str,
        depth: int = 1,
        pass_full_ctx: bool = True,
    ) -> SkillPipeline:
        """
        以 root_skill_id 为起点，递归跟随 chains_to 构建 pipeline。

        Parameters
        ----------
        root_skill_id : 链的起始 Skill
        depth         : 最多跟随几层 chains_to（防止循环 / 过深）
        pass_full_ctx : 是否把完整 context 传给每个步骤

        Returns
        -------
        SkillPipeline
        """
        try:
            from app.core.skills.skill_manager import SkillManager

            SkillManager._ensure_init()
        except Exception as exc:
            raise RuntimeError(f"SkillChain: SkillManager 加载失败 — {exc}") from exc

        steps: List[PipelineStep] = []
        visited: set = set()

        def _collect(skill_id: str, remaining_depth: int) -> None:
            if skill_id in visited or remaining_depth < 0:
                return
            visited.add(skill_id)
            steps.append(
                PipelineStep(
                    skill_id=skill_id,
                    output_key=skill_id,
                    pass_full_ctx=pass_full_ctx,
                )
            )
            if remaining_depth == 0:
                return
            s_def = SkillManager._def_registry.get(skill_id)
            if not s_def:
                return
            for next_id in getattr(s_def, "chains_to", []) or []:
                _collect(next_id, remaining_depth - 1)

        _collect(root_skill_id, depth)

        if not steps:
            raise ValueError(f"SkillChain: 找不到 Skill '{root_skill_id}'")

        logger.debug("[SkillChain] 构建完成: %s", " → ".join(s.skill_id for s in steps))
        return SkillPipeline(steps=steps)

    @classmethod
    def build_from_active(
        cls,
        active_skill_ids: List[str],
        pass_full_ctx: bool = True,
    ) -> Optional[SkillPipeline]:
        """
        从当前激活的 Skill 列表中，挑选有 entry_point 的 Skill 构建管道。

        没有 entry_point 的 Skill（纯 prompt 类）被过滤掉，因为它们无法被
        SkillCapabilityRegistry.dispatch() 调用，在 pipeline 中执行没意义。

        Returns None 当没有任何可执行步骤时。
        """
        try:
            from app.core.skills.skill_capability import SkillCapabilityRegistry
            from app.core.skills.skill_manager import SkillManager

            SkillManager._ensure_init()
        except Exception:
            return None

        steps = []
        for sid in active_skill_ids:
            s_def = SkillManager._def_registry.get(sid)
            if not s_def:
                continue
            has_ep = bool(getattr(s_def, "entry_point", None))
            has_cap = SkillCapabilityRegistry.has_capability(sid)
            if has_ep or has_cap:
                steps.append(
                    PipelineStep(
                        skill_id=sid, output_key=sid, pass_full_ctx=pass_full_ctx
                    )
                )

        if not steps:
            return None
        return SkillPipeline(steps=steps)

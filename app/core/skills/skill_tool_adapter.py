# -*- coding: utf-8 -*-
"""
SkillToolAdapter — Bridges SkillDefinition → ToolRegistry
==========================================================

P0 升级：将 Skill 系统从「Prompt 注入」升级为「原生函数调用」。

每个 Skill 被注册为 ToolRegistry 中的一个真实工具，工具名称为 ``skill_<skill_id>``。
LLM 通过原生 function calling 自行决定何时调用哪个 Skill，以及传入什么参数，
从而取代 SkillAutoMatcher 基于正则/Ollama 的猜测式激活机制。

调用优先级
----------
1. 若 Skill 在 SkillCapabilityRegistry 中注册了 callable（entry_point / 代码注册），
   则以 ``dispatch()`` 执行真实逻辑并返回结果。
2. 否则（model_hint / 纯 Prompt 型 Skill），返回渲染好的 Prompt 指导文本，
   让 LLM 在下一推理步骤中将其纳入上下文并据此输出。

接入方式（factory._build_registry 中调用一次即可）：
    from app.core.skills.skill_tool_adapter import SkillToolAdapter
    SkillToolAdapter.register_all(registry)

SkillAutoMatcher 仍保留为降级兜底，当 Skill 工具未被 LLM 触发时提供候选建议。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.core.agent.tool_registry import ToolRegistry
    from app.core.skills.skill_schema import SkillDefinition


class SkillToolAdapter:
    """将 SkillDefinitions 批量转换为 ToolRegistry 条目。"""

    # 工具名前缀，避免与插件注册的工具名冲突
    PREFIX = "skill_"

    @classmethod
    def register_all(
        cls,
        registry: "ToolRegistry",
        task_type: str = "",
        only_enabled: bool = False,
    ) -> int:
        """
        将所有 Skill 注册为 registry 中的可调用工具。

        Args:
            registry:     目标 ToolRegistry 实例。
            task_type:    若非空，只注册 task_types 包含此值的 Skill（空列表 Skill 不过滤）。
            only_enabled: True 时只注册已启用的 Skill；默认注册全部，让 LLM 自行选择。

        Returns:
            成功注册的 Skill 数量。
        """
        try:
            from app.core.skills.skill_manager import SkillManager

            SkillManager._ensure_init()
        except Exception as e:
            logger.warning("[SkillToolAdapter] SkillManager 初始化失败: %s", e)
            return 0

        try:
            skill_defs: List["SkillDefinition"] = list(
                SkillManager._def_registry.values()
            )
        except Exception as e:
            logger.warning("[SkillToolAdapter] 无法读取 Skill 注册表: %s", e)
            return 0

        count = 0
        for skill_def in skill_defs:
            # 启用状态过滤
            if only_enabled and not skill_def.enabled:
                continue

            # task_type 过滤（空列表 = 所有类型，不过滤）
            if (
                task_type
                and skill_def.task_types
                and task_type not in skill_def.task_types
            ):
                continue

            try:
                tool_name, func, description, parameters = cls._build_tool(skill_def)
                registry.register_tool(
                    name=tool_name,
                    func=func,
                    description=description,
                    parameters=parameters,
                )
                count += 1
                logger.debug("[SkillToolAdapter] 注册 Skill 工具: %s", tool_name)
            except Exception as e:
                logger.debug("[SkillToolAdapter] 跳过 Skill '%s': %s", skill_def.id, e)

        logger.info("[SkillToolAdapter] 共注册 %d 个 Skill 工具", count)
        return count

    @classmethod
    def _build_tool(
        cls,
        skill_def: "SkillDefinition",
    ) -> Tuple[str, Any, str, Dict]:
        """
        为单个 Skill 构建 (tool_name, callable, description, parameters) 四元组。

        Parameters 来源：
          - 若 skill_def.input_variables 非空：使用 to_mcp_tool() 的 inputSchema
          - 始终补充 user_input 字段（LLM 传入本轮用户消息的总结/摘要）

        Callable 路由逻辑：
          1. 若 SkillCapabilityRegistry 有该 Skill 的可调用实现 → dispatch()
          2. 否则 → 返回渲染好的 Skill 指导 Prompt，供 LLM 在下一步推理中参考
        """
        from app.core.skills.skill_capability import SkillCapabilityRegistry

        skill_id = skill_def.id
        tool_name = cls.PREFIX + skill_id

        # ── 描述：组合 description + intent_description + when_not_to_use ────────────────────
        icon = getattr(skill_def, "icon", "") or ""
        description = (
            f"{icon} {skill_def.name}：{skill_def.description or skill_def.name}"
        )
        if skill_def.intent_description:
            description += f"\n\n✅ 使用时机：{skill_def.intent_description}"
        when_not = getattr(skill_def, "when_not_to_use", "") or ""
        if when_not:
            description += f"\n\n❌ 不要在以下情况使用：{when_not}"

        # ── 参数 Schema：从 to_mcp_tool() 提取，再补充 user_input ──────────────
        try:
            mcp = skill_def.to_mcp_tool()
            input_schema: Dict = mcp.get("inputSchema", {})
            props: Dict = dict(input_schema.get("properties") or {})
            required: List[str] = list(input_schema.get("required") or [])
        except Exception:
            props, required = {}, []

        # 补充 user_input（如未声明）
        if "user_input" not in props:
            props["user_input"] = {
                "type": "string",
                "description": "用户的原始请求文本或需要处理的内容摘要",
            }

        parameters: Dict = {"type": "object", "properties": props}
        if required:
            parameters["required"] = required

        # ── Callable：dispatch 或返回 Prompt 指导 ─────────────────────────────
        has_capability = SkillCapabilityRegistry.has_capability(skill_id)

        def _tool_fn(
            _s_id: str = skill_id,
            _s_def: "SkillDefinition" = skill_def,
            _has_cap: bool = has_capability,
            **kwargs: Any,
        ) -> str:
            user_input: str = kwargs.pop("user_input", "") or ""

            # 1. 有真实实现：走 dispatch()
            if _has_cap:
                try:
                    result = SkillCapabilityRegistry.dispatch(
                        _s_id,
                        user_input=user_input,
                        context=kwargs,
                    )
                    # 非字符串结果序列化为字符串
                    if result is None:
                        pass  # 降到 Prompt 指导
                    elif not isinstance(result, str):
                        import json as _json

                        try:
                            return _json.dumps(result, ensure_ascii=False)
                        except Exception:
                            return str(result)
                    else:
                        return result
                except Exception as e:
                    import json as _json

                    logger.debug(
                        "[SkillToolAdapter] dispatch '%s' 失败，降级为 Prompt 指导: %s",
                        _s_id,
                        e,
                    )
                    return _json.dumps(
                        {
                            "status": "error",
                            "skill": _s_id,
                            "message": f"技能执行出错：{str(e)[:200]}",
                            "retry_hint": "请检查参数格式是否正确，或补充更多上下文信息后重试",
                        },
                        ensure_ascii=False,
                    )

            # 2. Prompt 指导型：渲染 Skill 的 system prompt 片段并返回
            guidance = _s_def.render_prompt(
                variables=kwargs if kwargs else None,
                with_examples=True,
                with_output_spec=True,
            )
            if guidance and guidance.strip():
                return (
                    f"[{_s_def.icon or '🎯'} {_s_def.name} 已激活]\n\n"
                    f"{guidance.strip()}\n\n"
                    f"请严格按照上述要求处理用户的请求：{user_input}"
                )
            return f"[{_s_id}] {_s_def.name} 已激活，请按技能要求输出。"

        return tool_name, _tool_fn, description, parameters

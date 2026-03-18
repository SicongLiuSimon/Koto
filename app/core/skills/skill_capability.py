# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   Koto  ─  SkillCapabilityRegistry（技能能力注册中心）           ║
╚══════════════════════════════════════════════════════════════════╝

原有 Skill 只是 prompt 片段 → 通过本模块让 Skill 具备真实的可执行能力。

两层能力绑定方式
──────────────
1. 代码显式注册  SkillCapabilityRegistry.register(skill_id, callable)
2. entry_point 延迟加载  SkillDefinition.entry_point = "module:function"

调用约定（统一签名）
────────────────────
    fn(user_input: str, context: Dict[str, Any]) -> Any

context 常见 key：
    file_path    : 目标文件路径（文档类 Skill 必需）
    session_id   : 当前会话 ID
    model_id     : 指定模型（若 Skill 需要 LLM）
    skill_id     : 当前 Skill ID（自动注入）
    extra        : 其他透传参数 dict

典型用法
────────
    from app.core.skills.skill_capability import SkillCapabilityRegistry

    # 1. 检查 skill 有没有真实实现
    if SkillCapabilityRegistry.has_capability("annotate_academic"):
        result = SkillCapabilityRegistry.dispatch(
            "annotate_academic",
            user_input="对这篇论文进行学术批注",
            context={"file_path": "/path/to/doc.docx"}
        )

    # 2. 获取 skill 规划模板
    steps = SkillCapabilityRegistry.get_plan_template("annotate_academic")
    if steps:
        plan = TaskPlanner().plan_from_skill("annotate_academic", task_id, user_input)
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class SkillCapabilityRegistry:
    """
    Skill → Python callable 的注册表。

    支持两种注册方式：
    - 代码注册：register(skill_id, fn)
    - entry_point 延迟导入：从 SkillDefinition.entry_point 字符串动态 import
    """

    # class-level 注册表（进程级单例）
    _registry: Dict[str, Callable] = {}

    # ── 注册 API ──────────────────────────────────────────────────────────────

    @classmethod
    def register(cls, skill_id: str, fn: Callable) -> None:
        """
        显式注册一个 skill_id → callable 映射。

        Args:
            skill_id: Skill 的唯一 ID。
            fn: 符合 fn(user_input, context) 签名的可调用对象。
        """
        cls._registry[skill_id] = fn
        logger.debug("[SkillCapabilityRegistry] register: %s → %s", skill_id, getattr(fn, "__name__", fn))

    @classmethod
    def unregister(cls, skill_id: str) -> bool:
        """注销一个已注册的 skill callable。返回是否成功找到并删除。"""
        if skill_id in cls._registry:
            del cls._registry[skill_id]
            logger.debug("[SkillCapabilityRegistry] unregister: %s", skill_id)
            return True
        return False

    # ── 查询 API ──────────────────────────────────────────────────────────────

    @classmethod
    def has_capability(cls, skill_id: str) -> bool:
        """
        判断此 Skill 是否有可调用实现（代码注册 或 entry_point 字段非空）。
        不会实际加载模块，仅做存在性检测。
        """
        if skill_id in cls._registry:
            return True
        try:
            from app.core.skills.skill_manager import SkillManager
            skill = SkillManager.get_definition(skill_id)
            return bool(skill and skill.entry_point)
        except Exception:
            return False

    @classmethod
    def get_plan_template(cls, skill_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        获取 Skill 的规划模板（plan_template 字段）。
        若无则返回 None，调用方应降级到 LLM 动态规划。
        """
        try:
            from app.core.skills.skill_manager import SkillManager
            skill = SkillManager.get_definition(skill_id)
            if skill and skill.plan_template:
                return skill.plan_template
        except Exception as e:
            logger.debug("[SkillCapabilityRegistry] get_plan_template(%s) error: %s", skill_id, e)
        return None

    @classmethod
    def get_executor_tools(cls, skill_id: str) -> Optional[List[str]]:
        """
        获取 Skill 的执行层工具约束（executor_tools 字段）。
        若为空列表或 None，表示不限制工具。
        """
        try:
            from app.core.skills.skill_manager import SkillManager
            skill = SkillManager.get_definition(skill_id)
            if skill and skill.executor_tools:
                return skill.executor_tools
        except Exception as e:
            logger.debug("[SkillCapabilityRegistry] get_executor_tools(%s) error: %s", skill_id, e)
        return None

    # ── 调用 API ──────────────────────────────────────────────────────────────

    @classmethod
    def dispatch(
        cls,
        skill_id: str,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        调用 Skill 的实现。

        优先级：
        1. 代码注册的 callable（register() 注册）
        2. SkillDefinition.entry_point 动态导入的函数

        Args:
            skill_id  : Skill ID
            user_input: 用户原始输入（或本步骤指令）
            context   : 额外上下文，如 {"file_path": "...", "session_id": "..."}

        Returns:
            callable 的返回值（类型由具体实现决定）

        Raises:
            KeyError       : Skill 无任何可调用实现
            ImportError    : entry_point 模块不可导入
            TypeError      : entry_point 目标不可调用
        """
        ctx = dict(context or {})
        ctx.setdefault("skill_id", skill_id)

        # 1. 代码注册表
        if skill_id in cls._registry:
            logger.debug("[SkillCapabilityRegistry] dispatch via registry: %s", skill_id)
            return cls._registry[skill_id](user_input=user_input, context=ctx)

        # 2. entry_point 延迟加载
        try:
            from app.core.skills.skill_manager import SkillManager
            skill = SkillManager.get_definition(skill_id)
        except Exception as e:
            raise RuntimeError(f"无法加载 Skill '{skill_id}' 的定义: {e}") from e

        if not skill or not skill.entry_point:
            raise KeyError(
                f"Skill '{skill_id}' 没有可调用实现（未注册 capability，且 entry_point 为空）"
            )

        fn = cls._load_entry_point(skill.entry_point)
        logger.debug(
            "[SkillCapabilityRegistry] dispatch via entry_point: %s → %s",
            skill_id, skill.entry_point,
        )
        return fn(user_input=user_input, context=ctx)

    # ── 内部工具 ─────────────────────────────────────────────────────────────

    @classmethod
    def _load_entry_point(cls, entry_point: str) -> Callable:
        """
        动态加载 entry_point 字符串指定的函数。

        格式：
          'module.path:function_name'
          'module.path:ClassName.method_name'  （不常用，仅静态/类方法）
        """
        if ":" not in entry_point:
            raise ValueError(
                f"entry_point 格式错误，应为 'module:function'，实际: {entry_point!r}"
            )

        module_path, attr_path = entry_point.split(":", 1)

        _ALLOWED_MODULE_PREFIXES = ("app.", "web.", "src.")
        if not any(module_path.startswith(p) for p in _ALLOWED_MODULE_PREFIXES):
            raise ImportError(
                f"模块 '{module_path}' 不在允许的模块前缀列表中"
            )

        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(
                f"无法导入 entry_point 模块 '{module_path}': {e}"
            ) from e

        obj = mod
        for attr in attr_path.split("."):
            try:
                obj = getattr(obj, attr)
            except AttributeError as e:
                raise AttributeError(
                    f"entry_point 路径 '{entry_point}' 中属性 '{attr}' 不存在: {e}"
                ) from e

        if not callable(obj):
            raise TypeError(f"entry_point '{entry_point}' 解析到的对象不可调用: {type(obj)}")

        return obj

    # ── 调试 / 管理 ──────────────────────────────────────────────────────────

    @classmethod
    def list_registered(cls) -> List[str]:
        """列出所有通过代码注册的 skill_id。"""
        return list(cls._registry.keys())

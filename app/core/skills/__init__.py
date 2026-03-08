# -*- coding: utf-8 -*-
"""
Koto Skills System v2
可插拔的 Prompt 技能模块。
现在每个 Skill 由原子化 SkillDefinition Schema 描述，支持 MCP 导出、IO 变量、输出验收。
"""
from .skill_manager import SkillManager
from .skill_schema import (
    SkillDefinition,
    SkillCategory,
    InputVariable,
    VariableType,
    OutputSpec,
    OutputFormat,
    make_simple_skill,
)
from .skill_recorder import SkillRecorder

__all__ = [
    "SkillManager",
    "SkillDefinition",
    "SkillCategory",
    "InputVariable",
    "VariableType",
    "OutputSpec",
    "OutputFormat",
    "make_simple_skill",
    "SkillRecorder",
]

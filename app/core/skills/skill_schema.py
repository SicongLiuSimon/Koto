# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║          Koto  ─  Skill 原子化标准定义（MCP 兼容）               ║
╚══════════════════════════════════════════════════════════════════╝

设计原则
────────
1. 兼容 Anthropic MCP (Model Context Protocol) 工具描述结构
   - SkillDefinition.to_mcp_tool() 可直接输出符合 MCP 规范的 Tool 描述对象，
     供任何支持 MCP 的客户端（Claude Desktop / Continue / 自定义 host）调用。

2. 严格的变量约定
   - InputVariable  : 每个可注入的参数有类型、描述、是否必填
   - OutputSpec     : 明确告知验收层期望的输出格式，用于 Verification Layer

3. 工具绑定
   - bound_tools    : 记录本 Skill 执行时可以（或必须）调用的内部工具名列表，
     供 ToolRegistry 按需加载，避免无关工具增加 context 窗口开销

4. 版本语义
   - version        : "MAJOR.MINOR.PATCH"，MAJOR 变更意味着 IO Schema 破坏性修改

用法示例
────────
    from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec

    skill = SkillDefinition(
        id="summarize_doc",
        name="文档摘要",
        icon="📄",
        category="domain",
        description="对用户上传的文档生成结构化摘要",
        intent_description="用户想要总结/摘要/提炼某段文本或文档的核心内容",
        system_prompt_template=(
            "你是专业的文档摘要助手。请对以下内容生成摘要：\n\n{document}\n\n"
            "摘要长度约 {max_length} 字，输出格式：Markdown 二级标题 + 要点列表。"
        ),
        input_variables=[
            InputVariable(name="document",   type="string", description="待摘要的原始文本", required=True),
            InputVariable(name="max_length",  type="integer", description="摘要目标字数", required=False, default=300),
        ],
        output_spec=OutputSpec(
            format="markdown",
            must_contain=["##"],
            description="Markdown 格式，包含至少一个二级标题和要点列表"
        ),
        bound_tools=[],
        task_types=["CHAT", "RESEARCH", "DOC_ANNOTATE"],
    )

    # 导出为 MCP Tool 描述
    mcp_tool = skill.to_mcp_tool()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# 枚举类型
# ══════════════════════════════════════════════════════════════════


class SkillCategory(str, Enum):
    BEHAVIOR = "behavior"  # 改变 AI 回答行为（步骤化、严谨模式等）
    STYLE = "style"  # 改变输出风格（文风、emoji 等）
    DOMAIN = "domain"  # 专业领域能力（代码、法律、金融等）
    WORKFLOW = "workflow"  # 复合型任务流（多步骤、多工具编排）
    MEMORY = "memory"  # 记忆增强：跨会话长期记忆注入
    CUSTOM = "custom"  # 用户自定义录制的技能


class SkillNature(str, Enum):
    """
    Skill 的本质类型，用于区分「模型原生能力提示」与「真实领域技能」。

    model_hint   : 通过 prompt 激活模型自身已有的能力，属于输出行为/风格调整。
                   例如步骤化输出、精简模式、严谨模式、创意写作风格等。
                   特征：关掉这个 Skill 模型也能做到，只是不那么刻意。

    domain_skill : 提供模型通常不会主动套用的专有框架、模板或领域规则。
                   例如学术批注、合同审阅、调试分析、邮件撰写规范等。
                   特征：Skill 注入的是特定领域的专业知识，而不只是行为偏好。

    system       : 系统级功能（记忆、工具调用等），非对话内容生成类。
    """

    MODEL_HINT = "model_hint"  # 模型原生能力激活/调整
    DOMAIN_SKILL = "domain_skill"  # 真实领域专项技能
    SYSTEM = "system"  # 系统功能


class VariableType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    PLAIN = "plain"
    JSON = "json"
    TABLE = "table"  # Markdown 表格
    CODE = "code"  # 代码块
    ANY = "any"


# ══════════════════════════════════════════════════════════════════
# 子结构
# ══════════════════════════════════════════════════════════════════


@dataclass
class InputVariable:
    """
    Skill 的一个输入参数定义。
    对应 MCP Tool 的 inputSchema.properties 中的一个字段。
    """

    name: str
    type: VariableType | str = VariableType.STRING
    description: str = ""
    required: bool = True
    default: Any = None
    example: Optional[str] = None  # 示例值（用于文档和训练）
    # JSON Schema 附加约束（可选）
    enum: Optional[List[Any]] = None  # 枚举约束
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    def to_json_schema_property(self) -> Dict[str, Any]:
        """生成 JSON Schema 格式的属性描述（用于 MCP inputSchema）"""
        prop: Dict[str, Any] = {
            "type": (
                str(self.type.value)
                if isinstance(self.type, VariableType)
                else self.type
            ),
            "description": self.description,
        }
        if self.enum:
            prop["enum"] = self.enum
        if self.default is not None:
            prop["default"] = self.default
        if self.min_length is not None:
            prop["minLength"] = self.min_length
        if self.max_length is not None:
            prop["maxLength"] = self.max_length
        if self.minimum is not None:
            prop["minimum"] = self.minimum
        if self.maximum is not None:
            prop["maximum"] = self.maximum
        return prop


@dataclass
class OutputSpec:
    """
    Skill 期望的输出格式约束，供 Verification Layer 验收使用。
    """

    format: OutputFormat | str = OutputFormat.ANY
    # 输出文本中必须包含的字符串列表（简单规则校验）
    must_contain: List[str] = field(default_factory=list)
    # 输出文本中不允许包含的字符串（防止泄露 prompt 等）
    must_not_contain: List[str] = field(default_factory=list)
    # 输出最小字符数
    min_chars: Optional[int] = None
    # 输出最大字符数
    max_chars: Optional[int] = None
    # 如果 format=json，期望的 JSON 字段列表
    required_json_keys: List[str] = field(default_factory=list)
    description: str = ""

    def validate(self, text: str) -> tuple[bool, str]:
        """
        验收云端返回文本是否符合本 Spec。
        返回 (passed: bool, reason: str)
        """
        if self.min_chars and len(text) < self.min_chars:
            reason = f"输出过短: {len(text)} < {self.min_chars} 字符"
            logger.debug("[OutputSpec] validate() failed: %s", reason)
            return False, reason
        if self.max_chars and len(text) > self.max_chars:
            reason = f"输出过长: {len(text)} > {self.max_chars} 字符"
            logger.debug("[OutputSpec] validate() failed: %s", reason)
            return False, reason

        for token in self.must_contain:
            if token not in text:
                reason = f"输出缺少必要内容: '{token}'"
                logger.debug("[OutputSpec] validate() failed: %s", reason)
                return False, reason

        for token in self.must_not_contain:
            if token in text:
                reason = f"输出包含禁止内容: '{token}'"
                logger.warning(
                    "[OutputSpec] validate() blocked forbidden content: %s", reason
                )
                return False, reason

        fmt = (
            self.format.value if isinstance(self.format, OutputFormat) else self.format
        )
        if fmt == "json" and self.required_json_keys:
            try:
                obj = json.loads(text)
                for key in self.required_json_keys:
                    if key not in obj:
                        reason = f"JSON 输出缺少字段: '{key}'"
                        logger.debug("[OutputSpec] validate() failed: %s", reason)
                        return False, reason
            except json.JSONDecodeError:
                reason = "期望 JSON 格式但输出不可解析"
                logger.debug("[OutputSpec] validate() failed: %s", reason)
                return False, reason

        if fmt == "table" and "|" not in text:
            reason = "期望 Markdown 表格但输出中无 '|' 符号"
            logger.debug("[OutputSpec] validate() failed: %s", reason)
            return False, reason

        return True, "OK"


# ══════════════════════════════════════════════════════════════════
# 核心：SkillDefinition
# ══════════════════════════════════════════════════════════════════


@dataclass
class SkillDefinition:
    """
    Koto Skill 的原子化完整定义。

    字段说明
    ────────
    id                    : 全局唯一标识符（snake_case），用于注册表 key
    name                  : 用户可见的中文名称
    icon                  : UI 展示用的 emoji
    category              : SkillCategory 枚举
    description           : 简短描述（显示在 Skill 列表 UI）
    intent_description    : [NEW] 一句话描述本 Skill 处理哪类意图，
                            供 Router（Qwen/Gemini）识别何时调用此 Skill
    system_prompt_template: [NEW] 含变量占位符的 System Prompt，
                            变量格式 {variable_name}，与 input_variables 对应
    input_variables       : [NEW] 可注入的参数列表
    output_spec           : [NEW] 期望输出格式，供 Verification Layer 使用
    bound_tools           : [NEW] 本 Skill 执行时绑定的工具名列表
    task_types            : 生效的任务分类列表；空列表 = 所有类型
    enabled               : 是否启用（用户可覆盖）
    priority              : 注入优先级（0-100），高优先级 Skill 先注入到 System Prompt
    conflict_with         : 与此 Skill 互斥的 Skill ID 列表（声明式冲突申明）
    examples              : 示例输入/输出对列表，格式 [{"input": str, "output": str, "note": str}]
    version               : 语义版本号
    author                : 作者标识，"builtin" 表示内置
    created_at            : ISO8601 创建时间（用于 Marketplace 展示）
    tags                  : 搜索标签

    向后兼容字段（保留，供旧 SkillManager 读取）
    ─────────────────────────────────────────────
    prompt                : 渲染好的 prompt 片段（无变量），直接注入 system_instruction。
                            新代码应优先使用 system_prompt_template + render_prompt()。
    """

    # ── 必填核心字段 ─────────────────────────────────────────────────────────
    id: str
    name: str
    icon: str
    category: SkillCategory | str
    description: str

    # ── 技能本质类型 ──────────────────────────────────────────────────────────
    # 区分「模型原生能力提示」与「真实领域技能」，用于 UI 分组和用户认知
    skill_nature: SkillNature | str = SkillNature.DOMAIN_SKILL

    # ── 新增：意图 & Prompt 模板 ─────────────────────────────────────────────
    intent_description: str = ""
    system_prompt_template: str = ""

    # ── 新增：IO 约束 ─────────────────────────────────────────────────────────
    input_variables: List[InputVariable] = field(default_factory=list)
    output_spec: OutputSpec = field(default_factory=OutputSpec)

    # ── 新增：工具绑定 ───────────────────────────────────────────────────────
    bound_tools: List[str] = field(default_factory=list)

    # ── 执行层增强（自定义 Skill 由 SkillRecorder LLM 分析自动填充）──────────
    # 执行时建议调用的内部工具名列表，供 UnifiedAgent 执行层参考
    executor_tools: List[str] = field(default_factory=list)
    # 有序执行步骤描述，供 inject_into_prompt 注入给模型
    plan_template: List[str] = field(default_factory=list)
    # AutoMatcher 触发关键词，用于 SkillAutoMatcher._PATTERN_MAP 注册
    trigger_keywords: List[str] = field(default_factory=list)

    # ── 原有字段（保留向后兼容）─────────────────────────────────────────────
    task_types: List[str] = field(default_factory=list)
    enabled: bool = False
    prompt: str = ""  # 旧版直接注入片段，新版用 render_prompt() 替代

    # ── 元数据 ───────────────────────────────────────────────────────────────
    version: str = "1.0.0"
    author: str = "builtin"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    tags: List[str] = field(default_factory=list)

    # ── 增强字段 ─────────────────────────────────────────────────────────────
    # 注入优先级（0-100）：高数值先注入到 System Prompt，
    # 让关键 Skill 的指令更靠近 Prompt 开头从而更受重视
    priority: int = 50

    # 声明与此 Skill 逻辑互斥的其他 Skill ID
    # SkillManager.detect_conflicts() 会合并此字段与内置规则表
    conflict_with: List[str] = field(default_factory=list)

    # 示例 I/O 对，供 UI 展示和测试
    # 格式: [{"input": "...", "output": "...", "note": "...（可选）"}, ...]
    examples: List[Dict[str, Any]] = field(default_factory=list)

    # ── Manifest v2 字段 ─────────────────────────────────────────────────────
    # 兼容性约束: {"min_koto_version": "1.0.0", "platform": "windows"}
    compatibility: Dict[str, Any] = field(default_factory=dict)

    # 依赖的其他 Skill ID 列表（安装此 Skill 时需要先安装依赖）
    dependencies: List[str] = field(default_factory=list)

    # 需要的权限列表，如 "file_read", "network", "process", "clipboard"
    permissions: List[str] = field(default_factory=list)

    # 更新检测 URL（留空 = 仅本地，不检查更新）
    update_url: str = ""

    # 发布者信息（比 author 更正式，可包含组织名）
    publisher: str = ""

    # Skill 的默认触发器配置（可由 SkillBindingManager 自动注册）
    # 格式: [{"trigger_type": "cron", "config": {"time": "09:00"}}]
    default_triggers: List[Dict[str, Any]] = field(default_factory=list)

    # ── 两层能力架构（v2）──────────────────────────────────────────────────────
    # 规划层：TaskPlanner 用此模板替代 LLM 规划，生成确定性步骤 DAG。
    # 每步格式：
    #   name           : 步骤唯一名称
    #   description    : 用户可见描述
    #   step_type      : "llm" | "file" | "tool" | "skill"
    #   depends_on     : 依赖步骤名列表
    #   executor_tools : 该步骤允许调用的工具子集（覆盖 skill 级别的 executor_tools）
    #   executor_prompt: 该步骤附加的 system_prompt 片段
    #   expected_output: 期望输出描述（用于验收）
    #   input_keys     : 从上游步骤结果中取哪些字段注入到本步骤
    plan_template: List[Dict[str, Any]] = field(default_factory=list)

    # 执行层：若非空，UnifiedAgent 只向 LLM 暴露此子集工具。
    # 避免无关工具污染 context，让模型精确使用绑定的工具集。
    # 空列表 = 不限制（使用 ToolRouter 默认策略）。
    executor_tools: List[str] = field(default_factory=list)

    # 执行层：Python 实现入口点，格式 "module.path:function_name"。
    # 设置后，SkillCapabilityRegistry 可将其加载为可调用能力。
    # 函数签名约定：fn(user_input: str, context: Dict[str, Any]) → Any
    entry_point: Optional[str] = None

    # ── 方法 ─────────────────────────────────────────────────────────────────


    def render_prompt(
        self,
        variables: Optional[Dict[str, Any]] = None,
        with_examples: bool = False,
        with_output_spec: bool = False,
    ) -> str:
        """
        渲染 Skill 的完整 system prompt 片段。

        合成顺序（均可单独开关）：
          1. system_prompt_template（含变量占位符）或旧版 prompt 字段
          2. [可选] output_spec.description → 告知模型期望输出格式
          3. [可选] examples → few-shot 示例对，极大提升小白创建的 Skill 质量

        Args:
            variables:        变量名 → 值 字典，用于替换 {variable} 占位符
            with_examples:    True 时追加 few-shot 示例块（"用法示例"）
            with_output_spec: True 时追加 output_spec.description 格式说明

        Returns:
            完整渲染后的 prompt 字符串
        """
        # 1. 主体 prompt
        template = self.system_prompt_template or self.prompt
        if variables:
            try:
                template = template.format(**variables)
            except KeyError as e:
                logger.warning("[SkillDefinition] render_prompt() missing variable %s for skill=%s", e, self.id)

        parts = [template] if template else []

        # 2. 输出格式规格（output_spec.description）
        if with_output_spec:
            fmt_desc = (
                self.output_spec.description
                if isinstance(self.output_spec, OutputSpec)
                else ""
            )
            if fmt_desc:
                parts.append(f"\n\n### 📋 输出格式要求\n{fmt_desc}")

        # 3. Few-shot 示例块
        if with_examples and self.examples:
            lines = ["\n\n### 💡 用法示例（请参照格式）"]
            for i, ex in enumerate(self.examples, 1):
                inp = ex.get("input", "").strip()
                out = ex.get("output", "").strip()
                note = ex.get("note", "").strip()
                if not inp and not out:
                    continue
                lines.append(f"\n**示例 {i}**{f'（{note}）' if note else ''}")
                if inp:
                    lines.append(f"用户输入：{inp}")
                if out:
                    lines.append(f"期望输出：\n{out}")
            if len(lines) > 1:  # 有实际示例内容才追加
                parts.append("\n".join(lines))

        return "".join(parts)

    def to_mcp_tool(self) -> Dict[str, Any]:
        """
        导出为 MCP (Model Context Protocol) 兼容的 Tool 描述对象。

        MCP Tool 结构：
        {
          "name": str,
          "description": str,
          "inputSchema": {
            "type": "object",
            "properties": { ... },
            "required": [...]
          }
        }

        参考: https://spec.modelcontextprotocol.io/specification/server/tools/
        """
        logger.debug(
            "[SkillDefinition] to_mcp_tool() skill=%s input_variables=%d",
            self.id,
            len(self.input_variables),
        )
        properties: Dict[str, Any] = {}
        required_fields: List[str] = []

        for var in self.input_variables:
            properties[var.name] = var.to_json_schema_property()
            if var.required:
                required_fields.append(var.name)

        tool_description = f"{self.description}"
        if self.intent_description:
            tool_description += f"\n\n适用场景：{self.intent_description}"

        return {
            "name": self.id,
            "description": tool_description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                **({"required": required_fields} if required_fields else {}),
            },
            # 扩展字段（非标准 MCP，但有助于 Koto 内部路由）
            "_koto_meta": {
                "task_types": self.task_types,
                "bound_tools": self.bound_tools,
                "output_format": (
                    self.output_spec.format.value
                    if isinstance(self.output_spec.format, OutputFormat)
                    else self.output_spec.format
                ),
                "version": self.version,
                "author": self.author,
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        """序列化为普通 dict（用于 JSON 持久化和 API 响应）"""
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "category": (
                self.category.value
                if isinstance(self.category, SkillCategory)
                else self.category
            ),
            "skill_nature": (
                self.skill_nature.value
                if isinstance(self.skill_nature, SkillNature)
                else self.skill_nature
            ),
            "description": self.description,
            "intent_description": self.intent_description,
            "system_prompt_template": self.system_prompt_template,
            "input_variables": [
                {
                    "name": v.name,
                    "type": (
                        v.type.value if isinstance(v.type, VariableType) else v.type
                    ),
                    "description": v.description,
                    "required": v.required,
                    "default": v.default,
                    "enum": v.enum,
                }
                for v in self.input_variables
            ],
            "output_spec": {
                "format": (
                    self.output_spec.format.value
                    if isinstance(self.output_spec.format, OutputFormat)
                    else self.output_spec.format
                ),
                "must_contain": self.output_spec.must_contain,
                "must_not_contain": self.output_spec.must_not_contain,
                "min_chars": self.output_spec.min_chars,
                "max_chars": self.output_spec.max_chars,
                "required_json_keys": self.output_spec.required_json_keys,
                "description": self.output_spec.description,
            },
            "bound_tools": self.bound_tools,
            "executor_tools": self.executor_tools,
            "plan_template": self.plan_template,
            "trigger_keywords": self.trigger_keywords,
            "task_types": self.task_types,
            "enabled": self.enabled,
            "prompt": self.prompt,
            "version": self.version,
            "author": self.author,
            "created_at": self.created_at,
            "tags": self.tags,
            "priority": self.priority,
            "conflict_with": self.conflict_with,
            "examples": self.examples,
            # manifest v2
            "compatibility": self.compatibility,
            "dependencies": self.dependencies,
            "permissions": self.permissions,
            "update_url": self.update_url,
            "publisher": self.publisher or self.author,
            "default_triggers": self.default_triggers,
            # v2 两层能力架构
            "plan_template": self.plan_template,
            "executor_tools": self.executor_tools,
            "entry_point": self.entry_point,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillDefinition":
        """从 dict 反序列化（用于从 JSON 文件加载自定义 Skill）"""
        logger.debug("[SkillDefinition] from_dict() id=%s", data.get("id", "?"))
        input_variables = [
            InputVariable(
                name=v["name"],
                type=v.get("type", "string"),
                description=v.get("description", ""),
                required=v.get("required", True),
                default=v.get("default"),
                enum=v.get("enum"),
            )
            for v in data.get("input_variables", [])
        ]

        raw_spec = data.get("output_spec", {})
        output_spec = OutputSpec(
            format=raw_spec.get("format", "any"),
            must_contain=raw_spec.get("must_contain", []),
            must_not_contain=raw_spec.get("must_not_contain", []),
            min_chars=raw_spec.get("min_chars"),
            max_chars=raw_spec.get("max_chars"),
            required_json_keys=raw_spec.get("required_json_keys", []),
            description=raw_spec.get("description", ""),
        )

        return cls(
            id=data["id"],
            name=data["name"],
            icon=data.get("icon", "🔧"),
            category=data.get("category", SkillCategory.CUSTOM),
            skill_nature=data.get("skill_nature", SkillNature.DOMAIN_SKILL),
            description=data.get("description", ""),
            intent_description=data.get("intent_description", ""),
            system_prompt_template=data.get("system_prompt_template", ""),
            input_variables=input_variables,
            output_spec=output_spec,
            bound_tools=data.get("bound_tools", []),
            executor_tools=data.get("executor_tools", []),
            trigger_keywords=data.get("trigger_keywords", []),
            task_types=data.get("task_types", []),
            enabled=data.get("enabled", False),
            prompt=data.get("prompt", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "user"),
            created_at=data.get("created_at", ""),
            tags=data.get("tags", []),
            priority=data.get("priority", 50),
            conflict_with=data.get("conflict_with", []),
            examples=data.get("examples", []),
            # manifest v2
            compatibility=data.get("compatibility", {}),
            dependencies=data.get("dependencies", []),
            permissions=data.get("permissions", []),
            update_url=data.get("update_url", ""),
            publisher=data.get("publisher", data.get("author", "user")),
            default_triggers=data.get("default_triggers", []),
            plan_template=data.get("plan_template", []),
            entry_point=data.get("entry_point"),
        )

    @classmethod
    def from_legacy_dict(cls, legacy: Dict[str, Any]) -> "SkillDefinition":
        """
        从旧版 BUILTIN_SKILLS dict 格式升级为 SkillDefinition。
        旧格式字段: id, name, icon, category, description, task_types, prompt, enabled
        新增字段（可选）: conflict_with, priority, examples
        """
        return cls(
            id=legacy["id"],
            name=legacy["name"],
            icon=legacy.get("icon", "🔧"),
            category=legacy.get("category", SkillCategory.BEHAVIOR),
            skill_nature=legacy.get("skill_nature", SkillNature.DOMAIN_SKILL),
            description=legacy.get("description", ""),
            intent_description=legacy.get("intent_description", ""),
            system_prompt_template=legacy.get("system_prompt_template", ""),
            input_variables=[],
            output_spec=OutputSpec(),
            bound_tools=legacy.get("bound_tools", []),
            executor_tools=legacy.get("executor_tools", []),
            plan_template=legacy.get("plan_template", []),
            trigger_keywords=legacy.get("trigger_keywords", []),
            task_types=legacy.get("task_types", []),
            enabled=legacy.get("enabled", False),
            prompt=legacy.get(
                "prompt", ""
            ),  # 保留旧版 prompt，render_prompt() 会降级使用
            version="1.0.0",
            author="builtin",
            tags=[legacy.get("category", "")],
            priority=legacy.get("priority", 50),
            conflict_with=legacy.get("conflict_with", []),
            examples=legacy.get("examples", []),
            entry_point=legacy.get("entry_point"),
        )


# ══════════════════════════════════════════════════════════════════
# 便捷工厂函数
# ══════════════════════════════════════════════════════════════════


def make_simple_skill(
    id: str,
    name: str,
    icon: str,
    category: str,
    description: str,
    prompt: str,
    task_types: Optional[List[str]] = None,
    intent_description: str = "",
    enabled: bool = False,
) -> SkillDefinition:
    """
    快速创建一个简单 Skill（无变量、无复杂 IO 约束）。
    兼容旧版 BUILTIN_SKILLS 的创建方式。
    """
    return SkillDefinition(
        id=id,
        name=name,
        icon=icon,
        category=category,
        description=description,
        intent_description=intent_description,
        system_prompt_template=prompt,
        prompt=prompt,
        task_types=task_types or [],
        enabled=enabled,
    )

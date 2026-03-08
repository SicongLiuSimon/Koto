# -*- coding: utf-8 -*-
"""
🎯 Koto Skills Manager（v2 — 原子化 Schema 升级）

可插拔的 Prompt 技能系统。
每个 Skill 现在由 SkillDefinition（原子化标准 Schema）描述，
支持 MCP Tool 导出、IO 变量约束、输出验收规格。

向后兼容：旧版 dict 格式的 BUILTIN_SKILLS 会自动升级为 SkillDefinition，
原有的 inject_into_prompt / list_skills / set_enabled 等 API 不变。

新增 API:
    SkillManager.get_definition(skill_id)   → SkillDefinition
    SkillManager.list_mcp_tools()           → List[MCP Tool dict]
    SkillManager.register_custom(skill_def) → 注册自定义 Skill
    SkillManager.load_custom_skills_dir()   → 从 config/skills/ 目录加载 JSON Skill 文件

用法:
    from app.core.skills.skill_manager import SkillManager

    enhanced = SkillManager.inject_into_prompt(base_instruction, task_type="CHAT")
    skills   = SkillManager.list_skills()
    SkillManager.set_enabled("concise_mode", True)

    # 新：导出 MCP 工具列表
    mcp_tools = SkillManager.list_mcp_tools()
"""

import os
import json
import logging
from typing import Optional, List, Dict
from pathlib import Path

from app.core.skills.skill_schema import SkillDefinition, OutputSpec

logger = logging.getLogger(__name__)

# ── 内置技能定义 ────────────────────────────────────────────────────────────────
# 每个技能字段说明：
#   id           : 唯一标识符（snake_case）
#   name         : 中文名称
#   icon         : emoji 图标
#   category     : 分类 behavior | style | domain
#   description  : 简短描述（显示在 UI）
#   task_types   : 生效的任务类型列表；空列表 = 所有任务类型
#   prompt       : 注入到 system_instruction 的文本片段
#   enabled      : 默认启用状态
# ───────────────────────────────────────────────────────────────────────────────
BUILTIN_SKILLS: List[Dict] = [

    # ── 行为类 ────────────────────────────────────────────────────────────────
    {
        "id": "step_by_step",
        "name": "步骤化输出",
        "icon": "🪜",
        "category": "behavior",
        "description": "将回答拆解为带编号的清晰步骤，特别适合教程、流程、操作类问题",
        "task_types": [],
        "prompt": (
            "\n\n## 🪜 行为要求：步骤化输出\n"
            "- 对于任何有先后顺序的内容，使用「步骤 1 / 步骤 2 …」格式\n"
            "- 每步包含：**目的** + **具体操作** + **预期结果**\n"
            "- 复杂流程用子步骤缩进展开\n"
            "- 最后给出「✅ 完成标志」，帮助用户确认执行成功"
        ),
        "enabled": False,
    },
    {
        "id": "strict_mode",
        "name": "严谨模式",
        "icon": "🔬",
        "category": "behavior",
        "description": "要求更严格的推理：引用来源、标明不确定性、避免模糊结论",
        "task_types": ["CHAT", "RESEARCH"],
        "prompt": (
            "\n\n## 🔬 行为要求：严谨模式\n"
            "- 区分「已知事实」和「推测/估计」，后者用「可能/据报道」等标注\n"
            "- 涉及时间敏感内容时，提醒信息可能已过时\n"
            "- 避免过度自信：不确定时明确说「我不确定」\n"
            "- 给出多角度分析时，注明前提假设是什么"
        ),
        "enabled": False,
    },
    {
        "id": "concise_mode",
        "name": "精简模式",
        "icon": "⚡",
        "category": "behavior",
        "description": "强制简短回答：只给核心结论和最关键步骤，适合快速查询场景",
        "intent_description": "用户需要简短、直接的回答，例如快速查询、简单事实确认",
        "task_types": ["CHAT"],
        "conflict_with": ["step_by_step", "teaching_mode", "proactive_suggestions"],
        "priority": 90,  # 最高优先级，覆盖其他行为
        "prompt": (
            "\n\n## ⚡ 行为要求：精简模式（最高优先级）\n"
            "- 回答控制在 150 字以内\n"
            "- 只给出「结论 + 最小可行步骤」，省略背景和解释\n"
            "- 禁止使用「首先…其次…最后…」等冗长过渡语\n"
            "- 若用户明确要求详细解释，则忽略此限制"
        ),
        "enabled": False,
    },
    {
        "id": "teaching_mode",
        "name": "教学模式",
        "icon": "🎓",
        "category": "behavior",
        "description": "苏格拉底式引导 + 类比 + 逐层展开，帮助用户真正理解而非死记答案",
        "task_types": ["CHAT", "RESEARCH"],
        "prompt": (
            "\n\n## 🎓 行为要求：教学模式\n"
            "- 用「类比」解释抽象概念（「就像…」「可以把它理解为…」）\n"
            "- 从基础前提出发，逐层深入，不跳过中间推导\n"
            "- 在适当节点加入「💡 为什么这样做？」的思考引导\n"
            "- 结尾附「📝 关键要点」小结，帮助用户归纳记忆"
        ),
        "enabled": False,
    },
    {
        "id": "proactive_suggestions",
        "name": "主动建议",
        "icon": "💡",
        "category": "behavior",
        "description": "回答后主动给出相关的延伸建议、潜在问题或下一步操作",
        "task_types": [],
        "prompt": (
            "\n\n## 💡 行为要求：主动建议\n"
            "- 完成主要回答后，用「💡 延伸建议」段落提出 1-3 个相关建议\n"
            "- 如果检测到用户可能遇到的陷阱，用「⚠️ 注意」提前提示\n"
            "- 适时建议下一步操作：「您可能还想要…」"
        ),
        "enabled": False,
    },

    # ── 风格类 ────────────────────────────────────────────────────────────────
    {
        "id": "professional_tone",
        "name": "专业语气",
        "icon": "👔",
        "category": "style",
        "description": "使用正式的商务/学术语气，适合需要对外输出的报告、邮件、文档",
        "task_types": ["CHAT", "FILE_GEN", "RESEARCH"],
        "prompt": (
            "\n\n## 👔 风格要求：专业语气\n"
            "- 使用正式书面语，避免口语化表达和网络用语\n"
            "- 称谓使用「您」；指代用户需求时用「贵方/您的需求」\n"
            "- 数字和单位规范书写（如「3 个」而非「三个」）\n"
            "- 结构清晰：每段只表达一个主要观点"
        ),
        "enabled": False,
    },
    {
        "id": "creative_writing",
        "name": "创意写作",
        "icon": "✨",
        "category": "style",
        "description": "使用生动、富有感染力的语言，适合文案、故事、创意内容生成",
        "task_types": ["CHAT", "FILE_GEN"],
        "prompt": (
            "\n\n## ✨ 风格要求：创意写作\n"
            "- 使用具体、感官性的描写代替抽象表述（「金黄色的光」而非「美丽的光」）\n"
            "- 善用比喻、拟人、排比等修辞手法增加文字感染力\n"
            "- 句子长短交错，避免单调节奏\n"
            "- 鼓励独特角度和出人意料的表达"
        ),
        "enabled": False,
    },
    {
        "id": "emoji_assist",
        "name": "Emoji 辅助",
        "icon": "🎨",
        "category": "style",
        "description": "适当使用 emoji 增强视觉层次感和可读性",
        "task_types": [],
        "prompt": (
            "\n\n## 🎨 风格要求：Emoji 辅助\n"
            "- 各段标题/分类用 emoji 标注以增强视觉区分\n"
            "- 重要警告用 ⚠️，成功结果用 ✅，关键步骤用 👉\n"
            "- 全文 emoji 密度适中（每段 1-2 个），不要每句话都用"
        ),
        "enabled": False,
    },
    {
        "id": "bilingual",
        "name": "双语标注",
        "icon": "🌐",
        "category": "style",
        "description": "对专业术语和概念提供中英双语标注，适合学习技术内容",
        "task_types": ["CHAT", "RESEARCH", "CODER"],
        "prompt": (
            "\n\n## 🌐 风格要求：双语标注\n"
            "- 首次出现的专业术语在中文后括注英文原文，如「机器学习 (Machine Learning)」\n"
            "- 代码相关的概念保留英文名称\n"
            "- 不需要对每个普通词汇都标注，只针对技术和专有名词"
        ),
        "enabled": False,
    },

    # ── 领域类 ────────────────────────────────────────────────────────────────
    {
        "id": "code_best_practices",
        "name": "代码最佳实践",
        "icon": "🛡️",
        "category": "domain",
        "description": "编写代码时强调可读性、注释、异常处理和测试，提升代码质量",
        "task_types": ["CODER", "CHAT"],
        "prompt": (
            "\n\n## 🛡️ 领域要求：代码最佳实践\n"
            "- 每个函数/类必须有中文文档字符串（docstring）\n"
            "- 关键逻辑加行内注释，说明「为什么」而不仅是「做什么」\n"
            "- 对外部调用、文件IO、网络请求一律使用 try/except 包裹\n"
            "- 变量和函数命名使用 snake_case，类名使用 PascalCase\n"
            "- 输出代码后，额外说明「可能的改进」或「注意事项」"
        ),
        "enabled": False,
    },
    {
        "id": "security_aware",
        "name": "安全意识",
        "icon": "🔒",
        "category": "domain",
        "description": "在代码和系统操作建议中主动指出安全风险和防护措施",
        "task_types": ["CODER", "SYSTEM", "AGENT", "CHAT"],
        "prompt": (
            "\n\n## 🔒 领域要求：安全意识\n"
            "- 涉及文件删除/格式化/权限修改等高风险操作时，主动添加确认提示\n"
            "- 代码中如有硬编码密码/密钥，必须提示替换为环境变量\n"
            "- SQL 拼接、文件路径拼接处提醒注入风险\n"
            "- 网络请求建议始终校验 HTTPS 和证书"
        ),
        "enabled": False,
    },
    {
        "id": "data_analysis",
        "name": "数据分析视角",
        "icon": "📊",
        "category": "domain",
        "description": "回答时多从数据和量化角度出发，提供可验证的建议",
        "task_types": ["CHAT", "RESEARCH"],
        "prompt": (
            "\n\n## 📊 领域要求：数据分析视角\n"
            "- 尽量用数据和指标支撑观点，而不仅是定性描述\n"
            "- 建议可测量的方法：「通过监控 X 指标来判断」\n"
            "- 对比分析时使用表格格式呈现\n"
            "- 推荐 Python/Excel 等工具时，给出具体的实现思路"
        ),
        "enabled": False,
    },

    # ── 领域增强 ──────────────────────────────────────────────────────────────
    {
        "id": "writing_assistant",
        "name": "写作助手",
        "icon": "✍️",
        "category": "domain",
        "description": "提升文字表达质量：润色语言、优化段落结构、保持风格一致",
        "intent_description": "用户要写文章、报告、邮件、文案或需要修改润色文字时",
        "task_types": ["CHAT", "FILE_GEN"],
        "priority": 60,
        "prompt": (
            "\n\n## ✍️ 领域要求：写作助手\n"
            "- 每次输出前默默检查：逻辑是否连贯、表达是否准确、有无冗余\n"
            "- 段落开头避免「首先/其次/另外」等程式化连接词\n"
            "- 对同一意思给出至少一种更简练或更有力的替代表达\n"
            "- 指出可能的歧义或不清晰之处，建议具体改法\n"
            "- 保持用户原文的语气和风格，不做风格切换"
        ),
        "enabled": False,
    },
    {
        "id": "debate_mode",
        "name": "批判思维",
        "icon": "⚖️",
        "category": "behavior",
        "description": "主动挑战假设和结论，从多角度分析，识别逻辑漏洞和可能的反驳",
        "intent_description": "用户在思考复杂决策、评估方案优劣、或需要检验观点可靠性时",
        "task_types": ["CHAT", "RESEARCH"],
        "conflict_with": ["proactive_suggestions"],
        "priority": 65,
        "prompt": (
            "\n\n## ⚖️ 行为要求：批判思维\n"
            "- 分析问题时，主动列出「支持」和「反对」两侧论据\n"
            "- 识别并明确指出输入中存在的假设（显性和隐性）\n"
            "- 对结论标注置信度：确定/可能/存疑\n"
            "- 指出「还需要哪些信息才能做出更可靠的判断」\n"
            "- 避免立场倾斜：即使用户倾向某一侧，也要呈现完整对立面"
        ),
        "enabled": False,
    },
    {
        "id": "research_depth",
        "name": "深度研究",
        "icon": "🔭",
        "category": "behavior",
        "description": "提供有深度的研究性回答：多维度分析、原因链条、历史背景和未来影响",
        "intent_description": "用户在做研究、深入了解某个话题、或准备报告/论文内容时",
        "task_types": ["RESEARCH", "CHAT"],
        "conflict_with": ["concise_mode"],
        "priority": 55,
        "prompt": (
            "\n\n## 🔭 行为要求：深度研究\n"
            "- 先给出「结论摘要」，再分层展开论据\n"
            "- 追溯原因链：「A 导致 B，因为 C，背后是 D」\n"
            "- 主动给出历史背景或类似案例以增加参考维度\n"
            "- 区分「已有广泛共识」和「存在争议」的内容\n"
            "- 给出延伸阅读方向：「若想深入了解，可查阅…」"
        ),
        "enabled": False,
    },
    {
        "id": "task_planner",
        "name": "任务规划",
        "icon": "🗓️",
        "category": "workflow",
        "description": "将复杂目标分解为具体可执行的任务列表，评估优先级和依赖关系",
        "intent_description": "用户在规划项目、拆解复杂任务、制定行动计划或整理待办事项时",
        "task_types": ["CHAT", "FILE_GEN"],
        "priority": 60,
        "prompt": (
            "\n\n## 🗓️ 行为要求：任务规划\n"
            "- 将用户的目标分解为 3-7 个具体、可执行的子任务\n"
            "- 每个子任务标注：预计耗时 / 优先级（高/中/低）/ 前置依赖\n"
            "- 识别关键路径：哪些任务必须先完成\n"
            "- 用 Markdown 复选框格式（- [ ] 任务名）便于直接使用\n"
            "- 最后提醒可能的风险点和应对建议"
        ),
        "enabled": False,
    },

    # ── 专项文档批注技能 ──────────────────────────────────────────────────────
    {
        "id": "annotate_academic",
        "name": "学术批注",
        "icon": "📚",
        "category": "domain",
        "description": "对学术论文进行批注润色：修正语言、增强逻辑连贯性、符合期刊投稿规范",
        "intent_description": "用户需要对学术论文、研究报告进行语言润色、批注修改、投稿前精修",
        "task_types": ["DOC_ANNOTATE"],
        "priority": 70,
        "prompt": (
            "\n\n## 📚 领域要求：学术批注\n"
            "- 采用「原文 → 修改建议 → 修改理由」三段式逐段批注\n"
            "- 重点关注：措辞精确性、被动语态使用、逻辑连接词、数据引用格式（APA/GB/MLA）\n"
            "- 标注可能引发审稿人质疑的逻辑断层或过度主张\n"
            "- 区分「必须修改」（❌）和「建议优化」（⚠️）两个层级\n"
            "- 最终附「总体评估」：语言水平评分、逻辑严密性评分、是否符合期刊投稿规范"
        ),
        "enabled": False,
    },
    {
        "id": "annotate_business",
        "name": "商务批注",
        "icon": "💼",
        "category": "domain",
        "description": "对商业报告、合同、邮件进行专业批注：语气规范化、结构清晰化、歧义消除",
        "intent_description": "用户需要修改商务报告、合同文本、商务邮件或内部公文",
        "task_types": ["DOC_ANNOTATE", "FILE_GEN"],
        "priority": 65,
        "prompt": (
            "\n\n## 💼 领域要求：商务批注\n"
            "- 聚焦「语气是否专业」「结构是否清晰」「表达是否精准」三个维度\n"
            "- 标注过于口语化、产生歧义或可能引发误解的措辞，给出替代表达\n"
            "- 检查文档逻辑结构：开头目的是否明确、中间论据是否支撑结论、结尾行动项是否清晰\n"
            "- 对关键数据或承诺用「🔍 建议核实」标注，防止后期争议\n"
            "- 输出格式：批注表格（位置 | 原文 | 问题 | 建议） + 下方附修订版全文"
        ),
        "enabled": False,
    },
    {
        "id": "annotate_code_review",
        "name": "代码审查批注",
        "icon": "🔍",
        "category": "domain",
        "description": "对代码文件进行专业审查批注：安全性、可读性、最佳实践、潜在 bug 分级标注",
        "intent_description": "用户需要 code review、代码批注、检查代码质量、找出 bug",
        "task_types": ["DOC_ANNOTATE", "CODER"],
        "priority": 75,
        "conflict_with": ["code_best_practices"],
        "prompt": (
            "\n\n## 🔍 领域要求：代码审查批注\n"
            "- 按严重等级分类：🔴 严重（安全漏洞/逻辑错误）、🟡 警告（可读性差/违反规范）、🔵 建议（优化空间）\n"
            "- 必查项：硬编码敏感信息、SQL/命令注入风险、未处理异常、资源未关闭、边界条件缺失\n"
            "- 每条批注格式：「第 N 行 / 函数名 → [等级] 问题描述 → 建议修改方式」\n"
            "- 识别重复代码块，标注「建议抽取为工具函数」\n"
            "- 最终给出「整体代码质量评分」(1-10) 和「Top 3 优先修复项」"
        ),
        "enabled": False,
    },
    {
        "id": "annotate_translation",
        "name": "翻译质检批注",
        "icon": "🌐",
        "category": "domain",
        "description": "对翻译文本进行质检批注：忠实度、流畅性、术语一致性对照审查",
        "intent_description": "用户需要检查翻译质量、对照原文批注、纠正译文错误或术语不一致",
        "task_types": ["DOC_ANNOTATE"],
        "priority": 70,
        "prompt": (
            "\n\n## 🌐 领域要求：翻译质检批注\n"
            "- 采用「原文 | 现有译文 | 问题类型 | 建议译文」四列对照格式\n"
            "- 错误类型分类：漏译（MT）、误译（MIS）、术语不一致（TERM）、语法错误（GRAM）、不自然表达（NAT）\n"
            "- 专有名词和术语首次出现后建立「术语表」，后续逐一检查一致性\n"
            "- 区分「硬错误」（影响理解，必须修改）和「软错误」（风格偏好，建议修改）\n"
            "- 最终给出：忠实度评分 + 流畅度评分 + 术语一致性评分（各 1-5 分）"
        ),
        "enabled": False,
    },

    # ── 专项调试技能 ──────────────────────────────────────────────────────────
    {
        "id": "debug_python",
        "name": "Python 调试",
        "icon": "🐍",
        "category": "domain",
        "description": "专项 Python 调试：精准定位 traceback、分析根因、给出最小复现和修复方案",
        "intent_description": "用户遇到 Python 报错、traceback、AttributeError、ImportError 等 bug 需要调试",
        "task_types": ["CODER"],
        "priority": 80,
        "prompt": (
            "\n\n## 🐍 领域要求：Python 专项调试\n"
            "- 优先读取完整 traceback，从最末帧（实际出错位置）逆向分析调用链\n"
            "- 调试输出结构：\n"
            "  1. 🔎 **根本原因**：一句话定位问题核心\n"
            "  2. 📋 **最小复现**：提炼能独立运行的最小代码片段\n"
            "  3. 🔧 **修复代码**：diff 格式或完整替换块\n"
            "  4. 💡 **防止复发**：说明如何避免同类问题（类型检查/防御性断言等）\n"
            "- 特别关注：类型错误、None 解引用、编码（UTF-8/GBK）、异步/线程竞态、循环 import\n"
            "- 涉及第三方库时，指出版本差异可能的影响（附 `pip show <pkg>` 验证建议）"
        ),
        "enabled": False,
    },
    {
        "id": "debug_web_frontend",
        "name": "前端调试",
        "icon": "🖥️",
        "category": "domain",
        "description": "专项前端调试：JS/TS/CSS 报错、控制台异常、DOM 问题、React/Vue 组件 bug",
        "intent_description": "用户遇到 JS/TS 报错、CSS 样式异常、React/Vue 组件 bug、浏览器控制台错误",
        "task_types": ["CODER"],
        "priority": 80,
        "prompt": (
            "\n\n## 🖥️ 领域要求：前端专项调试\n"
            "- 调试排查顺序：控制台错误 → Network 面板 → DOM 状态 → 组件 state/props\n"
            "- 区分错误来源：运行时错误（TypeError/ReferenceError）、异步错误（Promise rejection）、渲染错误（框架报错边界）\n"
            "- React/Vue 特有检查：key 唯一性、状态更新时机、副作用依赖数组、生命周期执行顺序\n"
            "- CSS 问题排查：选择器权重（specificity）、盒模型（box-sizing）、flexbox 轴方向、stacking context（z-index）\n"
            "- 修复输出：精确到文件行号的代码改动 + DevTools 验证步骤说明"
        ),
        "enabled": False,
    },
    {
        "id": "debug_api",
        "name": "API 联调调试",
        "icon": "🔌",
        "category": "domain",
        "description": "专项 API 调试：HTTP 状态码分析、请求/响应体排查、认证失败和超时问题",
        "intent_description": "用户遇到 API 请求失败、HTTP 错误码、认证失败、跨域 CORS、超时等接口问题",
        "task_types": ["CODER", "SYSTEM"],
        "priority": 75,
        "prompt": (
            "\n\n## 🔌 领域要求：API 联调调试\n"
            "- 分析框架：状态码 → 请求头（Authorization/Content-Type） → 请求体格式 → 响应体 → 超时/重试策略\n"
            "- 常见状态码处理指引：\n"
            "  - 4xx：聚焦客户端参数（401=认证缺失、403=权限不足、404=路径错误、422=参数格式）\n"
            "  - 5xx：聚焦服务端日志（500=内部错误、502=网关/反代问题、503=服务过载）\n"
            "  - CORS：检查 Origin 匹配和 Preflight 请求头\n"
            "- 认证问题：给出脱敏后的 curl 验证命令，帮助隔离问题\n"
            "- 超时问题：建议指数退避重试策略，区分 connect_timeout / read_timeout / total_timeout\n"
            "- 输出可直接运行的 curl 命令，便于在终端独立验证"
        ),
        "enabled": False,
    },
    {
        "id": "debug_performance",
        "name": "性能分析",
        "icon": "⚡",
        "category": "domain",
        "description": "代码/系统性能分析：定位瓶颈、复杂度评估、profiling 工具使用、分层优化建议",
        "intent_description": "用户遇到程序运行慢、内存占用高、接口延迟大、CPU 飙升等性能瓶颈问题",
        "task_types": ["CODER", "SYSTEM"],
        "priority": 70,
        "conflict_with": ["concise_mode"],
        "prompt": (
            "\n\n## ⚡ 领域要求：性能分析\n"
            "- 分析维度：时间复杂度（Big-O）、空间复杂度、I/O 密集型 vs CPU 密集型\n"
            "- 定位瓶颈优先级：数据库 N+1 查询 > 同步 I/O 阻塞 > 算法复杂度 > 内存分配与 GC\n"
            "- 输出结构：\n"
            "  1. 🎯 **性能瓶颈**：指明具体函数/行号/查询语句\n"
            "  2. 📊 **复杂度对比**：当前 vs 优化后的 Big-O\n"
            "  3. 🔧 **优化方案**：按收益从大到小排列，给出代码示例\n"
            "  4. 🧪 **验证方法**：Python → cProfile/timeit；前端 → Performance DevTools；DB → EXPLAIN ANALYZE\n"
            "- 将建议分层：「快速见效（< 1 天）」和「需要重构（> 3 天）」明确区分"
        ),
        "enabled": False,
    },

    # ── 记忆类 ────────────────────────────────────────────────────────────────
    {
        "id": "long_term_memory",
        "name": "长期记忆",
        "icon": "🧠",
        "category": "memory",
        "description": "跨会话记住用户偏好、项目背景和习惯，无需每次重复说明。通过「记忆」管理页面增删查看已存记忆。",
        "task_types": [],  # 所有任务类型均生效
        "prompt": "",      # prompt 由 inject_into_prompt 动态注入，此处留空
        "enabled": True,   # 默认开启，替代旧版设置里的独立开关
    },
]

# 所有合法的 category 和 task_type
SKILL_CATEGORIES = {
    "behavior": "⚙️ 行为",
    "style":    "🎨 风格",
    "domain":   "🔬 领域",
    "workflow": "🔄 工作流",
    "memory":   "🧠 记忆",
    "custom":   "🛠️ 自定义",
}

ALL_TASK_TYPES = [
    "CHAT", "CODER", "RESEARCH", "FILE_GEN",
    "SYSTEM", "AGENT", "WEB_SEARCH", "PAINTER",
    "DOC_ANNOTATE",
]


class SkillManager:
    """
    Skills 管理器 v2
    - 单例模式加载，首次访问时初始化
    - 启用/禁用状态持久化到 config/user_settings.json
    - 新增：_def_registry 存储原子化 SkillDefinition 对象（MCP 兼容）
    - 新增：register_custom / list_mcp_tools / load_custom_skills_dir
    """

    _registry: Dict[str, Dict] = {}               # id → 旧版 skill dict（向后兼容）
    _def_registry: Dict[str, SkillDefinition] = {} # id → 新版 SkillDefinition（v2）
    _initialized: bool = False

    # ── 初始化 ─────────────────────────────────────────────────────────────────
    @classmethod
    def _ensure_init(cls):
        if cls._initialized:
            return
        cls._registry = {}
        cls._def_registry = {}
        for skill in BUILTIN_SKILLS:
            s = dict(skill)  # shallow copy
            cls._registry[s["id"]] = s
            # 同步升级到 SkillDefinition
            cls._def_registry[s["id"]] = SkillDefinition.from_legacy_dict(s)
        cls._load_states_from_settings()
        # 从 config/skills/ 目录加载用户自定义 Skill（如果存在）
        cls._load_custom_skills_dir()
        cls._initialized = True

    @classmethod
    def _settings_path(cls) -> Path:
        """返回 config/user_settings.json 的绝对路径"""
        import sys
        if getattr(sys, 'frozen', False):
            # 打包模式：config/ 紧邻 Koto.exe，不在 _internal/ 里
            project_root = Path(sys.executable).parent
        else:
            here = Path(__file__).resolve()
            # app/core/skills/skill_manager.py → project_root/config/user_settings.json
            project_root = here.parents[3]
        return project_root / "config" / "user_settings.json"

    @classmethod
    def _load_states_from_settings(cls):
        """从 user_settings.json 读取持久化的启用状态"""
        try:
            p = cls._settings_path()
            if not p.exists():
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            skills_state = data.get("skills", {})
            for skill_id, state in skills_state.items():
                if skill_id in cls._registry and isinstance(state, dict):
                    if "enabled" in state:
                        cls._registry[skill_id]["enabled"] = bool(state["enabled"])
                    if "prompt_override" in state and state["prompt_override"]:
                        cls._registry[skill_id]["prompt"] = state["prompt_override"]
        except Exception as e:
            print(f"[SkillManager] 加载设置失败: {e}")

    @classmethod
    def _save_states_to_settings(cls):
        """将当前启用状态写回 user_settings.json"""
        try:
            p = cls._settings_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            skills_state = {}
            for skill_id, skill in cls._registry.items():
                state: Dict = {"enabled": skill["enabled"]}
                # 如果有自定义 prompt，也保存
                builtin_prompt = next(
                    (s["prompt"] for s in BUILTIN_SKILLS if s["id"] == skill_id), None
                )
                if skill["prompt"] != builtin_prompt:
                    state["prompt_override"] = skill["prompt"]
                skills_state[skill_id] = state
            data["skills"] = skills_state
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[SkillManager] 保存设置失败: {e}")

    # ── 公开 API ───────────────────────────────────────────────────────────────
    @classmethod
    def list_skills(cls) -> List[Dict]:
        """
        返回所有技能的完整信息列表（内置 + 自定义）
        """
        cls._ensure_init()
        result = []
        seen_ids: set = set()

        # 内置 Skill（保持原有顺序）
        for skill in BUILTIN_SKILLS:
            sid = skill["id"]
            seen_ids.add(sid)
            s = cls._registry.get(sid, skill)
            builtin_prompt = skill["prompt"]
            result.append({
                "id": s["id"],
                "name": s["name"],
                "icon": s["icon"],
                "category": s["category"],
                "description": s["description"],
                "task_types": s["task_types"],
                "enabled": s["enabled"],
                "has_custom_prompt": s.get("prompt") != builtin_prompt,
                "prompt": s["prompt"],
                "is_builtin": True,
            })

        # 自定义 Skill（不在内置列表中的）
        for skill_id, s in cls._registry.items():
            if skill_id in seen_ids:
                continue
            result.append({
                "id": s["id"],
                "name": s["name"],
                "icon": s["icon"],
                "category": s["category"],
                "description": s["description"],
                "task_types": s["task_types"],
                "enabled": s["enabled"],
                "has_custom_prompt": False,
                "prompt": s.get("prompt", ""),
                "is_builtin": False,
            })

        return result

    @classmethod
    def set_enabled(cls, skill_id: str, enabled: bool) -> bool:
        """启用或禁用一个技能，立即持久化"""
        cls._ensure_init()
        if skill_id not in cls._registry:
            return False
        cls._registry[skill_id]["enabled"] = enabled
        cls._save_states_to_settings()
        print(f"[SkillManager] {'✅ 启用' if enabled else '⏸️ 禁用'} skill: {skill_id}")
        return True

    @classmethod
    def update_prompt(cls, skill_id: str, prompt: str) -> bool:
        """更新某个技能的自定义 Prompt 内容（用户可自定义后保存）"""
        cls._ensure_init()
        if skill_id not in cls._registry:
            return False
        cls._registry[skill_id]["prompt"] = prompt
        cls._save_states_to_settings()
        return True

    @classmethod
    def reset_prompt(cls, skill_id: str) -> bool:
        """将某个技能的 Prompt 恢复为内置默认值"""
        cls._ensure_init()
        if skill_id not in cls._registry:
            return False
        builtin_prompt = next(
            (s["prompt"] for s in BUILTIN_SKILLS if s["id"] == skill_id), None
        )
        if builtin_prompt is not None:
            cls._registry[skill_id]["prompt"] = builtin_prompt
            cls._save_states_to_settings()
        return True

    @classmethod
    def check_conflicts(cls, task_type: Optional[str] = None) -> List[Dict]:
        """
        检测当前所有已启用 Skills 之间的冲突关系。

        返回冲突列表，每项格式：
          {
            "winner_id":   "concise_mode",      # 优先级更高（保留）
            "winner_name": "精简模式",
            "loser_id":    "research_depth",    # 被压制（注入时跳过）
            "loser_name":  "深度研究",
            "reason":      "concise_mode 优先级(90) > research_depth 优先级(55)"
          }
        """
        cls._ensure_init()
        conflicts: List[Dict] = []
        seen_pairs: set = set()

        enabled_skills = {
            sid: s for sid, s in cls._registry.items()
            if s.get("enabled", False)
        }
        # 筛选适用于当前 task_type 的
        if task_type:
            tt = task_type.upper()
            enabled_skills = {
                sid: s for sid, s in enabled_skills.items()
                if not s.get("task_types") or tt in s.get("task_types", [])
            }

        for skill_id, s in enabled_skills.items():
            for conflict_id in s.get("conflict_with", []):
                if conflict_id not in enabled_skills:
                    continue
                pair = tuple(sorted([skill_id, conflict_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                other = enabled_skills[conflict_id]
                pri_a = s.get("priority", 50)
                pri_b = other.get("priority", 50)

                if pri_a >= pri_b:
                    winner, loser = s, other
                    winner_id, loser_id = skill_id, conflict_id
                else:
                    winner, loser = other, s
                    winner_id, loser_id = conflict_id, skill_id

                conflicts.append({
                    "winner_id":   winner_id,
                    "winner_name": winner.get("name", winner_id),
                    "winner_priority": winner.get("priority", 50),
                    "loser_id":    loser_id,
                    "loser_name":  loser.get("name", loser_id),
                    "loser_priority": loser.get("priority", 50),
                    "reason": (
                        f"「{winner.get('name', winner_id)}」优先级({winner.get('priority', 50)}) "
                        f"≥「{loser.get('name', loser_id)}」优先级({loser.get('priority', 50)})，"
                        f"后者 prompt 在本次请求中被抑制"
                    ),
                })
        return conflicts

    @classmethod
    def inject_into_prompt(
        cls,
        base_instruction: str,
        task_type: Optional[str] = None,
        user_input: Optional[str] = None,
        temp_skill_ids: Optional[List[str]] = None,
    ) -> str:
        """
        将当前启用的、适用于 task_type 的 Skills 注入到 base_instruction 末尾。

        冲突处理：当两个互相冲突的 Skill 同时启用时，优先级（priority）更高的
        skill 正常注入，低优先级的 skill 被静默抑制（但仍保持「启用」状态供用户查看）。

        Args:
            base_instruction: 原始系统指令文本
            task_type:        当前任务类型（如 "CHAT"），None 表示通用
            user_input:       用户当前输入文本；传入后为长期记忆 skill
                              提供语义检索依据，精准命中相关记忆条目
            temp_skill_ids:   本轮临时激活的 Skill ID 列表（AutoMatcher 推荐）。
                              这些 Skill 即使 enabled=False 也会注入，且不修改
                              持久状态；注入后标注「自动匹配」供模型区分。

        Returns:
            注入后的系统指令文本
        """
        cls._ensure_init()
        active_prompts = []
        memory_block = ""
        seen_ids: set = set()  # 防止重复注入

        # ── 预计算冲突：找出所有因冲突被抑制的 skill_id ────────────────────
        suppressed_ids: set = set()
        all_enabled = {
            sid: s for sid, s in cls._registry.items()
            if s.get("enabled", False)
        }
        for sid, s in all_enabled.items():
            pri_a = s.get("priority", 50)
            for conflict_id in s.get("conflict_with", []):
                if conflict_id not in all_enabled:
                    continue
                pri_b = all_enabled[conflict_id].get("priority", 50)
                # 低优先级的那个被抑制
                if pri_a >= pri_b:
                    suppressed_ids.add(conflict_id)
                    logger.debug(
                        f"[SkillManager] 冲突抑制: {sid}(p={pri_a}) 抑制了 "
                        f"{conflict_id}(p={pri_b})"
                    )
                # 如果两者 priority 相等，按字母序决策（确保稳定性）
                elif pri_a == pri_b and sid < conflict_id:
                    suppressed_ids.add(conflict_id)

        # 遍历所有 registry 中的 Skill（内置 + 自定义），按 priority 排序
        all_skill_items = sorted(
            cls._registry.items(),
            key=lambda kv: kv[1].get("priority", 50),
            reverse=True,  # 高 priority 先注入
        )

        for skill_id, s in all_skill_items:
            if skill_id in seen_ids:
                continue
            seen_ids.add(skill_id)

            if not s.get("enabled", False):
                continue

            # 跳过被冲突抑制的 Skill
            if skill_id in suppressed_ids:
                logger.debug(f"[SkillManager] 跳过被抑制的 Skill: {skill_id}")
                continue

            applicable_types = s.get("task_types", [])
            if applicable_types and task_type and task_type.upper() not in applicable_types:
                continue

            # ── 长期记忆 skill：动态从 MemoryManager 检索相关记忆并注入 ────────
            if skill_id == "long_term_memory":
                try:
                    from web.memory_manager import MemoryManager
                    _mm = MemoryManager()
                    ctx = _mm.get_context_string(user_input or "")
                    if ctx.strip():
                        memory_block = ctx
                except Exception as _me:
                    logger.debug(f"[SkillManager] 长期记忆注入跳过: {_me}")
                continue  # 长期记忆不走普通 prompt 通道

            # 优先使用新版 SkillDefinition 的 render_prompt()
            skill_def = cls._def_registry.get(skill_id)
            if skill_def and skill_def.system_prompt_template:
                p = skill_def.render_prompt().strip()
            else:
                p = s.get("prompt", "").strip()

            if p:
                active_prompts.append(p)

            # ── Word 模板 skill：追加模板字段说明 ─────────────────────────────
            tmpl_path_rel = s.get("template_path")
            if tmpl_path_rel:
                try:
                    from pathlib import Path as _Path
                    import sys as _sys
                    _base = (
                        _Path(_sys.executable).parent if getattr(_sys, "frozen", False)
                        else _Path(__file__).resolve().parents[3]
                    )
                    tmpl_abs = _base / tmpl_path_rel
                    if not tmpl_abs.exists():
                        # 也尝试约定路径
                        tmpl_abs = _base / "config" / "skill_templates" / skill_id / "template.docx"
                    if tmpl_abs.exists():
                        from app.core.skills.template_engine import TemplateEngine
                        fields = TemplateEngine.parse_fields(tmpl_abs)
                        preview = TemplateEngine.get_raw_text(tmpl_abs)
                        tmpl_prompt = TemplateEngine.build_agent_prompt(
                            s.get("name", skill_id), fields, preview
                        )
                        active_prompts.append(tmpl_prompt)
                except Exception as _te:
                    logger.debug(f"[SkillManager] 模板提示注入跳过 ({skill_id}): {_te}")

        # ── 临时 Skill 注入（AutoMatcher 推荐，本轮生效，不改变持久状态）────
        auto_prompts = []
        _temp_ids = temp_skill_ids or []
        for skill_id in _temp_ids:
            if skill_id in seen_ids:
                continue  # 已作为用户启用 Skill 注入过，无需重复
            seen_ids.add(skill_id)
            s = cls._registry.get(skill_id)
            if not s:
                logger.debug(f"[SkillManager] 临时 Skill 不存在，跳过: {skill_id}")
                continue
            skill_def = cls._def_registry.get(skill_id)
            if skill_def and skill_def.system_prompt_template:
                p = skill_def.render_prompt().strip()
            else:
                p = s.get("prompt", "").strip()
            if p:
                auto_prompts.append(p)
                logger.debug(f"[SkillManager] 🤖 临时注入 Auto-Skill: {skill_id}")

        # 组装注入块：记忆优先放在 skills 前面
        result = base_instruction
        if memory_block:
            result = result + "\n\n─────────────────────────────────────────" + memory_block
        if active_prompts:
            separator = "\n\n─────────────────────────────────────────"
            skills_block = separator + "\n## 🎯 当前激活的 Skills（用户自定义行为）\n" + "\n".join(active_prompts)
            result = result + skills_block
        if auto_prompts:
            separator = "\n\n─────────────────────────────────────────"
            auto_block = (
                separator
                + "\n## 🤖 自动匹配的 Skills（本轮智能推荐，仅本次生效）\n"
                + "\n".join(auto_prompts)
            )
            result = result + auto_block
        return result

    @classmethod
    def get_active_skill_names(cls, task_type: Optional[str] = None) -> List[str]:
        """返回当前启用的、适用于 task_type 的技能名称列表（含自定义 Skill）"""
        cls._ensure_init()
        names = []
        seen_ids: set = set()
        for skill_id, s in cls._registry.items():
            if skill_id in seen_ids:
                continue
            seen_ids.add(skill_id)
            if not s.get("enabled", False):
                continue
            applicable_types = s.get("task_types", [])
            if applicable_types and task_type and task_type.upper() not in applicable_types:
                continue
            names.append(s["name"])
        return names

    @classmethod
    def reload(cls):
        """强制重新加载（settings 文件被外部修改后调用）"""
        cls._initialized = False
        cls._ensure_init()

    # ═══════════════════════════════════════════════════════════════
    # ▼  v2 新增 API（SkillDefinition / MCP / 自定义 Skill 支持）
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def get_definition(cls, skill_id: str) -> Optional[SkillDefinition]:
        """
        返回指定 skill 的完整 SkillDefinition 对象。
        用于路由决策、变量渲染、输出验收。
        """
        cls._ensure_init()
        return cls._def_registry.get(skill_id)

    @classmethod
    def list_mcp_tools(cls) -> List[Dict]:
        """
        将所有 **已启用** 的 Skill 导出为 MCP (Model Context Protocol) 兼容的
        Tool 描述列表，可直接传给支持 MCP 的 LLM host 或外部系统。
        """
        cls._ensure_init()
        tools = []
        for skill_id, skill_def in cls._def_registry.items():
            # 同步最新启用状态
            legacy = cls._registry.get(skill_id)
            if legacy:
                skill_def.enabled = legacy.get("enabled", False)
            if skill_def.enabled:
                tools.append(skill_def.to_mcp_tool())
        return tools

    @classmethod
    def list_all_mcp_tools(cls) -> List[Dict]:
        """
        导出所有 Skill（不论是否启用）的 MCP Tool 描述列表。
        用于 Studio / Marketplace 展示。
        """
        cls._ensure_init()
        return [skill_def.to_mcp_tool() for skill_def in cls._def_registry.values()]

    @classmethod
    def register_custom(cls, skill_def: SkillDefinition) -> bool:
        """
        注册一个新的自定义 Skill（运行时动态注册）。
        同时写入 _registry 和 _def_registry，并持久化到 config/skills/{id}.json。

        Args:
            skill_def: 完整的 SkillDefinition 对象

        Returns:
            True 成功，False 失败（id 已存在于 builtin 且 author=="builtin" 时拒绝覆盖）
        """
        cls._ensure_init()
        existing = cls._def_registry.get(skill_def.id)
        if existing and existing.author == "builtin":
            logger.warning(
                f"[SkillManager] 拒绝覆盖内置 Skill: {skill_def.id}。"
                f"如需修改内置 Skill，请先 fork 并使用不同 id。"
            )
            return False

        skill_def.author = skill_def.author or "user"
        cls._def_registry[skill_def.id] = skill_def

        # 同步到旧版 _registry（保证 inject_into_prompt 等方法正常工作）
        cls._registry[skill_def.id] = {
            "id": skill_def.id,
            "name": skill_def.name,
            "icon": skill_def.icon,
            "category": (
                skill_def.category.value
                if hasattr(skill_def.category, "value")
                else skill_def.category
            ),
            "description": skill_def.description,
            "task_types": skill_def.task_types,
            "prompt": skill_def.render_prompt(),
            "enabled": skill_def.enabled,
        }

        cls._apply_default_triggers(skill_def)

        # 持久化到 config/skills/{id}.json
        cls._persist_custom_skill(skill_def)
        logger.info(f"[SkillManager] ✅ 注册自定义 Skill: {skill_def.id} (v{skill_def.version})")
        return True

    @classmethod
    def _apply_default_triggers(cls, skill_def: SkillDefinition):
        """Register manifest v2 default triggers once per skill/config pair."""
        default_triggers = list(getattr(skill_def, "default_triggers", None) or [])
        if not default_triggers:
            return

        try:
            from app.core.skills.skill_trigger_binding import get_skill_binding_manager

            binding_manager = get_skill_binding_manager()
            existing = binding_manager.list_bindings(
                skill_id=skill_def.id,
                binding_type="trigger",
            )
            existing_keys = {
                (
                    binding.trigger_type,
                    json.dumps(binding.trigger_config or {}, sort_keys=True, ensure_ascii=False),
                )
                for binding in existing
            }

            for trigger in default_triggers:
                trigger_type = (trigger.get("trigger_type") or trigger.get("type") or "").strip()
                trigger_config = trigger.get("config") or {}
                if not trigger_type:
                    continue

                trigger_key = (
                    trigger_type,
                    json.dumps(trigger_config, sort_keys=True, ensure_ascii=False),
                )
                if trigger_key in existing_keys:
                    continue

                binding_manager.bind_trigger(
                    skill_id=skill_def.id,
                    trigger_type=trigger_type,
                    trigger_config=trigger_config,
                    mode=trigger.get("mode", "execute"),
                    job_payload=trigger.get("job_payload") or {
                        "skill_id": skill_def.id,
                        "query": trigger.get("query") or f"执行技能: {skill_def.name}",
                    },
                    name=trigger.get("name"),
                )
                existing_keys.add(trigger_key)
        except Exception as exc:
            logger.warning(f"[SkillManager] 应用默认触发器失败: {exc}")

    @classmethod
    def _persist_custom_skill(cls, skill_def: SkillDefinition):
        """将自定义 Skill 写入 config/skills/{id}.json"""
        try:
            skills_dir = cls._settings_path().parent / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            skill_file = skills_dir / f"{skill_def.id}.json"
            with open(skill_file, "w", encoding="utf-8") as f:
                json.dump(skill_def.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[SkillManager] 自定义 Skill 持久化失败: {e}")

    @classmethod
    def _load_custom_skills_dir(cls):
        """从 config/skills/ 目录加载所有自定义 Skill JSON 文件"""
        try:
            skills_dir = cls._settings_path().parent / "skills"
            if not skills_dir.exists():
                return
            for skill_file in skills_dir.glob("*.json"):
                try:
                    with open(skill_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    skill_def = SkillDefinition.from_dict(data)
                    # 自定义 Skill 不覆盖内置（内置在初始化时已先加载）
                    if skill_def.id not in cls._def_registry:
                        cls._def_registry[skill_def.id] = skill_def
                        entry = {
                            "id": skill_def.id,
                            "name": skill_def.name,
                            "icon": skill_def.icon,
                            "category": (
                                skill_def.category.value
                                if hasattr(skill_def.category, "value")
                                else skill_def.category
                            ),
                            "description": skill_def.description,
                            "task_types": skill_def.task_types,
                            "prompt": skill_def.render_prompt(),
                            "enabled": skill_def.enabled,
                        }
                        # 保留 template_path 和 bound_tools（若 JSON 中有）
                        if data.get("template_path"):
                            entry["template_path"] = data["template_path"]
                        if data.get("bound_tools"):
                            entry["bound_tools"] = data["bound_tools"]
                        cls._registry[skill_def.id] = entry
                        logger.info(f"[SkillManager] 加载自定义 Skill: {skill_def.id}")
                except Exception as e:
                    logger.warning(f"[SkillManager] 加载 {skill_file.name} 失败: {e}")
        except Exception as e:
            logger.warning(f"[SkillManager] 加载自定义 Skill 目录失败: {e}")

    @classmethod
    def validate_output(cls, skill_id: str, text: str) -> tuple:
        """
        使用指定 Skill 的 OutputSpec 验收文本。
        返回 (passed: bool, reason: str)
        若 Skill 不存在或无 OutputSpec，默认通过。
        """
        cls._ensure_init()
        skill_def = cls._def_registry.get(skill_id)
        if not skill_def:
            return True, "Skill 不存在，跳过验收"
        return skill_def.output_spec.validate(text)

    @classmethod
    def get_intent_descriptions(cls) -> Dict[str, str]:
        """
        返回所有已启用 Skill 的 {id: intent_description} 映射。
        供 Qwen Router 在意图识别时参考，提升路由准确性。
        """
        cls._ensure_init()
        result = {}
        for skill_id, skill_def in cls._def_registry.items():
            legacy = cls._registry.get(skill_id, {})
            if legacy.get("enabled", skill_def.enabled) and skill_def.intent_description:
                result[skill_id] = skill_def.intent_description
        return result

    # ═══════════════════════════════════════════════════════════════
    # ▼  智能推荐：根据用户输入建议相关 Skill
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def suggest_skills(
        cls,
        user_input: str,
        task_type: Optional[str] = None,
        top_k: int = 3,
        exclude_enabled: bool = True,
    ) -> List[Dict]:
        """
        根据用户输入和任务类型，推荐最相关的未启用 Skill。

        算法：
        1. 将 user_input 与每个 Skill 的 intent_description + description + tags 做关键词匹配打分
        2. 同时检查 task_type 适配性
        3. 返回得分最高的 top_k 个 Skill

        Args:
            user_input:     用户当前消息文本
            task_type:      当前任务类型（如 "CHAT", "CODER"）
            top_k:          返回建议数量
            exclude_enabled: True = 只推荐未启用的（让用户决定是否开启）

        Returns:
            [{"id", "name", "icon", "description", "score", "reason"}, ...]
        """
        import re as _re

        cls._ensure_init()
        user_lower = user_input.lower()
        scores: List[Dict] = []

        for skill_id, skill_def in cls._def_registry.items():
            s = cls._registry.get(skill_id, {})

            # 排除已启用的（可选）
            if exclude_enabled and s.get("enabled", skill_def.enabled):
                continue

            # 检查 task_type 适配性
            applicable = skill_def.task_types or []
            if applicable and task_type and task_type.upper() not in applicable:
                continue

            score = 0.0
            matched_reasons: List[str] = []

            # 计算与 intent_description 的相关性
            intent = (skill_def.intent_description or "").lower()
            desc = (skill_def.description or "").lower()
            tags = " ".join(skill_def.tags).lower()
            combined = f"{intent} {desc} {tags}"

            # 词频匹配 — 提取用户输入的关键词
            words = _re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", user_lower)
            for word in words:
                if word in combined:
                    score += 1.0
                    matched_reasons.append(word)

            # 精确短语匹配加权
            for phrase_len in (4, 3, 2):
                for i in range(len(user_lower) - phrase_len + 1):
                    phrase = user_lower[i:i + phrase_len]
                    if phrase in combined:
                        score += phrase_len * 0.5
                        break

            # 名称完全或部分匹配
            skill_name_lower = (skill_def.name or "").lower()
            if any(w in skill_name_lower for w in words if len(w) >= 2):
                score += 2.0
                matched_reasons.append(f"名称匹配: {skill_def.name}")

            if score > 0:
                reason = "与「{name}」相关：{r}".format(
                    name=skill_def.name,
                    r="、".join(dict.fromkeys(matched_reasons))[:50] if matched_reasons else "语义相关",
                )
                scores.append({
                    "id": skill_id,
                    "name": skill_def.name,
                    "icon": skill_def.icon,
                    "description": skill_def.description,
                    "score": round(score, 2),
                    "reason": reason,
                    "category": (
                        skill_def.category.value
                        if hasattr(skill_def.category, "value")
                        else skill_def.category
                    ),
                })

        # 按得分降序排列，取 top_k
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    # ═══════════════════════════════════════════════════════════════
    # ▼  冲突检测：防止相互矛盾的 Skill 同时启用
    # ═══════════════════════════════════════════════════════════════

    # 已知冲突组 — 同组内同时启用 2+ 个则报冲突
    _CONFLICT_GROUPS: List[tuple] = [
        # 详细 vs 精简
        ("step_by_step", "concise_mode"),
        # 正式 vs 随意（无强制，作为警告提示）
        # 高幽默 vs 严谨模式（警告级别）
        ("strict_mode", "emoji_assist"),
    ]
    # 软冲突（警告但不阻止）
    _SOFT_CONFLICTS: Dict[str, List[str]] = {
        "concise_mode":  ["step_by_step", "teaching_mode", "proactive_suggestions"],
        "step_by_step":  ["concise_mode"],
        "strict_mode":   ["creative_writing", "emoji_assist"],
        "creative_writing": ["strict_mode", "professional_tone", "data_analysis"],
        "professional_tone": ["creative_writing"],
    }

    @classmethod
    def detect_conflicts(cls, skill_id: str) -> Dict:
        """
        检测启用 skill_id 后是否与当前其他已启用 Skill 产生冲突。
        综合检查：内置冲突规则表 + SkillDefinition.conflict_with 声明字段

        Returns:
            {
              "has_conflict": bool,
              "hard_conflicts": [{"id", "name", "reason"}, ...],
              "soft_conflicts": [{"id", "name", "reason"}, ...],
            }
        """
        cls._ensure_init()
        hard: List[Dict] = []
        soft: List[Dict] = []

        # 当前已启用集合
        enabled_ids = {
            sid for sid, s in cls._registry.items()
            if s.get("enabled", False) and sid != skill_id
        }

        this_def = cls._def_registry.get(skill_id)
        this_name = this_def.name if this_def else skill_id

        # 声明式 conflict_with 字段检查
        if this_def and this_def.conflict_with:
            for other_id in this_def.conflict_with:
                if other_id in enabled_ids:
                    other_def = cls._def_registry.get(other_id)
                    other_name = other_def.name if other_def else other_id
                    hard.append({
                        "id": other_id,
                        "name": other_name,
                        "reason": f"「{this_name}」声明与「{other_name}」不兼容",
                    })

        # 内置硬冲突规则
        hard_ids = {h["id"] for h in hard}
        for group in cls._CONFLICT_GROUPS:
            if skill_id in group:
                others = [g for g in group if g != skill_id]
                for other_id in others:
                    if other_id in enabled_ids and other_id not in hard_ids:
                        other_def = cls._def_registry.get(other_id)
                        other_name = other_def.name if other_def else other_id
                        hard.append({
                            "id": other_id,
                            "name": other_name,
                            "reason": f"「{this_name}」与「{other_name}」行为相反，同时启用会产生矛盾",
                        })
                        hard_ids.add(other_id)

        # 软冲突检测
        soft_list = cls._SOFT_CONFLICTS.get(skill_id, [])
        for other_id in soft_list:
            if other_id in enabled_ids and other_id not in hard_ids:
                other_def = cls._def_registry.get(other_id)
                other_name = other_def.name if other_def else other_id
                soft.append({
                    "id": other_id,
                    "name": other_name,
                    "reason": f"与「{other_name}」可能存在风格不一致，建议选其一",
                })

        return {
            "has_conflict": bool(hard) or bool(soft),
            "hard_conflicts": hard,
            "soft_conflicts": soft,
        }

    # ═══════════════════════════════════════════════════════════════
    # ▼  响应验收：对 LLM 回复批量验证所有激活 Skill 的 OutputSpec
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def validate_response(
        cls,
        text: str,
        task_type: Optional[str] = None,
    ) -> Dict:
        """
        对 LLM 生成的回复文本，批量检验所有当前激活 Skill 的 OutputSpec。

        Args:
            text:      LLM 生成的回复文本
            task_type: 当前任务类型

        Returns:
            {
              "all_passed": bool,
              "results": [{"skill_id", "skill_name", "passed", "reason"}, ...]
            }
        """
        cls._ensure_init()
        results = []
        all_passed = True

        for skill_id, skill_def in cls._def_registry.items():
            s = cls._registry.get(skill_id, {})
            if not s.get("enabled", skill_def.enabled):
                continue
            applicable = skill_def.task_types or []
            if applicable and task_type and task_type.upper() not in applicable:
                continue
            # 若 OutputSpec 约束为空（默认），跳过
            spec = skill_def.output_spec
            has_constraint = (
                spec.must_contain or spec.must_not_contain
                or spec.min_chars or spec.max_chars
                or spec.required_json_keys
            )
            if not has_constraint:
                continue

            passed, reason = spec.validate(text)
            if not passed:
                all_passed = False
            results.append({
                "skill_id": skill_id,
                "skill_name": skill_def.name,
                "passed": passed,
                "reason": reason,
            })

        return {"all_passed": all_passed, "results": results}

    # ═══════════════════════════════════════════════════════════════
    # ▼  Skill 摘要：供调试 / UI 状态面板使用
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def get_status_summary(cls) -> Dict:
        """
        返回当前 Skill 库的完整状态摘要，供 UI/日志使用。
        """
        cls._ensure_init()
        total = len(cls._def_registry)
        enabled = sum(1 for s in cls._registry.values() if s.get("enabled", False))
        builtin_count = sum(1 for s in cls._def_registry.values() if s.author == "builtin")
        custom_count = total - builtin_count
        active_names = cls.get_active_skill_names()

        return {
            "total": total,
            "enabled": enabled,
            "builtin": builtin_count,
            "custom": custom_count,
            "active_skill_names": active_names,
            "version": "2.1",
        }

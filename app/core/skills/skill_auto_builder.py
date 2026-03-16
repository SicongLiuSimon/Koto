# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║      Koto  ─  SkillAutoBuilder：交流风格 → Skill 自动转化器      ║
╚══════════════════════════════════════════════════════════════════╝

核心功能
────────
1. 风格分析（StyleAnalyzer）
   - 从对话历史中检测交流风格模式（语气、格式倾向、专业程度等）
   - 支持 10+ 维度量化评分

2. Prompt 生成器（PromptSynthesizer）
   - 根据风格评分自动合成高质量 system_prompt_template
   - 注入具体的行为约束和输出期望

3. 自动构建入口（SkillAutoBuilder.from_style_description）
   - 输入：一段自然语言描述（"我想要一个像老朋友一样聊天的风格"）
   - 输出：完整的 SkillDefinition（无需 AI API，纯本地规则引擎）

4. 对话模式识别（ConversationPatternMatcher）
   - 从历史对话中提取 AI 使用的独特表达模式
   - 复现对话中的高质量回复风格

用法示例
────────
    from app.core.skills.skill_auto_builder import SkillAutoBuilder

    # 方式 1：从风格描述自动生成
    skill = SkillAutoBuilder.from_style_description(
        name="暖心闺蜜",
        description="像闺蜜一样聊天，温柔、感同身受、善于鼓励"
    )

    # 方式 2：从对话历史提取风格
    skill = SkillAutoBuilder.from_conversation_history(
        session_id="chat_123",
        name="我的私人助理风格",
    )

    # 方式 3：用风格调节旋钮手动配置
    skill = SkillAutoBuilder.from_style_config(
        name="极简主义",
        formality=0.8,       # 0=口语, 1=正式
        verbosity=0.2,       # 0=极简, 1=详细
        empathy=0.5,         # 0=客观, 1=感同身受
        structure=0.9,       # 0=自由, 1=结构化
        creativity=0.1,      # 0=保守, 1=创意
        domain="general",    # 专业领域
    )
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys as _sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _get_base_dir() -> Path:
    if getattr(_sys, 'frozen', False):
        return Path(_sys.executable).parent
    return Path(__file__).resolve().parents[3]


_BASE_DIR = _get_base_dir()

# ══════════════════════════════════════════════════════════════════
# 风格维度枚举与评分结构
# ══════════════════════════════════════════════════════════════════

STYLE_DIMENSIONS = [
    "formality",    # 语气正式程度  0=口语/随意  1=严肃/正式
    "verbosity",    # 详细程度      0=极简  1=非常详细
    "empathy",      # 共情程度      0=客观中立  1=温暖感同身受
    "structure",    # 结构化程度    0=散文自由  1=高度结构化
    "creativity",   # 创意程度      0=保守务实  1=创意发散
    "technicality", # 技术深度      0=通俗易懂  1=专业技术
    "positivity",   # 积极程度      0=中性/批判  1=积极鼓励
    "proactivity",  # 主动建议程度  0=被动回答  1=主动发散
    "humor",        # 幽默程度      0=严肃  1=幽默风趣
    "conciseness",  # 简洁程度      0=冗长  1=精炼（与 verbosity 反向）
]


@dataclass
class StyleProfile:
    """10 维风格评分（0.0 ~ 1.0）"""
    formality:    float = 0.5
    verbosity:    float = 0.5
    empathy:      float = 0.5
    structure:    float = 0.5
    creativity:   float = 0.3
    technicality: float = 0.3
    positivity:   float = 0.6
    proactivity:  float = 0.4
    humor:        float = 0.2
    conciseness:  float = 0.5
    domain:       str   = "general"   # 专业领域标签
    language:     str   = "zh"        # 语言偏好

    def to_dict(self) -> Dict[str, Any]:
        return {
            "formality": self.formality,
            "verbosity": self.verbosity,
            "empathy": self.empathy,
            "structure": self.structure,
            "creativity": self.creativity,
            "technicality": self.technicality,
            "positivity": self.positivity,
            "proactivity": self.proactivity,
            "humor": self.humor,
            "conciseness": self.conciseness,
            "domain": self.domain,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "StyleProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ══════════════════════════════════════════════════════════════════
# 规则库：关键词 → 风格信号映射
# ══════════════════════════════════════════════════════════════════

_KEYWORD_SIGNALS: List[Tuple[str, str, float, float]] = [
    # (pattern, dimension, direction_score, weight)
    # 正式度相关
    (r"正式|专业|商务|学术|报告", "formality", 0.85, 1.0),
    (r"随意|轻松|朋友|聊天|口语", "formality", 0.15, 1.0),
    (r"您|贵方|请问|敬请", "formality", 0.9, 0.7),
    (r"哈哈|哈|lol|笑死|太好玩", "formality", 0.1, 0.8),
    # 详细度
    (r"详细|全面|深入|系统|完整|逐步|step by step", "verbosity", 0.85, 1.0),
    (r"简洁|简短|精简|一句话|快速|要点", "verbosity", 0.15, 1.0),
    (r"简单来说|总结|要点|核心", "verbosity", 0.25, 0.8),
    # 共情度
    (r"温暖|关怀|体贴|感同身受|理解|支持|陪伴|鼓励", "empathy", 0.9, 1.0),
    (r"客观|中立|理性|逻辑|数据|事实", "empathy", 0.15, 0.8),
    (r"闺蜜|好朋友|倾听|情绪|心情|难过|开心", "empathy", 0.85, 1.0),
    # 结构化
    (r"结构|框架|步骤|列表|分条|有序|清晰|层次", "structure", 0.9, 1.0),
    (r"自由|散文|创意写作|诗意|流畅|流动", "structure", 0.15, 0.8),
    (r"表格|清单|numbered|bullet", "structure", 0.88, 0.9),
    # 创意度
    (r"创意|创新|灵感|想象|跳出框架|新颖|独特", "creativity", 0.9, 1.0),
    (r"传统|保守|规范|标准|经典|稳妥", "creativity", 0.15, 0.8),
    (r"比喻|故事|隐喻|联想|发散", "creativity", 0.8, 0.9),
    # 技术深度
    (r"代码|技术|编程|算法|架构|系统|API|数据库|python|java", "technicality", 0.9, 1.0),
    (r"通俗|简单|入门|初学者|小白|非技术|日常", "technicality", 0.1, 0.9),
    (r"法律|医疗|金融|学术|研究|论文|专业知识", "technicality", 0.8, 0.8),
    # 积极度
    (r"鼓励|积极|正能量|加油|支持|赞美|肯定|棒", "positivity", 0.9, 1.0),
    (r"批判|客观|挑战|质疑|反驳|严格", "positivity", 0.2, 0.7),
    # 主动建议
    (r"主动|建议|延伸|提示|提醒|预警|下一步", "proactivity", 0.85, 1.0),
    (r"只回答|不添加|直接回复|不多说", "proactivity", 0.1, 0.9),
    # 幽默
    (r"幽默|风趣|搞笑|俏皮|有趣|笑|轻松愉快|调皮", "humor", 0.85, 1.0),
    (r"严肃|认真|正经|不开玩笑", "humor", 0.05, 0.9),
    # 简洁度（与 verbosity 相反方向）
    (r"简洁|精炼|简短|一句|极简|直接", "conciseness", 0.9, 1.0),
    (r"展开|详细解释|深入讲解|全面", "conciseness", 0.15, 0.8),
]

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "coding":       ["代码", "编程", "开发", "python", "java", "javascript", "算法", "bug", "debug", "API"],
    "writing":      ["写作", "文章", "文案", "创作", "故事", "编辑", "润色", "排版"],
    "research":     ["研究", "分析", "调研", "文献", "论文", "学术", "数据", "统计"],
    "finance":      ["金融", "投资", "股票", "理财", "基金", "会计", "财务", "经济"],
    "legal":        ["法律", "合同", "条款", "法规", "诉讼", "律师", "合规"],
    "medical":      ["医疗", "健康", "病症", "药物", "治疗", "医生", "诊断"],
    "education":    ["教育", "教学", "学习", "课程", "辅导", "知识", "老师", "学生"],
    "marketing":    ["营销", "推广", "文案", "品牌", "广告", "用户", "转化", "SEO"],
    "productivity": ["效率", "工作", "任务", "管理", "日程", "流程", "优化"],
    "lifestyle":    ["生活", "健身", "饮食", "旅行", "情感", "关系", "心理"],
}

# ══════════════════════════════════════════════════════════════════
# 风格分析器
# ══════════════════════════════════════════════════════════════════

class StyleAnalyzer:
    """
    从文本中分析交流风格，输出 StyleProfile。
    纯本地规则引擎，无需调用 AI API。
    """

    @classmethod
    def analyze_text(cls, text: str) -> StyleProfile:
        """从单段文本（描述或对话片段）推断风格"""
        text_lower = text.lower()
        scores: Dict[str, List[float]] = {dim: [] for dim in STYLE_DIMENSIONS}

        # 关键词扫描
        for pattern, dim, score, weight in _KEYWORD_SIGNALS:
            if re.search(pattern, text_lower):
                scores[dim].extend([score] * int(weight * 10))

        # 计算均值，未触发的维度保持默认 0.5
        profile_kwargs: Dict[str, Any] = {}
        for dim in STYLE_DIMENSIONS:
            if scores[dim]:
                profile_kwargs[dim] = round(sum(scores[dim]) / len(scores[dim]), 2)
            else:
                profile_kwargs[dim] = 0.5

        # 领域检测
        domain = cls._detect_domain(text_lower)
        profile_kwargs["domain"] = domain

        # 语言检测（简单启发式）
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        profile_kwargs["language"] = "zh" if chinese_chars > len(text) * 0.1 else "en"

        return StyleProfile(**profile_kwargs)

    @classmethod
    def analyze_conversation(cls, turns: List[Dict[str, str]], role: str = "assistant") -> StyleProfile:
        """
        从对话历史中提取 AI 回复的风格。
        仅分析 role='assistant' 的消息。
        """
        ai_texts = [
            t["text"] for t in turns
            if t.get("role") in (role, "ai", "model")
        ]
        if not ai_texts:
            return StyleProfile()
        combined = "\n".join(ai_texts[:20])  # 最多取 20 条
        return cls.analyze_text(combined)

    @classmethod
    def _detect_domain(cls, text_lower: str) -> str:
        domain_scores: Dict[str, int] = {}
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if count > 0:
                domain_scores[domain] = count
        if not domain_scores:
            return "general"
        return max(domain_scores, key=domain_scores.get)


# ══════════════════════════════════════════════════════════════════
# Prompt 合成器
# ══════════════════════════════════════════════════════════════════

class PromptSynthesizer:
    """
    根据 StyleProfile 合成 system_prompt_template 和 intent_description。
    采用模块化 Prompt 块拼装策略，保证生成质量。
    """

    # ── 各维度 Prompt 模块库 ─────────────────────────────────────────────
    _FORMALITY_BLOCKS = {
        "high": (
            "请使用正式的书面语言，措辞严谨、客观、专业。"
            "避免口语化表达和网络用语，使用「您」称呼用户。"
            "数字和单位规范书写，结论和观点要有依据支撑。"
        ),
        "mid": (
            "语气自然、亲切，既不过于随意也不过于刻板。"
            "在专业话题上保持准确，在日常话题上可以轻松一些。"
        ),
        "low": (
            "用口语化、轻松自然的方式交流，像朋友一样说话。"
            "可以用「你」称呼用户，语气随意不拘谨，自然流畅。"
        ),
    }

    _VERBOSITY_BLOCKS = {
        "high": (
            "提供详细、全面的回答。对复杂概念逐步展开，给出背景、原因、步骤和示例。"
            "宁可多说一点，也不要让用户意犹未尽。"
        ),
        "mid": "根据问题复杂度调整回答长度，简单问题简短回答，复杂问题充分展开。",
        "low": (
            "保持回答简洁精炼，控制在 150 字以内。只给核心结论和最关键信息，省略冗余解释。"
            "用户如需要更多细节，会主动追问。"
        ),
    }

    _EMPATHY_BLOCKS = {
        "high": (
            "充分感受用户的情绪和处境，在给出建议之前先表达理解和共情。"
            "用「我理解」「听起来你感到…」等方式建立情感连接。"
            "在困难时期给予温暖支持，在开心时一起庆祝。"
        ),
        "mid": "在适当时候关注用户的感受，保持温和、体贴的态度。",
        "low": "保持客观、理性的立场，聚焦于事实和逻辑，不过多涉及情绪层面。",
    }

    _STRUCTURE_BLOCKS = {
        "high": (
            "回答时使用清晰的结构：标题分节、有序步骤、要点列表。"
            "确保每个段落只表达一个主要观点，层次分明。"
            "复杂内容优先考虑用表格或列表呈现。"
        ),
        "mid": "根据内容自然组织结构，长回答用小标题分隔，简单问题可以直接回答。",
        "low": (
            "以自然流畅的散文方式回答，不强制使用固定格式。"
            "让思路自由流动，像正常对话一样表达。"
        ),
    }

    _CREATIVITY_BLOCKS = {
        "high": (
            "鼓励创新思维，给出独特的角度和意想不到的联系。"
            "善用比喻、类比和故事让想法生动起来。"
            "展示多种可能性，不局限于唯一答案。"
        ),
        "mid": "在需要创意的地方发挥想象，在需要准确的地方保持严谨。",
        "low": "坚持经过验证的方法和成熟的思路，注重实用性而非新奇。",
    }

    _TECHNICALITY_BLOCKS = {
        "high": (
            "使用准确的技术术语，不过度简化专业概念。"
            "可以假设用户具备一定的专业背景，直接展开技术细节。"
        ),
        "mid": "技术内容解释清楚，专业术语首次出现时给出简短说明。",
        "low": (
            "用最通俗易懂的方式解释，避免专业术语或在使用时加括号解释。"
            "可以用生活中的例子类比抽象概念。"
        ),
    }

    _POSITIVITY_BLOCKS = {
        "high": (
            "保持积极、鼓励的态度。肯定用户的努力和进步，用正向语言表达。"
            "当用户遇到困难时，重点放在可能性和解决方案上。"
        ),
        "mid": "保持中立、建设性的态度，真实评价，不过度褒贬。",
        "low": (
            "客观评价，不回避问题和不足之处。"
            "批判性分析是帮助改进的方式，直接指出问题比一味鼓励更有价值。"
        ),
    }

    _PROACTIVITY_BLOCKS = {
        "high": (
            "在完成主要回答后，主动提出 1-3 个相关的延伸建议或需要注意的问题。"
            "预判用户可能的后续需求，提前给出信息。"
        ),
        "mid": "在明显必要时才给出额外建议，不强行延伸话题。",
        "low": "只回答用户明确提出的问题，不添加额外的建议或评论。",
    }

    _HUMOR_BLOCKS = {
        "high": (
            "在适当时机加入轻松幽默的元素，让对话愉快有趣。"
            "可以用俏皮的表达、小玩笑或轻松的比喻，但不影响内容准确性。"
        ),
        "mid": "偶尔可以轻松幽默，但整体保持认真负责的态度。",
        "low": "保持严肃、认真的态度，聚焦于内容质量，不追求娱乐性。",
    }

    _DOMAIN_CONTEXTS = {
        "coding":       "你擅长软件开发和编程，能解决技术问题、审查代码、设计架构。",
        "writing":      "你是写作专家，擅长各类文体写作、编辑润色和创意表达。",
        "research":     "你具备严谨的研究分析能力，善于收集整理信息、批判性思考。",
        "finance":      "你熟悉金融和投资领域，能提供理性、专业的财务分析建议（非投资建议）。",
        "legal":        "你了解法律基础知识，能协助理解法律文本和条款（非法律建议，如需具体意见请咨询律师）。",
        "medical":      "你了解医疗和健康知识，能提供一般性健康信息（非医疗建议，如有疑问请就医）。",
        "education":    "你是有耐心的教育者，善于用恰当的方式解释知识，引导学习理解。",
        "marketing":    "你熟悉营销和品牌建设，能帮助制定推广策略和创作吸引人的文案。",
        "productivity": "你专注于工作效率和流程优化，帮助用户管理时间、任务和项目。",
        "lifestyle":    "你关注生活品质和个人成长，提供实用的生活建议和情感支持。",
        "general":      "你是一个全能助手，善于处理各种类型的问题和任务。",
    }

    @classmethod
    def synthesize(cls, profile: StyleProfile, name: str, description: str = "") -> Tuple[str, str]:
        """
        根据 StyleProfile 合成 (system_prompt_template, intent_description)。
        返回 (prompt, intent_desc) 元组。
        """
        blocks: List[str] = []

        # 1. 身份设定
        domain_ctx = cls._DOMAIN_CONTEXTS.get(profile.domain, cls._DOMAIN_CONTEXTS["general"])
        if description:
            blocks.append(f"你是「{name}」。{description}")
        else:
            blocks.append(f"你是「{name}」，{domain_ctx}")

        blocks.append("")  # 空行分隔

        # 2. 行为约束块（根据维度评分选择档位）
        def pick(blocks_dict: Dict, score: float) -> str:
            if score >= 0.7:
                return blocks_dict["high"]
            elif score >= 0.35:
                return blocks_dict["mid"]
            else:
                return blocks_dict["low"]

        behavior_header = "## 行为规范\n"
        behavior_items: List[str] = []

        formality_block = pick(cls._FORMALITY_BLOCKS, profile.formality)
        behavior_items.append(f"**语气风格**：{formality_block}")

        verbosity_block = pick(cls._VERBOSITY_BLOCKS, profile.verbosity)
        behavior_items.append(f"**回答详度**：{verbosity_block}")

        if profile.empathy >= 0.35:
            empathy_block = pick(cls._EMPATHY_BLOCKS, profile.empathy)
            behavior_items.append(f"**共情方式**：{empathy_block}")

        structure_block = pick(cls._STRUCTURE_BLOCKS, profile.structure)
        behavior_items.append(f"**内容结构**：{structure_block}")

        if profile.creativity >= 0.5:
            creativity_block = pick(cls._CREATIVITY_BLOCKS, profile.creativity)
            behavior_items.append(f"**创意表达**：{creativity_block}")

        if profile.technicality >= 0.6 or profile.technicality <= 0.3:
            tech_block = pick(cls._TECHNICALITY_BLOCKS, profile.technicality)
            behavior_items.append(f"**技术深度**：{tech_block}")

        if profile.positivity >= 0.6:
            pos_block = pick(cls._POSITIVITY_BLOCKS, profile.positivity)
            behavior_items.append(f"**积极态度**：{pos_block}")

        if profile.proactivity >= 0.6:
            pro_block = pick(cls._PROACTIVITY_BLOCKS, profile.proactivity)
            behavior_items.append(f"**主动建议**：{pro_block}")

        if profile.humor >= 0.55:
            humor_block = pick(cls._HUMOR_BLOCKS, profile.humor)
            behavior_items.append(f"**幽默风格**：{humor_block}")

        blocks.append(behavior_header + "\n\n".join(f"- {item}" for item in behavior_items))

        # 3. 输入变量提示（模板中的占位符支持）
        blocks.append(
            "\n## 用户输入\n用户输入：{input}\n\n请根据以上风格要求和行为规范，给出高质量的回应。"
        )

        system_prompt = "\n".join(blocks)

        # 4. 生成 intent_description
        formality_desc = "正式" if profile.formality > 0.65 else ("随意" if profile.formality < 0.35 else "自然")
        verbosity_desc = "详细" if profile.verbosity > 0.65 else ("简短" if profile.verbosity < 0.35 else "适中")
        empathy_desc = "温暖共情" if profile.empathy > 0.65 else ("理性客观" if profile.empathy < 0.35 else "平衡")
        intent_desc = (
            f"用户需要以 {formality_desc}、{verbosity_desc}、{empathy_desc} 的方式回应的场景。"
        )
        if description:
            intent_desc = f"{description}。" + intent_desc

        return system_prompt, intent_desc


# ══════════════════════════════════════════════════════════════════
# Skill 打包器（.kotosk 格式）
# ══════════════════════════════════════════════════════════════════

class SkillPackager:
    """
    将一个或多个 SkillDefinition 打包为 .kotosk（zip 格式）文件，
    或从 .kotosk 文件中解包。

    .kotosk 文件结构:
      manifest.json   ← 包元信息（版本、作者、依赖等）
      skills/
        {id}.json     ← 每个 Skill 的完整定义
      README.md       ← 可选说明文档
    """

    KOTOSK_VERSION = "1.0"

    @classmethod
    def pack(
        cls,
        skills: List[Any],       # List[SkillDefinition]
        output_path: str,
        pack_name: str = "",
        author: str = "user",
        description: str = "",
        readme: str = "",
    ) -> str:
        """
        打包多个 Skill 为 .kotosk 文件。
        返回输出文件路径。
        """
        import zipfile
        import tempfile
        from datetime import datetime, timezone

        if not output_path.endswith(".kotosk"):
            output_path += ".kotosk"

        manifest = {
            "kotosk_version": cls.KOTOSK_VERSION,
            "pack_name": pack_name or (skills[0].name if skills else "skill_pack"),
            "author": author,
            "description": description,
            "skill_count": len(skills),
            "skill_ids": [s.id for s in skills],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for skill in skills:
                zf.writestr(f"skills/{skill.id}.json", json.dumps(skill.to_dict(), ensure_ascii=False, indent=2))
            if readme:
                zf.writestr("README.md", readme)

        logger.info(f"[SkillPackager] 已打包 {len(skills)} 个 Skill → {output_path}")
        return output_path

    @classmethod
    def unpack(cls, kotosk_path: str) -> Tuple[Dict, List[Any]]:
        """
        从 .kotosk 文件解包，返回 (manifest, [SkillDefinition, ...])。
        """
        import zipfile

        def _get_schema():
            from app.core.skills.skill_schema import SkillDefinition
            return SkillDefinition

        SkillDefinition = _get_schema()

        if not os.path.exists(kotosk_path):
            raise FileNotFoundError(f"文件不存在: {kotosk_path}")

        manifest = {}
        skills = []

        with zipfile.ZipFile(kotosk_path, "r") as zf:
            if "manifest.json" in zf.namelist():
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

            for name in zf.namelist():
                if name.startswith("skills/") and name.endswith(".json"):
                    raw = json.loads(zf.read(name).decode("utf-8"))
                    try:
                        skill = SkillDefinition.from_dict(raw)
                        skills.append(skill)
                    except Exception as e:
                        logger.warning(f"[SkillPackager] 解析 {name} 失败: {e}")

        logger.info(f"[SkillPackager] 解包完成: {len(skills)} 个 Skill from {kotosk_path}")
        return manifest, skills

    @classmethod
    def get_manifest(cls, kotosk_path: str) -> Dict:
        """只读取 manifest，不解析 skill 定义（用于快速预览）"""
        import zipfile
        with zipfile.ZipFile(kotosk_path, "r") as zf:
            if "manifest.json" in zf.namelist():
                return json.loads(zf.read("manifest.json").decode("utf-8"))
        return {}


# ══════════════════════════════════════════════════════════════════
# 主入口：SkillAutoBuilder
# ══════════════════════════════════════════════════════════════════

class SkillAutoBuilder:
    """
    一站式 Skill 自动构建器。

    支持三种构建模式：
      1. from_style_description  — 从自然语言描述自动推断风格
      2. from_conversation_history — 从历史对话提取 AI 风格
      3. from_style_config       — 手动调参精细控制
    """

    @classmethod
    def from_style_description(
        cls,
        name: str,
        description: str,
        icon: str = "🎭",
        category: str = "style",
        author: str = "user",
        tags: Optional[List[str]] = None,
        enabled: bool = False,
        personalize: bool = False,
        personalization_context: Optional[Dict[str, Any]] = None,
    ):
        """
        从自然语言描述自动生成 SkillDefinition（本地规则引擎，无需 API）。

        Args:
            name:        技能名称，如「暖心闺蜜」
            description: 风格描述，如「像闺蜜一样聊天，温柔、感同身受」
            icon:        emoji 图标
            category:    skill 分类（style/behavior/domain/custom）
            author:      作者标识
            tags:        搜索标签
            enabled:     是否默认启用

        Returns:
            SkillDefinition
        """
        from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec

        context = personalization_context or {}
        if personalize and not context:
            context = cls.load_personalization_context()

        effective_description = description
        if context:
            effective_description = cls._build_effective_description(description, context)

        profile = StyleAnalyzer.analyze_text(effective_description)
        if context:
            profile = cls._apply_profile_bias(profile, context)

        prompt, intent_desc = PromptSynthesizer.synthesize(profile, name, effective_description)

        skill_id = _make_skill_id(name)
        auto_tags = tags or [profile.domain, category]

        return SkillDefinition(
            id=skill_id,
            name=name,
            icon=icon,
            category=category,
            description=description,
            intent_description=intent_desc,
            system_prompt_template=prompt,
            prompt=prompt,
            input_variables=[
                InputVariable(
                    name="input",
                    description="用户输入的内容",
                    required=True,
                    example="你好，今天天气真不错",
                )
            ],
            output_spec=OutputSpec(
                format="any",
                description=f"以「{name}」风格回答",
            ),
            task_types=["CHAT"],
            enabled=enabled,
            version="1.0.0",
            author=author,
            tags=auto_tags,
        )

    @classmethod
    def from_ai_description(
        cls,
        name: str,
        description: str,
        icon: str = "🎭",
        category: str = "style",
        author: str = "user",
        tags: Optional[List[str]] = None,
        enabled: bool = False,
        model: str = "gemini-3-flash-preview",
        personalize: bool = False,
        personalization_context: Optional[Dict[str, Any]] = None,
    ):
        """
        使用 Gemini AI 生成高质量 SkillDefinition。
        相比规则引擎，能理解复杂的语义意图，生成更精准的 system_prompt_template。
        若 API 不可用或调用失败，自动降级到 from_style_description() 规则引擎。

        Args:
            name:        技能名称
            description: 用户的自然语言描述
            icon:        emoji 图标
            category:    Skill 分类
            author:      作者
            tags:        搜索标签
            enabled:     是否默认启用
            model:       Gemini 模型名称

        Returns:
            SkillDefinition（AI 生成或规则降级）
        """
        from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec

        skill_id = _make_skill_id(name)
        auto_tags = tags or [category]

        context = personalization_context or {}
        if personalize and not context:
            context = cls.load_personalization_context()

        effective_description = description
        if context:
            effective_description = cls._build_effective_description(description, context)

        # 尝试 AI 生成
        ai_prompt = cls._generate_prompt_with_ai(name, effective_description, model)

        if ai_prompt:
            # AI 生成成功：同时用规则引擎获取辅助信息
            profile = StyleAnalyzer.analyze_text(effective_description)
            if context:
                profile = cls._apply_profile_bias(profile, context)
            _, intent_desc = PromptSynthesizer.synthesize(profile, name, effective_description)

            return SkillDefinition(
                id=skill_id,
                name=name,
                icon=icon,
                category=category,
                description=description,
                intent_description=intent_desc,
                system_prompt_template=ai_prompt,
                prompt=ai_prompt,
                input_variables=[
                    InputVariable(name="input", description="用户输入的内容", required=True)
                ],
                output_spec=OutputSpec(format="any", description=f"以「{name}」风格回答"),
                task_types=["CHAT"],
                enabled=enabled,
                version="1.0.0",
                author=author,
                tags=auto_tags,
                examples=[],
            )
        else:
            # 降级到规则引擎
            logger.info(f"[SkillAutoBuilder] AI 生成失败，降级为规则引擎生成: {name}")
            return cls.from_style_description(
                name=name,
                description=description,
                icon=icon,
                category=category,
                author=author,
                tags=tags,
                enabled=enabled,
                personalize=personalize,
                personalization_context=context,
            )

    @classmethod
    def _generate_prompt_with_ai(cls, name: str, description: str, model: str) -> Optional[str]:
        """
        调用 Gemini API 生成 system_prompt_template。
        成功返回 prompt 字符串，失败返回 None。
        """
        try:
            from app.core.llm.gemini import GeminiProvider

            client = GeminiProvider()

            meta_prompt = f"""你是一个专业的 AI Skill 设计师。
用户想创建一个名为「{name}」的 AI 技能，使用场景描述如下：

"{description}"

请为这个技能设计一段高质量的 System Prompt，要求：
1. 以「你是「{name}」」开头，明确 AI 的角色定位
2. 包含「## 行为规范」标题，下面用要点列表列出 4-6 条具体行为指南
3. 行为指南要具体、可执行，不要空洞，要针对上述描述量身定制
4. 结尾说明输入格式：「## 用户输入\\n用户输入：{{input}}\\n\\n请...」
5. 总长度 200-400 字
6. 只输出 System Prompt 纯文本，不要 JSON，不要解释，不要代码块包裹

直接输出 System Prompt："""

            result = client.generate_content(
                prompt=meta_prompt,
                model=model,
                temperature=0.7,
                max_tokens=1024,
            )

            if isinstance(result, dict) and "text" in result:
                return result["text"].strip()
            elif isinstance(result, str):
                return result.strip()
            return None

        except Exception as e:
            logger.debug(f"[SkillAutoBuilder] Gemini 生成失败: {e}")
            return None

    @classmethod
    def from_conversation_history(
        cls,
        session_id: str,
        name: str,
        description: str = "",
        icon: str = "💬",
        category: str = "style",
        author: str = "user",
        max_turns: int = 10,
    ):
        """
        从历史对话文件中提取 AI 的交流风格，生成 SkillDefinition。

        Args:
            session_id: chats/{session_id}.json 中的会话 ID
            name:       技能名称
            description:额外描述（会与提取的风格合并）
            max_turns:  分析最近 N 轮对话

        Returns:
            SkillDefinition
        """
        # 加载对话历史
        chats_dir = str(_BASE_DIR / "chats")
        chat_file = os.path.join(chats_dir, f"{session_id}.json")

        if not os.path.exists(chat_file):
            raise ValueError(f"未找到 session '{session_id}' 的对话记录")

        with open(chat_file, "r", encoding="utf-8") as f:
            raw = json.load(f)

        history = raw if isinstance(raw, list) else raw.get("history", [])
        turns = _normalize_turns(history)[-max_turns * 2:]

        profile = StyleAnalyzer.analyze_conversation(turns)
        if description:
            # 合并描述中的风格信号
            desc_profile = StyleAnalyzer.analyze_text(description)
            # 取两者加权平均（对话权重更高）
            for dim in STYLE_DIMENSIONS:
                setattr(profile, dim, round(
                    getattr(profile, dim) * 0.6 + getattr(desc_profile, dim) * 0.4, 2
                ))
            profile.domain = desc_profile.domain if desc_profile.domain != "general" else profile.domain

        final_desc = description or f"从会话 {session_id} 中提取的交流风格"
        prompt, intent_desc = PromptSynthesizer.synthesize(profile, name, final_desc)

        from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec

        return SkillDefinition(
            id=_make_skill_id(name),
            name=name,
            icon=icon,
            category=category,
            description=final_desc,
            intent_description=intent_desc,
            system_prompt_template=prompt,
            prompt=prompt,
            input_variables=[
                InputVariable(name="input", description="用户输入", required=True)
            ],
            output_spec=OutputSpec(format="any"),
            task_types=["CHAT"],
            enabled=False,
            version="1.0.0",
            author=author,
            tags=[profile.domain, "auto-extracted"],
        )

    @classmethod
    def from_style_config(
        cls,
        name: str,
        formality: float = 0.5,
        verbosity: float = 0.5,
        empathy: float = 0.5,
        structure: float = 0.5,
        creativity: float = 0.3,
        technicality: float = 0.3,
        positivity: float = 0.6,
        proactivity: float = 0.4,
        humor: float = 0.2,
        domain: str = "general",
        description: str = "",
        icon: str = "🎛️",
        category: str = "style",
        author: str = "user",
        enabled: bool = False,
        personalize: bool = False,
        personalization_context: Optional[Dict[str, Any]] = None,
    ):
        """
        通过精细旋钮配置生成 SkillDefinition。
        所有维度参数范围 0.0 ~ 1.0。
        """
        from app.core.skills.skill_schema import SkillDefinition, InputVariable, OutputSpec

        profile = StyleProfile(
            formality=formality,
            verbosity=verbosity,
            empathy=empathy,
            structure=structure,
            creativity=creativity,
            technicality=technicality,
            positivity=positivity,
            proactivity=proactivity,
            humor=humor,
            domain=domain,
        )

        context = personalization_context or {}
        if personalize and not context:
            context = cls.load_personalization_context()

        effective_description = description
        if context:
            if description:
                effective_description = cls._build_effective_description(description, context)
                desc_profile = StyleAnalyzer.analyze_text(effective_description)
                for dim in STYLE_DIMENSIONS:
                    cur = getattr(profile, dim)
                    setattr(profile, dim, round(cur * 0.75 + getattr(desc_profile, dim) * 0.25, 2))
            profile = cls._apply_profile_bias(profile, context)

        prompt, intent_desc = PromptSynthesizer.synthesize(profile, name, effective_description)

        return SkillDefinition(
            id=_make_skill_id(name),
            name=name,
            icon=icon,
            category=category,
            description=description or f"自定义风格技能：{name}",
            intent_description=intent_desc,
            system_prompt_template=prompt,
            prompt=prompt,
            input_variables=[
                InputVariable(name="input", description="用户输入", required=True)
            ],
            output_spec=OutputSpec(format="any"),
            task_types=["CHAT"],
            enabled=enabled,
            version="1.0.0",
            author=author,
            tags=[domain, category],
        )

    @classmethod
    def preview_prompt(
        cls,
        name: str,
        description: str,
        formality: float = 0.5,
        verbosity: float = 0.5,
        empathy: float = 0.5,
        structure: float = 0.5,
        creativity: float = 0.3,
        positivity: float = 0.6,
        proactivity: float = 0.4,
        humor: float = 0.2,
        domain: str = "general",
        personalize: bool = False,
        personalization_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        快速预览生成的 Prompt（不创建 SkillDefinition，用于 UI 实时预览）。
        """
        profile = StyleProfile(
            formality=formality,
            verbosity=verbosity,
            empathy=empathy,
            structure=structure,
            creativity=creativity,
            positivity=positivity,
            proactivity=proactivity,
            humor=humor,
            domain=domain,
        )
        context = personalization_context or {}
        if personalize and not context:
            context = cls.load_personalization_context()

        effective_description = description
        if context and description:
            effective_description = cls._build_effective_description(description, context)

        # 若传入 description，用于补充分析
        if effective_description:
            desc_profile = StyleAnalyzer.analyze_text(effective_description)
            for dim in STYLE_DIMENSIONS:
                cur = getattr(profile, dim)
                if cur == 0.5:  # 只替换默认值
                    setattr(profile, dim, getattr(desc_profile, dim))

        if context:
            profile = cls._apply_profile_bias(profile, context)

        prompt, intent_desc = PromptSynthesizer.synthesize(profile, name, effective_description)
        return {
            "system_prompt": prompt,
            "intent_description": intent_desc,
            "style_profile": profile.to_dict(),
            "suggested_id": _make_skill_id(name),
        }

    @classmethod
    def load_personalization_context(cls, max_memories: int = 8) -> Dict[str, Any]:
        """
        读取本地 user_profile + memory，供 Skill 自动构建做轻量个性化。
        失败时返回空上下文，不抛异常。
        """
        context: Dict[str, Any] = {
            "communication_style": {},
            "technical_background": {},
            "preferences": {},
            "memory_hints": [],
        }
        try:
            profile_path = _BASE_DIR / "config" / "user_profile.json"
            if profile_path.exists():
                profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
                context["communication_style"] = profile_data.get("communication_style", {}) or {}
                context["technical_background"] = profile_data.get("technical_background", {}) or {}
                context["preferences"] = profile_data.get("preferences", {}) or {}
        except Exception as e:
            logger.debug(f"[SkillAutoBuilder] 读取 user_profile 失败: {e}")

        try:
            memory_path = _BASE_DIR / "config" / "memory.json"
            if memory_path.exists():
                raw = json.loads(memory_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    pref_items = [
                        str(it.get("content", "")).strip()
                        for it in raw
                        if str(it.get("category", "")).lower() in (
                            "preference", "user_preference", "user_profile", "project_info"
                        ) and str(it.get("content", "")).strip()
                    ]
                    context["memory_hints"] = pref_items[:max_memories]
        except Exception as e:
            logger.debug(f"[SkillAutoBuilder] 读取 memory 失败: {e}")

        return context

    @classmethod
    def _build_effective_description(cls, description: str, context: Dict[str, Any]) -> str:
        """将用户描述与本地偏好上下文拼接，增强风格识别命中率。"""
        extra_parts: List[str] = []

        comm = context.get("communication_style") or {}
        tech = context.get("technical_background") or {}
        prefs = context.get("preferences") or {}
        memory_hints = context.get("memory_hints") or []

        if comm:
            detail = comm.get("preferred_detail_level")
            formality = comm.get("formality")
            code_style = comm.get("code_style")
            preferred_lang = comm.get("preferred_language")
            emoji_usage = comm.get("emoji_usage")
            extra_parts.append(
                f"用户沟通偏好：详略={detail or 'moderate'}，语气={formality or 'natural'}，"
                f"代码风格={code_style or 'normal'}，语言={preferred_lang or 'zh-CN'}，"
                f"emoji={'yes' if emoji_usage else 'no'}。"
            )

        if tech:
            langs = tech.get("programming_languages") or []
            experience = tech.get("experience_level") or ""
            domains = tech.get("domains") or []
            if langs or experience or domains:
                extra_parts.append(
                    f"用户技术背景：经验={experience or 'unknown'}，语言={','.join(langs[:3]) or 'n/a'}，"
                    f"领域={','.join(domains[:3]) or 'general'}。"
                )

        likes = prefs.get("likes") or []
        dislikes = prefs.get("dislikes") or []
        habits = prefs.get("habits") or []
        if likes or dislikes or habits:
            extra_parts.append(
                f"用户偏好：喜欢={','.join(likes[:3]) or 'n/a'}；不喜欢={','.join(dislikes[:3]) or 'n/a'}；"
                f"习惯={','.join(habits[:3]) or 'n/a'}。"
            )

        if memory_hints:
            extra_parts.append("历史记忆偏好：" + "；".join(memory_hints[:5]))

        if not extra_parts:
            return description
        return description + "\n\n" + "\n".join(extra_parts)

    @classmethod
    def _apply_profile_bias(cls, profile: StyleProfile, context: Dict[str, Any]) -> StyleProfile:
        """根据 user_profile 对 StyleProfile 做温和偏置，避免覆盖用户显式输入。"""
        comm = context.get("communication_style") or {}

        detail = str(comm.get("preferred_detail_level", "")).lower()
        if detail in ("concise", "brief", "short"):
            profile.verbosity = round(profile.verbosity * 0.6, 2)
            profile.conciseness = min(1.0, round(profile.conciseness * 0.7 + 0.3, 2))
        elif detail in ("detailed", "deep", "comprehensive"):
            profile.verbosity = min(1.0, round(profile.verbosity * 0.7 + 0.3, 2))
            profile.conciseness = round(profile.conciseness * 0.7, 2)

        formality = str(comm.get("formality", "")).lower()
        if formality in ("casual", "relaxed"):
            profile.formality = round(profile.formality * 0.6, 2)
        elif formality in ("formal", "professional"):
            profile.formality = min(1.0, round(profile.formality * 0.7 + 0.3, 2))

        emoji_usage = comm.get("emoji_usage", None)
        if emoji_usage is True:
            profile.humor = min(1.0, round(profile.humor * 0.7 + 0.2, 2))
        elif emoji_usage is False:
            profile.humor = round(profile.humor * 0.6, 2)

        code_style = str(comm.get("code_style", "")).lower()
        if code_style in ("concise", "minimal"):
            profile.conciseness = min(1.0, round(profile.conciseness * 0.7 + 0.25, 2))
            profile.verbosity = round(profile.verbosity * 0.7, 2)

        return profile


# ══════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════

def _make_skill_id(name: str) -> str:
    """生成 URL 安全的 skill id"""
    import hashlib
    slug = re.sub(r"[^\w\u4e00-\u9fff]", "_", name.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or f"skill_{hashlib.md5(name.encode()).hexdigest()[:8]}"


def _normalize_turns(history: List[Dict]) -> List[Dict[str, str]]:
    """将对话历史规范化为 [{role, text}, ...] 格式"""
    turns = []
    for entry in history:
        role_raw = entry.get("role", "")
        role = "assistant" if role_raw in ("model", "assistant", "ai") else "user"
        text = entry.get("content") or ""
        if not text and "parts" in entry:
            parts = entry["parts"]
            if isinstance(parts, list):
                text = " ".join(
                    p.get("text", p) if isinstance(p, dict) else str(p)
                    for p in parts
                )
        if text:
            turns.append({"role": role, "text": str(text)})
    return turns

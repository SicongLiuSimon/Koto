# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║      Koto  ─  输出质量验收层 (Output Verification Layer)          ║
╚══════════════════════════════════════════════════════════════════╝

职责
────
云端 LLM（Gemini 等）返回结果后，本模块在本地对输出进行质量验收：

1. 格式验收  — 对照 Skill.OutputSpec 检查格式是否符合约定
2. 完整性验收 — 检测截断、空输出、重复、模型拒绝等异常
3. 安全验收  — 检测响应中是否意外回显了 PII 占位符或内部 Prompt
4. 重试决策  — 返回结构化结果，调用方决定是否触发重试/格式化

验收结果
────────
    PASS   : 通过，可直接返回用户
    WARN   : 通过但有轻微问题（如过短），记录日志
    REFORMAT: 内容正确但格式不符，触发本地格式化
    RETRY  : 质量不合格，需要重新请求云端
    BLOCK  : 严重安全问题（发现内部 Prompt 泄露等），必须拦截

用法
────
    from app.core.security.output_validator import OutputValidator, ValidationResult

    result: ValidationResult = OutputValidator.validate(
        text=llm_response,
        skill_id="summarize_doc",        # 可选，有 Skill 时做格式验收
        original_prompt=user_input,       # 可选，用于检测 prompt 注入回显
    )

    if result.action == "PASS":
        return result.text          # 直接用
    elif result.action == "REFORMAT":
        return result.text          # 已被本地自动格式化
    elif result.action == "RETRY":
        # 触发重试逻辑...
        pass
    elif result.action == "BLOCK":
        return "⚠️ 响应包含异常内容，已拦截"
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 结果类型
# ══════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """
    验收结果。

    Attributes:
        action       : "PASS" | "WARN" | "REFORMAT" | "RETRY" | "BLOCK"
        text         : 验收（和可能格式化）后的最终文本
        original_text: LLM 返回的原始文本
        reasons      : 触发该 action 的原因列表
        skill_id     : 对应的 Skill ID（如果有）
    """
    action: str          # PASS | WARN | REFORMAT | RETRY | BLOCK
    text: str
    original_text: str
    reasons: List[str] = field(default_factory=list)
    skill_id: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.action in ("PASS", "WARN", "REFORMAT")

    @property
    def needs_retry(self) -> bool:
        return self.action == "RETRY"

    @property
    def is_blocked(self) -> bool:
        return self.action == "BLOCK"


# ══════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════

# 模型拒绝/失败的典型开头词（多语言）
_REFUSAL_PATTERNS = [
    r"^i (cannot|can't|am unable to|won't)",
    r"^我(不能|无法|不可以|拒绝|无权)",
    r"^抱歉.*我(无法|不能|不允许)",
    r"^sorry.*i (cannot|can't)",
    r"^as an ai.*i (cannot|can't|am not)",
    r"^作为.*ai.*我(无法|不能)",
]
_REFUSAL_RE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _REFUSAL_PATTERNS]

# 截断指示词（出现在文末，说明内容可能被 token limit 截断）
_TRUNCATION_ENDINGS = [
    "...", "…", "（未完）", "（待续）", "to be continued",
    "（下文省略", "（以下略", "\n—",
]

# 内部 Prompt 泄露检测词（如果 LLM 把 system prompt 回显了）
_INTERNAL_LEAK_PATTERNS = [
    r"<<[^>]{2,30}>>",          # PII 占位符未被还原
    r"\[SYSTEM\]",
    r"system_instruction",
    r"System Prompt:",
    r"\[INST\]",                 # llama 格式
    r"<\|im_start\|>",           # chatml 格式
]
_LEAK_RE = [re.compile(p, re.IGNORECASE) for p in _INTERNAL_LEAK_PATTERNS]

# 异常重复检测：同一句话重复超过 N 次
_REPETITION_THRESHOLD = 4


# ══════════════════════════════════════════════════════════════════
# 格式化器（REFORMAT 时使用）
# ══════════════════════════════════════════════════════════════════

class _Formatter:
    """本地轻量格式化器，将纯文本转换为要求格式"""

    @staticmethod
    def to_markdown_list(text: str) -> str:
        """将纯文本段落转换为 Markdown 无序列表"""
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        return "\n".join(f"- {line}" if not line.startswith(("-", "*", "#")) else line
                         for line in lines)

    @staticmethod
    def to_markdown_table(text: str) -> str:
        """
        尝试将 CSV-like 文本转换为 Markdown 表格。
        如果无法解析，在原文上加简单包裹。
        """
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        if len(lines) < 2:
            return text

        # 检测是否已经是 | 分隔
        if "|" in lines[0]:
            return text

        # 尝试逗号分隔
        rows = [l.split(",") for l in lines]
        max_cols = max(len(r) for r in rows)

        header = rows[0] + [""] * (max_cols - len(rows[0]))
        table = "| " + " | ".join(c.strip() for c in header) + " |\n"
        table += "| " + " | ".join(["---"] * max_cols) + " |\n"
        for row in rows[1:]:
            padded = row + [""] * (max_cols - len(row))
            table += "| " + " | ".join(c.strip() for c in padded) + " |\n"
        return table


# ══════════════════════════════════════════════════════════════════
# 核心：OutputValidator
# ══════════════════════════════════════════════════════════════════

class OutputValidator:
    """
    静态验收器。所有方法均为类方法，无需实例化。
    """

    @classmethod
    def validate(
        cls,
        text: str,
        skill_id: Optional[str] = None,
        original_prompt: Optional[str] = None,
    ) -> ValidationResult:
        """
        对 LLM 输出文本进行多维度验收。

        Args:
            text           : LLM 返回的原始文本
            skill_id       : 对应的 Skill ID（用于查 OutputSpec），可为 None
            original_prompt: 用户的原始请求（用于检测 prompt 回显），可为 None

        Returns:
            ValidationResult 对象
        """
        if not text or not text.strip():
            return ValidationResult(
                action="RETRY",
                text=text or "",
                original_text=text or "",
                reasons=["LLM 返回空响应"],
                skill_id=skill_id,
            )

        reasons: List[str] = []
        current_text = text

        # ── 1. 安全检测：内部信息泄露 ──────────────────────────────
        leak_reasons = cls._check_leaks(text, original_prompt)
        if leak_reasons:
            logger.warning(f"[OutputValidator] ⚠️ 检测到潜在泄露: {leak_reasons}")
            return ValidationResult(
                action="BLOCK",
                text="⚠️ 响应包含异常内容，已被安全护栏拦截。",
                original_text=text,
                reasons=leak_reasons,
                skill_id=skill_id,
            )

        # ── 2. 完整性检测：模型拒绝 ────────────────────────────────
        if cls._is_refusal(text):
            reasons.append("模型拒绝执行请求")
            return ValidationResult(
                action="RETRY",
                text=text,
                original_text=text,
                reasons=reasons,
                skill_id=skill_id,
            )

        # ── 3. 完整性检测：截断 ─────────────────────────────────────
        if cls._is_truncated(text):
            reasons.append("响应可能被截断（末尾有截断标志）")
            # 截断通常是 token limit 问题，WARN 而非 RETRY（可能用户不在意）
            return ValidationResult(
                action="WARN",
                text=text,
                original_text=text,
                reasons=reasons,
                skill_id=skill_id,
            )

        # ── 4. 完整性检测：异常重复 ─────────────────────────────────
        repetition_reason = cls._check_repetition(text)
        if repetition_reason:
            reasons.append(repetition_reason)
            return ValidationResult(
                action="RETRY",
                text=text,
                original_text=text,
                reasons=reasons,
                skill_id=skill_id,
            )

        # ── 5. Skill OutputSpec 格式验收 ───────────────────────────
        if skill_id:
            format_result = cls._validate_skill_spec(text, skill_id)
            if format_result:
                action, formatted_text, spec_reason = format_result
                reasons.append(spec_reason)
                if action == "REFORMAT":
                    logger.info(f"[OutputValidator] 🔧 自动格式化 [{skill_id}]: {spec_reason}")
                return ValidationResult(
                    action=action,
                    text=formatted_text,
                    original_text=text,
                    reasons=reasons,
                    skill_id=skill_id,
                )

        # ── 6. 通过 ─────────────────────────────────────────────────
        return ValidationResult(
            action="PASS",
            text=current_text,
            original_text=text,
            reasons=[],
            skill_id=skill_id,
        )

    # ── 私有检测方法 ──────────────────────────────────────────────

    @classmethod
    def _check_leaks(cls, text: str, original_prompt: Optional[str]) -> List[str]:
        """检测内部信息泄露"""
        reasons = []
        for pattern in _LEAK_RE:
            if pattern.search(text):
                reasons.append(f"响应包含内部标记: {pattern.pattern}")
        return reasons

    @classmethod
    def _is_refusal(cls, text: str) -> bool:
        """检测模型拒绝响应"""
        first_100 = text[:100].strip()
        return any(p.match(first_100) for p in _REFUSAL_RE)

    @classmethod
    def _is_truncated(cls, text: str) -> bool:
        """检测响应是否被截断"""
        stripped = text.rstrip()
        return any(stripped.endswith(ending) for ending in _TRUNCATION_ENDINGS)

    @classmethod
    def _check_repetition(cls, text: str) -> Optional[str]:
        """检测异常重复（同一行重复超过阈值）"""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) < _REPETITION_THRESHOLD:
            return None
        # 检查最常见行的重复次数
        from collections import Counter
        counts = Counter(lines)
        most_common, count = counts.most_common(1)[0]
        if count >= _REPETITION_THRESHOLD:
            return f"检测到异常重复内容（'{most_common[:30]}...' 重复 {count} 次）"
        return None

    @classmethod
    def _validate_skill_spec(
        cls, text: str, skill_id: str
    ) -> Optional[tuple]:
        """
        使用 SkillManager 中该 Skill 的 OutputSpec 验收，
        返回 (action, text, reason) 或 None（表示通过）
        """
        try:
            from app.core.skills.skill_manager import SkillManager
            passed, reason = SkillManager.validate_output(skill_id, text)
            if passed:
                return None

            # 验收失败：尝试本地自动格式化
            skill_def = SkillManager.get_definition(skill_id)
            if skill_def:
                fmt = skill_def.output_spec.format
                fmt_str = fmt.value if hasattr(fmt, "value") else fmt

                if fmt_str == "table" and "|" not in text:
                    reformatted = _Formatter.to_markdown_table(text)
                    return ("REFORMAT", reformatted, f"自动修复格式 ({reason})")

                if fmt_str == "markdown" and not text.strip().startswith("#"):
                    # 尝试添加 markdown 列表格式
                    reformatted = _Formatter.to_markdown_list(text)
                    return ("REFORMAT", reformatted, f"自动修复格式 ({reason})")

            # 无法自动修复，要求重试
            return ("RETRY", text, reason)

        except Exception as e:
            logger.warning(f"[OutputValidator] Skill 验收异常: {e}")
            return None

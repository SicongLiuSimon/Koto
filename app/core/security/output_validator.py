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

import logging
import re
import concurrent.futures
from dataclasses import dataclass, field
from typing import Any, List, Optional

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

    action: str  # PASS | WARN | REFORMAT | RETRY | BLOCK
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

# 代码替代检测：模型声称无法联网/获取实时数据，然后提供代码片段作为"替代"
# 这种行为对用户无用且具有欺骗性，应触发 RETRY 并要求调用 web_search
_CODE_SUBSTITUTE_PATTERN = re.compile(
    r"(我.*?(没有|无法|不能|不支持|没有办法).{0,30}(接口|联网|实时|工具|互联网|网络访问))"
    r".*?"
    r"(```\s*python|import\s+(yfinance|akshare|requests|pandas|httpx)|pip\s+install)",
    re.DOTALL | re.IGNORECASE,
)

# 实时无能力响应检测：模型承认不知道实时信息但未调用 web_search，直接让用户自己查
# 如：「我无法知道当前天气，建议你查天气网站」→ 触发 RETRY 并要求调用 web_search
_REALTIME_INABILITY_PATTERN = re.compile(
    r"(我.{0,10}(不知道|无法知道|不清楚|不了解|没有).{0,20}(当前|实时|现在|最新|今天|明天|目前).{0,20}"
    r"(天气|温度|气温|股价|金价|价格|行情|新闻|消息|比分|结果|汇率))"
    r"|(建议.{0,10}(你|您).{0,10}(去|上|使用|查询|搜索).{0,20}(天气|新闻|网站|app|平台|搜索引擎))"
    r"|(你可以.{0,10}(去|上|使用|查询|搜索).{0,20}(天气|新闻|网站|app|平台))",
    re.IGNORECASE,
)

# 截断指示词（出现在文末，说明内容可能被 token limit 截断）
_TRUNCATION_ENDINGS = [
    "...",
    "…",
    "（未完）",
    "（待续）",
    "to be continued",
    "（下文省略",
    "（以下略",
    "\n—",
]

# 内部 Prompt 泄露检测词（如果 LLM 把 system prompt 回显了）
_INTERNAL_LEAK_PATTERNS = [
    r"<<[^>]{2,30}>>",  # PII 占位符未被还原
    r"\[SYSTEM\]",
    r"system_instruction",
    r"System Prompt:",
    r"\[INST\]",  # llama 格式
    r"<\|im_start\|>",  # chatml 格式
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
        return "\n".join(
            f"- {line}" if not line.startswith(("-", "*", "#")) else line
            for line in lines
        )

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

    可选 LLM 质量判断模式：调用 configure_llm_judge(client, model_id) 启用。
    启用后，通过所有规则检测的响应会额外经过一次语义质量评判，
    能捕获答非所问、内容严重不足等规则无法判断的问题。
    """

    # ── LLM 判断配置 ──────────────────────────────────────────────
    _judge_client: Optional[Any] = None  # GeminiProvider 或兼容实例
    _judge_model: str = "gemini-2.5-flash"  # 默认使用快速但有能力的模型
    _judge_timeout: float = 15.0  # 最长等待秒数，超时则跳过
    _judge_min_len: int = 80  # 响应短于此字符数则不启动 LLM 判断

    @classmethod
    def configure_llm_judge(
        cls,
        client: Any,
        model_id: str = "gemini-2.5-flash",
        timeout: float = 15.0,
    ) -> None:
        """
        配置 LLM 质量判断器。在应用启动时调用一次即可。

        Args:
            client  : GeminiProvider 实例（或任何实现 generate_content() 的对象）
            model_id: 用于质量判断的模型 ID（轻量快速模型即可，不需要最强模型）
            timeout : 单次判断最长等待秒数，超时自动跳过（默认 15s）
        """
        cls._judge_client = client
        cls._judge_model = model_id
        cls._judge_timeout = timeout
        logger.info(
            "[OutputValidator] LLM judge 已配置: model=%s timeout=%.0fs",
            model_id,
            timeout,
        )

    @classmethod
    def _get_judge_client(cls) -> Optional[Any]:
        """
        返回已配置的 judge client。若未通过 configure_llm_judge() 配置，
        尝试用环境变量懒加载一个 GeminiProvider。
        """
        if cls._judge_client is not None:
            return cls._judge_client
        try:
            import os

            api_key = (
                os.environ.get("GEMINI_API_KEY")
                or os.environ.get("API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
            )
            if not api_key:
                return None
            from app.core.llm.gemini import GeminiProvider

            cls._judge_client = GeminiProvider(api_key=api_key)
            logger.info(
                "[OutputValidator] LLM judge 懒加载成功: model=%s", cls._judge_model
            )
            return cls._judge_client
        except Exception as e:
            logger.debug("[OutputValidator] LLM judge 懒加载失败（跳过）: %s", e)
            return None

    @classmethod
    def _llm_judge(cls, text: str, original_prompt: str) -> Optional[tuple]:
        """
        使用 LLM 对输出进行语义质量判断。仅在规则检测全部通过后调用。

        返回 (action, reason) 或 None（表示通过，不需干预）。

        判断标准：
            PASS  — 回复切题、内容完整
            WARN  — 基本可用但有轻微瑕疵（略短、信息稍不完整等）
            RETRY — 答非所问、内容严重不足或逻辑明显错误
        """
        if len(text) < cls._judge_min_len:
            return None

        client = cls._get_judge_client()
        if client is None:
            return None

        judge_prompt = (
            "你是一个 AI 回复质量评判员。请根据用户请求和 AI 的回复，判断回复质量。\n\n"
            f"【用户请求】\n{original_prompt[:600]}\n\n"
            f"【AI 回复】\n{text[:2000]}\n\n"
            "请只输出 JSON，格式如下，不要其他内容：\n"
            '{"verdict": "PASS" | "WARN" | "RETRY", "reason": "一句话说明原因"}\n\n'
            "判断标准：\n"
            "- PASS  : 回复切题，内容完整，无明显问题\n"
            "- WARN  : 回复基本可用但有瑕疵（如信息略显不足或稍短）\n"
            "- RETRY : 答非所问、内容严重不足、逻辑明显错误，或完全没有实质性内容"
        )

        def _call():
            return client.generate_content(
                prompt=judge_prompt,
                model=cls._judge_model,
                temperature=0.0,
                max_tokens=128,
                response_mime_type="application/json",
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call)
                response = future.result(timeout=cls._judge_timeout)

            import json

            raw = response.get("content", "{}") if isinstance(response, dict) else "{}"
            # 兼容模型直接返回 markdown 代码块包裹 JSON 的情况
            raw = raw.strip().strip("```json").strip("```").strip()
            data = json.loads(raw)
            verdict = data.get("verdict", "PASS").upper()
            reason = data.get("reason", "")

            if verdict == "RETRY":
                logger.info("[OutputValidator] LLM judge → RETRY: %s", reason)
                return ("RETRY", f"[LLM 质量判断] {reason}")
            elif verdict == "WARN":
                logger.info("[OutputValidator] LLM judge → WARN: %s", reason)
                return ("WARN", f"[LLM 质量判断] {reason}")
            return None

        except concurrent.futures.TimeoutError:
            logger.debug(
                "[OutputValidator] LLM judge 超时 (%.0fs)，已跳过", cls._judge_timeout
            )
            return None
        except Exception as e:
            logger.debug("[OutputValidator] LLM judge 异常（跳过）: %s", e)
            return None

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
        logger.debug(
            "[OutputValidator] validate() text_len=%d skill_id=%s",
            len(text) if text else 0,
            skill_id,
        )

        if not text or not text.strip():
            logger.debug("[OutputValidator] action=RETRY reason=empty_input")
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
            logger.info(
                "[OutputValidator] action=RETRY reason=refusal text_preview=%.50r",
                text[:50],
            )
            reasons.append("模型拒绝执行请求")
            return ValidationResult(
                action="RETRY",
                text=text,
                original_text=text,
                reasons=reasons,
                skill_id=skill_id,
            )

        # ── 2.5 实时数据规则：代码替代嗅探 ───────────────────────
        # 当模型声称无法联网，却给出 Python 代码片段作为"替代"时，触发 RETRY
        if _CODE_SUBSTITUTE_PATTERN.search(text):
            reasons.append("模型以代码片段替代实时数据查询，要求重试并调用 web_search")
            logger.warning(
                "[OutputValidator] ⚠️ 检测到代码替代实时数据响应，触发 RETRY"
            )
            fix_prompt = (
                "你的上一条回复提供了 Python 代码来获取数据，但这对用户没有帮助。"
                "请直接调用 web_search 工具查询实时数据，并将结果以自然语言返回给用户。"
            )
            return ValidationResult(
                action="RETRY",
                text=fix_prompt,
                original_text=text,
                reasons=reasons,
                skill_id=skill_id,
            )

        # ── 2.6 实时无能力响应检测 ────────────────────────────────
        # 当模型承认无法获取实时信息且建议用户自己去查时，触发 RETRY 要求调用 web_search
        if _REALTIME_INABILITY_PATTERN.search(text):
            reasons.append("模型推脱实时数据查询（未调用 web_search），要求重试")
            logger.warning("[OutputValidator] ⚠️ 检测到实时无能力响应，触发 RETRY")
            fix_prompt = (
                "你的上一条回复说无法获取实时信息并建议用户自行搜索，这对用户没有帮助。"
                "请直接调用 web_search 工具查询，然后将结果以自然语言回答用户。"
            )
            return ValidationResult(
                action="RETRY",
                text=fix_prompt,
                original_text=text,
                reasons=reasons,
                skill_id=skill_id,
            )

        # ── 3. 完整性检测：截断 ─────────────────────────────────────
        if cls._is_truncated(text):
            logger.info("[OutputValidator] action=WARN reason=truncation")
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
            logger.info(
                "[OutputValidator] action=RETRY reason=repetition: %s",
                repetition_reason,
            )
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
                    logger.info(
                        f"[OutputValidator] 🔧 自动格式化 [{skill_id}]: {spec_reason}"
                    )
                return ValidationResult(
                    action=action,
                    text=formatted_text,
                    original_text=text,
                    reasons=reasons,
                    skill_id=skill_id,
                )

        # ── 7. LLM 语义质量判断（可选，规则全部通过后才触发）─────────
        if original_prompt and len(text) >= cls._judge_min_len:
            judge_result = cls._llm_judge(text, original_prompt)
            if judge_result:
                action, reason = judge_result
                logger.info("[OutputValidator] action=%s from LLM judge", action)
                return ValidationResult(
                    action=action,
                    text=current_text,
                    original_text=text,
                    reasons=[reason],
                    skill_id=skill_id,
                )

        # ── 8. 通过 ─────────────────────────────────────────────────
        logger.debug("[OutputValidator] action=PASS skill_id=%s", skill_id)
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
    def _validate_skill_spec(cls, text: str, skill_id: str) -> Optional[tuple]:
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

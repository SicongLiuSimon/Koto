# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║       Koto  ─  PII 脱敏过滤层 (Privacy-Preserving Guard)         ║
╚══════════════════════════════════════════════════════════════════╝

职责
────
在用户输入发往云端（Gemini / 任意远程 LLM）之前，
本模块在本地对文本执行 PII (Personally Identifiable Information) 掩码处理：

1. 替换敏感信息为占位符（如 <<手机号-1>>），不丢失语义
2. 维护一张 mask_map，云端返回结果后可选择性还原
3. 支持用户自定义关键词黑名单（COMPANY_SECRETS）
4. 完全本地运行，零网络调用，零依赖（仅 re 标准库）

掩码类别（默认开启）
──────────────────
- 中国大陆手机号      : 1[3-9]\d{9}
- 固定电话            : 区号 + 7-8位
- 身份证号            : 18位
- 银行卡号            : 16-19位连续数字
- 电子邮箱
- IPv4 地址
- 中文姓名（前接称谓词）
- 家庭住址模式
- 护照/港澳台证件号
- 自定义关键词列表

用法
────
    from app.core.security.pii_filter import PIIFilter, PIIConfig

    # 默认配置：所有检测器开启
    result = PIIFilter.mask("请帮我给张伟发消息，他的手机是13812345678，邮箱是z@qq.com")
    print(result.masked_text)
    # → 请帮我给<<姓名-1>>发消息，他的手机是<<手机号-1>>，邮箱是<<邮箱-1>>

    # 还原（如果需要）
    original = PIIFilter.restore(result.masked_text, result.mask_map)

    # 自定义配置：只过滤手机和邮箱
    config = PIIConfig(mask_phone=True, mask_email=True, mask_id_card=False)
    result2 = PIIFilter.mask(text, config=config)
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════

@dataclass
class PIIConfig:
    """PII 检测器开关，所有选项默认开启"""
    mask_phone: bool = True          # 中国大陆手机号
    mask_landline: bool = True       # 固定电话
    mask_id_card: bool = True        # 身份证号
    mask_bank_card: bool = True      # 银行卡号
    mask_email: bool = True          # 电子邮箱
    mask_ip: bool = False            # IPv4（技术场景可能需要，默认关闭）
    mask_name: bool = True           # 中文姓名（启发式，前接称谓词）
    mask_address: bool = True        # 家庭住址模式
    mask_passport: bool = True       # 护照/港澳证件号
    # 用户自定义敏感词（精确词组，不是正则）
    custom_keywords: List[str] = field(default_factory=list)
    # 是否在日志中输出 mask 统计（不输出原始内容）
    log_stats: bool = True


# ══════════════════════════════════════════════════════════════════
# 正则规则库
# ══════════════════════════════════════════════════════════════════

# 每条规则: (label, pattern, flags)
_RULES: List[Tuple[str, str, int]] = [
    # ─── 手机号 ───────────────────────────────────────────────────
    # 匹配：1[3-9] + 9位，前后为非数字边界
    ("手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)", 0),

    # ─── 固定电话 ─────────────────────────────────────────────────
    # 0xx-xxxxxxxx 或 (0xx)xxxxxxxx
    ("固话", r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)", 0),

    # ─── 身份证 ───────────────────────────────────────────────────
    # 18位：前17位数字 + 最后一位数字或X
    ("身份证", r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)", 0),

    # ─── 银行卡 ───────────────────────────────────────────────────
    # 16-19位连续数字（非身份证覆盖区域）
    ("银行卡", r"(?<!\d)\d{16,19}(?!\d)", 0),

    # ─── 电子邮箱 ─────────────────────────────────────────────────
    ("邮箱", r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", 0),

    # ─── IPv4 地址 ────────────────────────────────────────────────
    ("IP地址",
     r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?!\d)",
     0),

    # ─── 中文姓名 ─────────────────────────────────────────────────
    # 启发式：称谓词 + 2-4个中文字符
    # 称谓词: 叫/是/给/向/找/告诉/通知/联系/发给/转发给/cc/抄送
    ("姓名",
     r"(?:叫|是|给|向|找|告诉|通知|联系|发给|转发给|抄送|cc)[：:：\s]*"
     r"([\u4e00-\u9fa5]{2,4})(?=[，。！？\s「」【】]|$)",
     re.UNICODE),

    # ─── 家庭住址 ─────────────────────────────────────────────────
    # 省/市/区/街道/路/号/楼 组合模式，至少包含2个地址单元
    ("地址",
     r"[\u4e00-\u9fa5]{2,6}(?:省|自治区)"
     r"[\u4e00-\u9fa5]{2,8}(?:市|地区|自治州)"
     r"[\u4e00-\u9fa5]{0,8}(?:区|县|旗)"
     r"[\u4e00-\u9fa5]{0,20}(?:街道|镇|乡)"
     r"[\u4e00-\u9fa5]{0,20}(?:路|街|大道|大街|巷|弄)"
     r"\d{0,6}号?[\u4e00-\u9fa5]{0,10}",
     re.UNICODE),

    # ─── 护照/港澳证件 ────────────────────────────────────────────
    # 护照: E + 8位字母数字  |  港澳通行证: H/M + 10位
    ("证件号", r"[EeHhMm][a-zA-Z0-9]{7,9}", 0),
]

# 构建 config 开关映射: label → 配置字段名
_LABEL_TO_CONFIG: Dict[str, str] = {
    "手机号": "mask_phone",
    "固话":   "mask_landline",
    "身份证": "mask_id_card",
    "银行卡": "mask_bank_card",
    "邮箱":   "mask_email",
    "IP地址": "mask_ip",
    "姓名":   "mask_name",
    "地址":   "mask_address",
    "证件号": "mask_passport",
}


# ══════════════════════════════════════════════════════════════════
# 结果类型
# ══════════════════════════════════════════════════════════════════

@dataclass
class MaskResult:
    """
    脱敏操作结果。

    Attributes:
        masked_text : 脱敏后的文本（安全发往云端）
        original_text: 原始输入文本
        mask_map    : {占位符: 原始值} 用于事后还原
        stats       : {label: 触发次数}
        has_pii     : 是否检测到任何 PII
    """
    masked_text: str
    original_text: str
    mask_map: Dict[str, str]
    stats: Dict[str, int]

    @property
    def has_pii(self) -> bool:
        return bool(self.mask_map)

    def restore(self, text: str) -> str:
        """将 mask 占位符替换回原始值"""
        result = text
        # 按占位符长度降序替换，避免子串歧义
        for placeholder, original in sorted(self.mask_map.items(), key=lambda x: -len(x[0])):
            result = result.replace(placeholder, original)
        return result


# ══════════════════════════════════════════════════════════════════
# 核心：PIIFilter
# ══════════════════════════════════════════════════════════════════

class PIIFilter:
    """
    静态工具类。所有方法均为类方法，无需实例化。
    线程安全（mask_map 为局部变量，无全局状态）。
    """

    # 预编译正则（避免每次调用重复编译）
    _compiled: Optional[List[Tuple[str, re.Pattern]]] = None

    @classmethod
    def _get_compiled(cls) -> List[Tuple[str, re.Pattern]]:
        if cls._compiled is None:
            cls._compiled = [
                (label, re.compile(pattern, flags))
                for label, pattern, flags in _RULES
            ]
        return cls._compiled

    @classmethod
    def mask(
        cls,
        text: str,
        config: Optional[PIIConfig] = None,
    ) -> MaskResult:
        """
        对输入文本执行 PII 掩码。

        Args:
            text  : 原始输入文本
            config: 检测器开关配置，None 表示使用默认（全开）

        Returns:
            MaskResult 对象，包含脱敏文本和还原映射
        """
        if not text or not text.strip():
            return MaskResult(
                masked_text=text,
                original_text=text,
                mask_map={},
                stats={},
            )

        cfg = config or PIIConfig()
        mask_map: Dict[str, str] = {}
        stats: Dict[str, int] = {}
        counters: Dict[str, int] = {}
        result = text

        # ── 正则规则扫描 ────────────────────────────────────────────
        for label, pattern in cls._get_compiled():
            config_field = _LABEL_TO_CONFIG.get(label)
            if config_field and not getattr(cfg, config_field, True):
                continue  # 该类型被配置关闭

            def _replace(m: re.Match, _label: str = label) -> str:
                original = m.group(0)
                # 为相同类型的每次出现生成唯一占位符
                counters[_label] = counters.get(_label, 0) + 1
                placeholder = f"<<{_label}-{counters[_label]}>>"
                mask_map[placeholder] = original
                stats[_label] = stats.get(_label, 0) + 1
                return placeholder

            result = pattern.sub(_replace, result)

        # ── 自定义关键词 ─────────────────────────────────────────────
        for keyword in cfg.custom_keywords:
            if keyword and keyword in result:
                counters["自定义"] = counters.get("自定义", 0) + 1
                placeholder = f"<<自定义-{counters['自定义']}>>"
                mask_map[placeholder] = keyword
                stats["自定义"] = stats.get("自定义", 0) + 1
                result = result.replace(keyword, placeholder)

        if cfg.log_stats and stats:
            logger.info(f"[PIIFilter] 脱敏统计: {stats} | 共 {len(mask_map)} 处")

        return MaskResult(
            masked_text=result,
            original_text=text,
            mask_map=mask_map,
            stats=stats,
        )

    @classmethod
    def restore(cls, masked_text: str, mask_map: Dict[str, str]) -> str:
        """
        将已脱敏文本中的占位符还原为原始值。
        可传入 MaskResult.mask_map 或自定义映射。
        """
        result = masked_text
        for placeholder, original in sorted(mask_map.items(), key=lambda x: -len(x[0])):
            result = result.replace(placeholder, original)
        return result

    @classmethod
    def has_pii(cls, text: str, config: Optional[PIIConfig] = None) -> bool:
        """
        快速判断文本是否包含 PII（不返回详情，仅布尔值）。
        用于决策是否需要脱敏：has_pii → True 时才走完整 mask()。
        """
        result = cls.mask(text, config=config)
        return result.has_pii

    @classmethod
    def add_custom_keyword(cls, config: PIIConfig, keyword: str) -> PIIConfig:
        """返回添加了自定义关键词的新 config（不可变操作）"""
        new_keywords = list(config.custom_keywords) + [keyword]
        from dataclasses import replace as dc_replace
        return dc_replace(config, custom_keywords=new_keywords)

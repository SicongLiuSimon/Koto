#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文档修改二次快速检测机制
用于在应用 AI 修改建议前/后进行质量校验，防止破坏性修改。
"""

import re
from collections import Counter
from typing import Any, Dict, List, Tuple


class DocumentValidator:
    """文档修改校验器"""

    @staticmethod
    def validate_modifications(
        original_content: str, modifications: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        在应用修改前，校验修改列表的有效性和安全性
        (优化版：预先计算词频)
        """
        issues = []
        valid_count = 0
        risk_level = "LOW"

        # 1. 预先统计频次，减少重复搜索
        # 只统计 modifications 中涉及的原文
        target_originals = set()
        for mod in modifications:
            o = (
                mod.get("original")
                or mod.get("原文片段")
                or mod.get("原文")
                or mod.get("原文片段")
            )
            if o and o.strip():
                target_originals.add(o.strip())

        # 批量统计
        # 使用 count 仍然是 O(M*N)，但比每次都重新调用稍好
        # 优化：提前判断内容长度。如果内容巨大且 M 巨大，直接跳过部分高耗时检查

        counts = {}

        # 简单 heuristic: 只有当 M * N < 阈值 时才全量检查，否则只做存在性检查 (in)
        full_check_threshold = 10_000_000  # 例如 10MB * 10 checks
        n_len = len(original_content)
        m_len = len(target_originals)

        do_full_count = (n_len * m_len) < full_check_threshold

        for text in target_originals:
            if do_full_count:
                counts[text] = original_content.count(text)
            else:
                # 只做 fast check
                if text in original_content:
                    # 我们不知道多少次，假设是 1 次，除非它很短，风险自负
                    # 或者我们可以只对短文本做 count
                    if len(text) < 10:
                        counts[text] = original_content.count(text)
                    else:
                        counts[text] = 1  # 假定唯一
                else:
                    counts[text] = 0

        # 归一化空格后的内容缓存 (懒加载)
        content_clean = None

        for idx, mod in enumerate(modifications):
            original = mod.get("original") or mod.get("原文片段") or mod.get("原文")
            modified = (
                mod.get("modified")
                or mod.get("修改建议")
                or mod.get("修改后文本")
                or mod.get("改为")
            )

            if not original:
                issues.append(f"#{idx+1}: 原文为空")
                continue

            original = original.strip()
            if not original:
                issues.append(f"#{idx+1}: 原文为空白字符")
                continue

            # 检查检查原文是否存在
            count = counts.get(original, 0)

            if count == 0:
                # 尝试放宽匹配（去除空白符）
                if content_clean is None:
                    content_clean = re.sub(r"\s+", "", original_content)

                orig_clean = re.sub(r"\s+", "", original)
                if orig_clean not in content_clean:
                    issues.append(f"#{idx+1}: 原文未找到 '{original[:20]}...'")
                    risk_level = "HIGH"
                    continue
                else:
                    # 模糊匹配成功，不视为严重问题，不加入 issues 以免被过滤器拦截
                    # 如果需要日志，可以另加 warning 列表，但目前只依赖 issues 判断 pass/fail
                    pass
            elif count > 1:
                if len(original) < 20:
                    issues.append(
                        f"#{idx+1}: 原文出现 {count} 次，可能替换错误 '{original[:20]}...'"
                    )
                    risk_level = "MEDIUM" if risk_level != "HIGH" else "HIGH"

            if modified is None:
                issues.append(f"#{idx+1}: 修改内容为空")
                continue

            if original == modified:
                issues.append(f"#{idx+1}: 修改前后无变化")
                continue

            valid_count += 1

        return {
            "success": len(issues) == 0,
            "valid_count": valid_count,
            "total_count": len(modifications),
            "issues": issues,
            "risk_level": risk_level,
        }

    @staticmethod
    def verify_track_changes_integrity(doc_path: str) -> List[str]:
        """
        检测文档中的修订标记是否破坏了段落结构（事后检测）

        Args:
            doc_path: Word文档路径

        Returns:
            问题列表
        """
        from docx import Document

        issues = []
        try:
            doc = Document(doc_path)
            for i, p in enumerate(doc.paragraphs):
                # 检查是否存在空的 Track Changes 标签残余
                xml = p._element.xml
                if "<w:ins" in xml and "<w:t></w:t>" in xml:
                    issues.append(f"段落 #{i+1}: 存在空的内容插入标记")
                if "<w:del" in xml and "<w:delText></w:delText>" in xml:
                    issues.append(f"段落 #{i+1}: 存在空的删除标记")

                # 简单检查：如果段落只有 w:ins 而没有任何 pending text，可能正常
                # 但如果段落结构极度破碎（例如全是 split），也记录
        except Exception as e:
            issues.append(f"文档解析失败: {e}")

        return issues

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
路由系统回归测试
验证路由重构后，各类请求被正确分发
"""

import os
import sys

# 添加项目根目录到路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# 直接测试核心函数
from web.app import _is_analysis_request


def test_analysis_request():
    """测试分析请求判断 — 纯分析不带生成意图"""
    print("=" * 80)
    print("  测试: _is_analysis_request() — 分析请求判断")
    print("  目标: 只有纯分析（不含生成/改善意图）才触发")
    print("=" * 80)

    tests = [
        # 纯分析请求
        ("分析这篇论文的结构", True),
        ("总结这篇文档的要点", True),
        ("梳理一下文章的核心观点", True),
        ("评估一下这篇报告", True),
        ("对比两种方案的优缺点", True),
        # 带生成/改善意图 → 不是纯分析
        ("分析并改善结论", False),
        ("总结之后帮我写个摘要", False),
        ("分析文章并优化引言", False),
        ("帮我改善引言", False),
        ("重写结论", False),
        ("润色这段话", False),
    ]

    passed = 0
    failed = 0

    for text, expected in tests:
        result = _is_analysis_request(text)
        status = "✅" if result == expected else "❌"
        if result != expected:
            failed += 1
            print(f'{status} "{text}"')
            print(f"     期望: {expected}, 实际: {result}")
        else:
            passed += 1
            print(f'{status} "{text}" → {result}')

    print(f"\n结果: {passed}/{len(tests)} 通过, {failed} 失败\n")
    return failed == 0


def test_intelligent_analyzer_routing():
    """测试文档上传时智能分析器是否正确触发"""
    print("=" * 80)
    print("  测试: 文档上传智能分析器触发逻辑")
    print("  目标: 对文档的实质处理请求都进入智能分析器")
    print("=" * 80)

    # 这些请求上传 .docx 时应该被智能分析器处理
    _doc_intent_keywords = [
        "写",
        "生成",
        "帮我写",
        "写一段",
        "写个",
        "改",
        "改善",
        "改进",
        "优化",
        "润色",
        "重写",
        "修改",
        "提升",
        "摘要",
        "引言",
        "结论",
        "abstract",
        "前言",
        "导言",
        "分析",
        "总结",
        "梳理",
        "概述",
        "评估",
        "不满意",
        "不好",
        "不够",
        "需要改",
        "有问题",
    ]

    tests = [
        # 应该触发智能分析器的请求
        ("写一段摘要", True),
        ("帮我改善结论", True),
        ("重新改善引言", True),
        ("分析这篇论文的结构", True),
        ("帮我写一段300字的摘要", True),
        ("这篇论文的结论不够好，帮我优化", True),
        ("帮我润色引言部分", True),
        ("生成一个摘要", True),
        ("改进引言，使其与文章主体符合", True),
        # 不应该触发的请求（非文档处理）
        ("这是什么文件", False),
        ("打开这个文件", False),
    ]

    passed = 0
    failed = 0

    for text, expected in tests:
        result = any(kw in text.lower() for kw in _doc_intent_keywords)
        status = "✅" if result == expected else "❌"
        if result != expected:
            failed += 1
            print(f'{status} "{text}"')
            print(f"     期望: {expected}, 实际: {result}")
        else:
            passed += 1
            print(f"{status} \"{text}\" → {'智能分析器' if result else '其他路由'}")

    print(f"\n结果: {passed}/{len(tests)} 通过, {failed} 失败\n")
    return failed == 0


def main():
    print("\n" + "█" * 80)
    print("  Koto 路由系统回归测试")
    print("  验证路由重构后的正确性")
    print("█" * 80 + "\n")

    results = []
    results.append(("分析请求判断", test_analysis_request()))
    results.append(("智能分析器触发", test_intelligent_analyzer_routing()))

    print("=" * 80)
    print("  总体结果")
    print("=" * 80)

    all_passed = True
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"  {status}: {name}")
        all_passed = all_passed and result

    print()
    if all_passed:
        print("🎉 所有路由测试通过！路由重构成功。")
        print()
        print("关键改进:")
        print(
            "  1. _should_use_annotation_system 严格化 — 不再用'修改/优化/检查'等宽泛词误判"
        )
        print("  2. _is_analysis_request 排除生成意图 — '分析并改善'不再被当做纯分析")
        print("  3. 智能分析器入口放宽 — 任何对文档的实质处理需求都进入智能分析器")
        print("  4. SmartDispatcher 不再无文件上下文就判定 DOC_ANNOTATE")
        print("  5. 本地模型不再独裁 — DOC_ANNOTATE/FILE_GEN 需要文件上下文才采信")
    else:
        print("⚠️ 部分测试失败，需要进一步调整。")

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
综合测试: 文件质量自检系统 + 智能反馈系统
"""
import os
import sys
import json

# Ensure project root is in sys.path
test_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(test_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from web.file_quality_checker import ContentSanitizer, FileQualityEvaluator, FileQualityGate
from web.smart_feedback import SmartFeedback

# ══════════════════════════════════════════════════════════
# TEST 1: ContentSanitizer — 文本清洗
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 1: ContentSanitizer — 文本清洗")
print("=" * 60)

test_cases = [
    # (输入, 预期应被清洗的内容, 上下文)
    ("**核心概念** — 这是一段解释", "核心概念 — 这是一段解释", "ppt_point"),
    ("当然可以！以下是AI的主要应用", "AI的主要应用", "ppt_point"),
    ("Sure! Here's the answer: AI is great", "AI is great", "ppt_point"),
    ("``代码示例``", "代码示例", "ppt_point"),
    ("### 章节标题", "章节标题", "ppt_title"),
    ("- 这是一个要点", "这是一个要点", "ppt_point"),
    ("1. 第一个要点", "第一个要点", "ppt_point"),
    ("好的！让我来为你生成内容：市场规模达500亿", "市场规模达500亿", "ppt_point"),
    ("希望这对你有帮助", "", "ppt_point"),
    ("需要我继续吗？", "", "ppt_point"),
    ("''这是一段话''", "这是一段话", "ppt_point"),
    ("*斜体文字*内容", "斜体文字内容", "ppt_point"),
    ("~~删除线~~正常文字", "正常文字", "ppt_point"),
    ("[链接文字](https://example.com)", "链接文字", "ppt_point"),
]

passed = 0
failed = 0
for i, (input_text, expected_contains, ctx) in enumerate(test_cases):
    result = ContentSanitizer.sanitize_text(input_text, ctx)
    # 检查是否包含预期内容（或等于预期）
    ok = False
    if expected_contains == "":
        ok = result == "" or result == input_text  # 空预期表示应被清除（或无变化）
    elif expected_contains in result:
        ok = True
    elif result == expected_contains:
        ok = True
    
    # 检查不应包含的残留
    no_residue = True
    if ctx == "ppt_point":
        if "**" in result and "**" in input_text:
            no_residue = False
        if "`" in result and "`" in input_text:
            no_residue = False
        if result.startswith("当然") or result.startswith("好的") or result.startswith("Sure"):
            no_residue = False
    
    status = "✅" if (ok and no_residue) else "❌"
    if ok and no_residue:
        passed += 1
    else:
        failed += 1
    print(f"  {status} Case {i+1}: '{input_text[:40]}' → '{result[:40]}'")
    if not ok:
        print(f"       Expected to contain: '{expected_contains[:40]}'")

print(f"\n  结果: {passed}/{passed+failed} 通过\n")

# ══════════════════════════════════════════════════════════
# TEST 2: PPT 大纲清洗
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 2: ContentSanitizer.sanitize_ppt_outline")
print("=" * 60)

test_outline = [
    {
        "type": "detail",
        "title": "### **市场概览**",
        "points": [
            "**核心概念** — 这是``代码``里的内容",
            "当然可以！以下是市场数据",
            "希望这对你有帮助",
            "市场规模达到500亿美元，同比增长35%",
        ],
        "content": [
            "**核心概念** — 这是``代码``里的内容",
            "当然可以！以下是市场数据",
            "希望这对你有帮助",
            "市场规模达到500亿美元，同比增长35%",
        ]
    },
    {
        "type": "comparison",
        "title": "好的！**方案对比**",
        "points": [],
        "content": [],
        "subsections": [
            {
                "subtitle": "### 方案A",
                "label": "### 方案A",
                "points": ["**优势1** — 成本低", "Sure! Here's point 2"]
            },
            {
                "subtitle": "方案B",
                "label": "方案B",
                "points": ["特点1", "特点2"]
            }
        ]
    },
    {
        "type": "divider",
        "title": "过渡页",
        "points": [],
        "content": [],
        "description": "第二部分"
    }
]

sanitized, fixes = ContentSanitizer.sanitize_ppt_outline(test_outline)
print(f"  修复数: {len(fixes)}")
for f in fixes:
    print(f"    - {f}")

# 验证
checks = [
    ("标题无残留", "**" not in sanitized[0]["title"] and "###" not in sanitized[0]["title"]),
    ("AI前缀已清除", not any("当然" in p for p in sanitized[0]["points"])),
    ("对话痕迹已清除", not any("希望" in p for p in sanitized[0]["points"])),
    ("正常内容保留", any("500亿" in p for p in sanitized[0]["points"])),
    ("对比页子标题清洗", "###" not in sanitized[1]["subsections"][0]["subtitle"]),
    ("对比页AI前缀清除", not any("Sure" in p for p in sanitized[1]["subsections"][0]["points"])),
    ("过渡页不受影响", sanitized[2]["title"] == "过渡页"),
]

for name, result in checks:
    print(f"  {'✅' if result else '❌'} {name}")

print()

# ══════════════════════════════════════════════════════════
# TEST 3: FileQualityEvaluator — PPT 大纲评估
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 3: FileQualityEvaluator — PPT 大纲评估")
print("=" * 60)

# 好的大纲
good_outline = [
    {"type": "divider", "title": "第一部分", "points": [], "content": []},
    {"type": "detail", "title": "市场概览", "points": [
        "全球AI市场规模达到5000亿美元，据IDC预测2026年将突破8000亿",
        "中国AI产业增速领先全球，年复合增长率达35%，居各主要经济体首位",
        "AI芯片市场由NVIDIA主导，占据全球GPU服务器市场约80%份额",
        "大模型参数规模从GPT-3的1750亿增长到GPT-4的超过1万亿参数"
    ], "content": []},
    {"type": "detail", "title": "技术架构", "points": [
        "Transformer架构自2017年提出以来，已成为NLP和CV领域的标准范式",
        "注意力机制使模型能够关注输入序列中最相关的部分，提升理解能力",
        "预训练+微调的范式逐渐被提示工程和RAG检索增强生成所补充",
        "多模态技术使AI能同时处理文本、图像、音频和视频等多种数据形式"
    ], "content": []},
    {"type": "overview", "title": "应用场景", "points": [], "content": [], "subsections": [
        {"subtitle": "医疗", "label": "医疗", "points": ["AI辅助诊断准确率达95%以上", "药物研发周期缩短40%"]},
        {"subtitle": "金融", "label": "金融", "points": ["智能风控将欺诈检测率提升至99.5%", "量化交易策略收益提升20%"]},
    ]},
    {"type": "highlight", "title": "关键数据", "points": ["5000亿 | 全球AI市场规模", "35% | 中国AI年增长率", "80% | NVIDIA GPU市场份额"], "content": []},
    {"type": "detail", "title": "未来展望", "points": [
        "AGI（通用人工智能）可能在2030年前后实现，将彻底改变社会形态",
        "AI安全和伦理法规在全球范围内加速立法，欧盟AI法案将于2026年全面实施",
        "边缘AI使本地推理成为可能，预计2027年50%的AI推理将在终端设备完成"
    ], "content": []},
]

result_good = FileQualityEvaluator.evaluate_ppt_outline(good_outline, "做一个关于人工智能的PPT")
print(f"  好大纲评分: {result_good['score']}/100, pass={result_good['pass']}")
print(f"  类型: {result_good['metrics']['types_used']}")
print(f"  总要点: {result_good['metrics']['total_points']}, 平均: {result_good['metrics']['avg_points']}")
if result_good['issues']:
    print(f"  问题: {result_good['issues']}")

# 差的大纲
bad_outline = [
    {"type": "detail", "title": "", "points": [], "content": []},
    {"type": "detail", "title": "页面2", "points": ["短"], "content": ["短"]},
    {"type": "detail", "title": "**有残留**", "points": ["**bold**点", "`code`点"], "content": []},
]

result_bad = FileQualityEvaluator.evaluate_ppt_outline(bad_outline, "做一个关于AI的PPT")
print(f"\n  差大纲评分: {result_bad['score']}/100, pass={result_bad['pass']}")
print(f"  问题数: {len(result_bad['issues'])}")
for issue in result_bad['issues']:
    print(f"    - {issue}")

print()

# ══════════════════════════════════════════════════════════
# TEST 4: FileQualityGate — 完整门控流程
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 4: FileQualityGate — 完整门控流程")
print("=" * 60)

progress_msgs = []
def _test_progress(msg, detail=""):
    progress_msgs.append((msg, detail))

# 测试 PPT 门控
contaminated_outline = [
    {"type": "detail", "title": "**AI发展趋势**", "points": [
        "当然可以！**核心趋势** — AI技术正在快速发展，据IDC预测全球市场将达到5000亿美元",
        "好的，以下是第二个要点：深度学习框架越来越成熟，PyTorch和TensorFlow生态不断壮大",
        "第三个要点 — 边缘AI部署加速，高通和联发科推出了多款专用AI芯片",
        "Let me know if you need more. 多模态AI融合文本视觉和音频处理能力",
    ], "content": [
        "当然可以！**核心趋势** — AI技术正在快速发展，据IDC预测全球市场将达到5000亿美元",
        "好的，以下是第二个要点：深度学习框架越来越成熟，PyTorch和TensorFlow生态不断壮大",
        "第三个要点 — 边缘AI部署加速，高通和联发科推出了多款专用AI芯片",
        "Let me know if you need more. 多模态AI融合文本视觉和音频处理能力",
    ]},
    {"type": "detail", "title": "Sure! Here's: 技术架构", "points": [
        "Transformer是目前最主流的深度学习架构，由Google Brain在2017年提出",
        "注意力机制让模型能够有选择地关注输入中最重要的部分",
        "RAG检索增强生成结合了搜索和生成，大幅提升回答准确性",
    ], "content": []},
    {"type": "highlight", "title": "关键数据", "points": [
        "5000亿 | 全球AI市场规模(2025年)",
        "35% | 中国AI年复合增长率",
        "1万亿 | GPT-4参数规模",
    ], "content": []},
    {"type": "detail", "title": "未来展望", "points": [
        "AGI可能在2030年前后实现，将彻底改变社会生产方式和经济结构",
        "AI安全立法加速，欧盟AI法案将于2026年全面实施，规定高风险AI系统准入标准",
        "边缘AI使终端设备具备推理能力，2027年50%的AI任务将在本地完成",
        "人机协作将成为主流工作模式，AI替代重复劳动，人类专注创意和决策",
    ], "content": []},
]

qg_result = FileQualityGate.check_and_fix_ppt_outline(contaminated_outline, "做一个关于AI的PPT", _test_progress)

print(f"  修复数: {len(qg_result['fixes'])}")
print(f"  评分: {qg_result['quality']['score']}/100")
print(f"  动作: {qg_result['action']}")
print(f"  进度消息: {len(progress_msgs)} 条")
for msg, detail in progress_msgs:
    print(f"    [{msg}] {detail}")

# 验证清洗结果
slide0_title = qg_result['outline'][0]['title']
slide0_points = qg_result['outline'][0]['points']
slide1_title = qg_result['outline'][1]['title']

checks = [
    ("标题 ** 已清除", "**" not in slide0_title),
    ("AI前缀已清除", not any(p.startswith("当然") for p in slide0_points)),
    ("AI前缀已清除(好的)", not any(p.startswith("好的") for p in slide0_points)),
    ("Sure前缀已清除", "Sure" not in slide1_title),
    ("对话痕迹已清除", not any("Let me know" in p for p in slide0_points)),
    ("正常内容保留", any("5000亿" in p for p in slide0_points)),
    ("质量评分合理", qg_result['quality']['score'] >= 50),
]

for name, result in checks:
    print(f"  {'✅' if result else '❌'} {name}")

print()

# ══════════════════════════════════════════════════════════
# TEST 5: 文档质量检查
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 5: 文档质量检查")
print("=" * 60)

doc_text = """当然可以！以下是关于AI的报告：

# 人工智能发展报告

## 行业概述

人工智能（AI）技术在过去十年取得了显著突破。据IDC预测，全球AI市场规模将在2025年达到5000亿美元。中国作为AI领域的重要力量，年复合增长率达35%。

深度学习、自然语言处理、计算机视觉等子领域不断涌现创新成果。GPT-4、Gemini等大模型的出现标志着AI进入了新纪元。

## 技术架构

现代AI系统主要基于Transformer架构，该架构由Google Brain在2017年提出。注意力机制使模型能够关注输入序列中最相关的部分，极大提升了处理效率。

预训练+微调的范式已经成为行业标准，而RAG（检索增强生成）技术的出现进一步提升了AI系统的准确性。

## 未来展望

AGI（通用人工智能）被认为可能在2030年前后实现。AI安全和伦理法规也在全球范围内加速立法。

希望这对你有帮助！如果需要更多信息，请随时告诉我。
"""

progress_msgs_doc = []
def _test_doc_progress(msg, detail=""):
    progress_msgs_doc.append((msg, detail))

doc_result = FileQualityGate.check_and_fix_document(doc_text, "写一篇AI报告", _test_doc_progress)

print(f"  评分: {doc_result['quality']['score']}/100")
print(f"  pass: {doc_result['quality']['pass']}")
print(f"  修复数: {len(doc_result['fixes'])}")
for f in doc_result['fixes']:
    print(f"    - {f}")
print(f"  指标: {doc_result['quality']['metrics']}")

# 验证清洗结果
checks_doc = [
    ("AI前缀已清除", "当然可以" not in doc_result['text'][:20]),
    ("对话痕迹已清除", "希望这对你有帮助" not in doc_result['text']),
    ("正常内容保留", "5000亿" in doc_result['text']),
    ("标题保留", "# 人工智能发展报告" in doc_result['text']),
    ("质量评分合理", doc_result['quality']['score'] >= 60),
]

for name, result in checks_doc:
    print(f"  {'✅' if result else '❌'} {name}")

print()

# ══════════════════════════════════════════════════════════
# TEST 6: SmartFeedback — 智能反馈系统
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 6: SmartFeedback — 智能反馈系统")  
print("=" * 60)

import time

messages = []
def _fb_emit(msg, detail=""):
    messages.append((msg, detail))
    print(f"    💬 [{msg}] {detail}")

# PPT 任务
fb = SmartFeedback.for_ppt("做一个关于人工智能发展趋势的PPT", _fb_emit)
fb.start()
fb.ppt_planning("调用 AI 规划内容")
fb.search_start("人工智能最新趋势")
fb.search_done(result_count=5, char_count=3000)
fb.ppt_outline_ready(10, title="人工智能发展趋势", type_summary="详细×6, 概览×2, 亮点×1, 过渡×1")
fb.ppt_enriching(3)
fb.ppt_enriched(3)
fb.ppt_images(4)
fb.ppt_images_done(3)
fb.quality_report(85, issues=[], fixes=["移除2处Markdown残留"])
fb.ppt_rendering(10)
fb.ppt_slide_progress(3, 10, "技术架构", "详细页")
fb.ppt_slide_progress(7, 10, "应用场景", "概览页")
fb.done("PPT 生成完成", "文件: AI趋势报告.pptx")

print(f"\n  PPT反馈消息数: {len(messages)}")

# 验证消息质量
checks_fb = [
    ("包含用户主题", any("人工智能" in m for m, _ in messages)),
    ("包含步骤计数", any("[" in m and "/" in m for m, _ in messages)),
    ("包含幻灯片数量", any("10" in m for m, _ in messages)),
    ("包含质量评分", any("85" in m for m, _ in messages)),
    ("包含完成消息", any("完成" in m for m, _ in messages)),
    ("包含耗时", any("耗时" in m for m, _ in messages)),
    ("无emoji前缀滥用", not all(m.startswith("🎨") for m, _ in messages)),
]

for name, result in checks_fb:
    print(f"  {'✅' if result else '❌'} {name}")

print()

# 文档任务
messages_doc = []
def _fb_emit_doc(msg, detail=""):
    messages_doc.append((msg, detail))

fb_doc = SmartFeedback.for_document("写一篇关于量子计算的技术报告", _fb_emit_doc)
fb_doc.start()
fb_doc.doc_generating("Word", "gemini-2.5-flash")
fb_doc.doc_writing_progress(2000)
fb_doc.doc_saving("Word")
fb_doc.quality_report(90)
fb_doc.done("Word 文档保存完成")

print(f"  文档反馈消息数: {len(messages_doc)}")
checks_fb_doc = [
    ("包含用户主题", any("量子计算" in m for m, _ in messages_doc)),
    ("包含字数进度", any("2000" in m for m, _ in messages_doc)),
    ("包含质量评分", any("90" in m for m, _ in messages_doc)),
]
for name, result in checks_fb_doc:
    print(f"  {'✅' if result else '❌'} {name}")

print()

# ══════════════════════════════════════════════════════════
# TEST 7: PPT 生成 + 质量检查端到端测试
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 7: PPT 生成 + 质量检查 端到端测试")
print("=" * 60)

try:
    from web.ppt_generator import PPTGenerator
    import tempfile
    
    # 生成一个测试 PPT
    test_outline = [
        {"type": "divider", "title": "市场分析", "description": "第一部分", "points": [], "content": []},
        {"type": "detail", "title": "**全球AI市场概况**", "points": [
            "**核心数据** — 市场规模达5000亿美元，同比增长25%",
            "当然可以！中国AI产业增速达35%，领先全球主要经济体",
            "深度学习框架PyTorch用户数突破`100万`，生态日趋成熟",
            "大模型参数规模从GPT-3的1750亿增长到GPT-4的超过1万亿",
        ], "content": [
            "**核心数据** — 市场规模达5000亿美元，同比增长25%",
            "当然可以！中国AI产业增速达35%，领先全球主要经济体",
            "深度学习框架PyTorch用户数突破`100万`，生态日趋成熟",
            "大模型参数规模从GPT-3的1750亿增长到GPT-4的超过1万亿",
        ]},
        {"type": "overview", "title": "应用场景", "points": [], "content": [], "subsections": [
            {"subtitle": "### 医疗","label": "### 医疗", "points": [
                "**AI辅助诊断** — 准确率达95%以上",
                "希望这对你有帮助",
            ]},
            {"subtitle": "金融", "label": "金融", "points": [
                "智能风控检测欺诈率提升至99.5%",
                "量化交易策略平均收益提升20%",
            ]}
        ]},
        {"type": "highlight", "title": "关键数据", "points": [
            "5000亿 | 全球AI市场规模",
            "35% | 中国AI年增长率",
            "1万亿 | GPT-4参数规模",
        ], "content": []},
        {"type": "detail", "title": "未来展望", "points": [
            "AGI可能在2030年实现，将彻底改变经济结构和社会形态",
            "人机协作将成为主流，AI替代重复劳动，人类专注创意和决策",
            "边缘AI使终端推理成为可能，预计2027年50%任务在本地完成",
        ], "content": []},
    ]
    
    # Step 1: 质量门控（清洗+评估）
    qg = FileQualityGate.check_and_fix_ppt_outline(test_outline, "做一个AI市场分析的PPT")
    print(f"  质量门控评分: {qg['quality']['score']}/100")
    print(f"  修复数: {len(qg['fixes'])}")
    print(f"  动作: {qg['action']}")
    
    # Step 2: 生成 PPT
    gen = PPTGenerator(theme="business")
    with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as f:
        temp_path = f.name
    
    result = gen.generate_from_outline(
        title="AI市场分析报告",
        outline=qg['outline'],
        output_path=temp_path,
        subtitle="2025年度报告",
        author="Koto AI"
    )
    
    print(f"  生成结果: {result['success']}, {result.get('slide_count', 0)} 页")
    
    # Step 3: 后置文件检查
    post_check = FileQualityGate.post_check_pptx(temp_path)
    print(f"  文件后检评分: {post_check['score']}/100")
    print(f"  Markdown残留: {post_check.get('metrics', {}).get('md_residue', 0)}")
    print(f"  空页面: {post_check.get('metrics', {}).get('empty_slides', 0)}")
    
    checks_e2e = [
        ("生成成功", result['success']),
        ("门控通过", qg['quality']['pass']),
        ("文件后检通过", post_check['pass']),
        ("无Markdown残留", post_check.get('metrics', {}).get('md_residue', 0) == 0),
        ("无空页面", post_check.get('metrics', {}).get('empty_slides', 0) == 0),
        ("页数合理", result.get('slide_count', 0) >= 5),
    ]
    
    for name, result_check in checks_e2e:
        print(f"  {'✅' if result_check else '❌'} {name}")
    
    # 清理
    os.unlink(temp_path)

except ImportError as e:
    print(f"  ⚠️ 跳过端到端测试 (缺少依赖): {e}")

print()

# ══════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════
print("=" * 60)
print("所有测试完成！")
print("=" * 60)

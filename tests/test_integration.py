#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
集成测试 — 验证文件质量自检 + 智能反馈系统的端到端集成
模拟实际 API 调用，检查 SSE 流中的进度消息
"""

import json
import requests
import sys
import time

BASE_URL = "http://127.0.0.1:5000"

def test_server_alive():
    """测试服务器是否在线"""
    try:
        r = requests.get(BASE_URL, timeout=5)
        return r.status_code == 200
    except:
        return False

def stream_chat(message: str, timeout: int = 120):
    """发送消息并收集所有 SSE 事件"""
    url = f"{BASE_URL}/api/chat/stream"
    payload = {
        "message": message,
        "session": "test_quality_feedback",
        "model": "auto",
    }
    
    events = []
    progress_messages = []
    tokens = []
    saved_files = []
    
    try:
        r = requests.post(url, json=payload, stream=True, timeout=timeout)
        r.raise_for_status()
        
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                    events.append(data)
                    
                    evt_type = data.get("type", "")
                    if evt_type == "progress":
                        msg = data.get("message", "")
                        detail = data.get("detail", "")
                        progress_messages.append({"msg": msg, "detail": detail})
                        print(f"  📊 {msg}" + (f" | {detail}" if detail else ""))
                    elif evt_type == "token":
                        content = data.get("content", "")
                        tokens.append(content)
                        if content.strip():
                            print(f"  📝 {content.strip()[:120]}")
                    elif evt_type == "done":
                        saved_files = data.get("saved_files", [])
                    elif evt_type == "classification":
                        task_type = data.get("task_type", "?")
                        print(f"  🎯 分类: {task_type}")
                    elif evt_type == "status":
                        msg = data.get("message", "")
                        if msg.strip():
                            print(f"  📌 {msg.strip()[:80]}")
                    elif evt_type == "error":
                        print(f"  ❌ {data.get('message', '?')}")
                        
                except json.JSONDecodeError:
                    pass
                    
    except requests.exceptions.Timeout:
        print("  ⚠️ 请求超时")
    except Exception as e:
        print(f"  ⚠️ 请求异常: {e}")
    
    return {
        "events": events,
        "progress": progress_messages,
        "tokens": tokens,
        "saved_files": saved_files,
        "full_text": "".join(tokens),
    }

def check_no_hardcoded_emoji_prefix(progress_msgs):
    """检查进度消息中没有公式化的emoji前缀"""
    bad_patterns = ["🚀", "🖥️ 正在分析", "📂 正在分析"]
    issues = []
    for p in progress_msgs:
        msg = p["msg"]
        for bp in bad_patterns:
            if msg.startswith(bp):
                issues.append(f"公式化前缀: '{msg[:40]}'")
    return issues

def check_smart_feedback_quality(progress_msgs, task_type):
    """检查智能反馈消息质量"""
    results = {"ok": True, "issues": []}
    
    if not progress_msgs:
        results["ok"] = False
        results["issues"].append("无进度消息")
        return results
    
    # 检查是否有步骤计数格式 [x/y]
    has_step_count = any("[" in p["msg"] and "/" in p["msg"] and "]" in p["msg"] for p in progress_msgs)
    
    # 检查是否有子步骤 →
    has_substep = any("→" in p["msg"] for p in progress_msgs)
    
    # 检查是否有质量检查
    has_quality = any("质量" in p["msg"] for p in progress_msgs)
    
    # 检查是否有完成消息
    has_done = any("完成" in p["msg"] for p in progress_msgs)
    
    if task_type == "PPT":
        if not has_step_count:
            results["issues"].append("PPT 流程缺少步骤计数 [x/y]")
        if not has_quality:
            results["issues"].append("PPT 流程缺少质量检查报告")
    
    if not has_done:
        results["issues"].append("缺少完成消息")
    
    # 检查公式化前缀
    emoji_issues = check_no_hardcoded_emoji_prefix(progress_msgs)
    results["issues"].extend(emoji_issues)
    
    results["ok"] = len(results["issues"]) == 0
    return results


# ═════════════════════════════════════════════════
# 主测试流程
# ═════════════════════════════════════════════════

print("=" * 60)
print("集成测试: 文件质量自检 + 智能反馈系统")
print("=" * 60)

# 检查服务器
if not test_server_alive():
    print("❌ 服务器不在线，请先启动 Koto")
    sys.exit(1)
print("✅ 服务器在线\n")

# ── 测试 1: PPT 生成（核心功能） ──
print("=" * 60)
print("TEST 1: PPT 生成流程 — 智能反馈 + 质量自检")
print("=" * 60)

result = stream_chat("帮我做一个关于人工智能发展趋势的PPT，大概5页", timeout=180)

print(f"\n  进度消息数: {len(result['progress'])}")
print(f"  生成文件: {result['saved_files']}")
print(f"  回复文本长度: {len(result['full_text'])}")

# 检查
checks = []
checks.append(("有进度消息", len(result['progress']) > 0))
checks.append(("有生成文件", len(result['saved_files']) > 0))
checks.append(("有回复内容", len(result['full_text']) > 0))

# 检查关键进度消息
all_msgs = " ".join(p["msg"] for p in result["progress"])
checks.append(("包含'规划'步骤", "规划" in all_msgs))
checks.append(("包含'质量'检查", "质量" in all_msgs))
checks.append(("包含'渲染'步骤", "渲染" in all_msgs))
checks.append(("无旧式公式化前缀", len(check_no_hardcoded_emoji_prefix(result["progress"])) == 0))

# 检查文件内容无 markdown 残留
if result['saved_files']:
    checks.append(("文件路径有效", result['saved_files'][0].endswith('.pptx')))

for name, ok in checks:
    print(f"  {'✅' if ok else '❌'} {name}")

# ── 测试 2: 简单对话（确保没有影响） ──
print(f"\n{'=' * 60}")
print("TEST 2: 简单对话 — 确保没有副作用")
print("=" * 60)

result2 = stream_chat("你好，今天天气怎么样？", timeout=30)

print(f"\n  进度消息数: {len(result2['progress'])}")
print(f"  回复长度: {len(result2['full_text'])}")

checks2 = []
checks2.append(("有回复", len(result2['full_text']) > 0))
checks2.append(("无异常错误", not any(e.get("type") == "error" for e in result2["events"])))
checks2.append(("开始消息无公式emoji", len(check_no_hardcoded_emoji_prefix(result2["progress"])) == 0))

for name, ok in checks2:
    print(f"  {'✅' if ok else '❌'} {name}")

print(f"\n{'=' * 60}")
print("集成测试完成！")
print("=" * 60)

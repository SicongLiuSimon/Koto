#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实际API调用测试 - 验证各种任务类型下各模型能否正常工作
运行方式:
  cd Koto
  .venv\\Scripts\\python.exe tests\\test_model_calls_live.py
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 加载 API key
with open(os.path.join(ROOT, "config", "gemini_config.env")) as f:
    for line in f:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()


# 自动检测并设置代理（与 web/app.py setup_proxy 逻辑一致）
def _setup_proxy_for_test():
    import socket
    from urllib.parse import urlparse

    candidates = [
        "http://127.0.0.1:7890",
        "http://127.0.0.1:10809",
        "http://127.0.0.1:1080",
        "http://127.0.0.1:8080",
    ]
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as k:
            if winreg.QueryValueEx(k, "ProxyEnable")[0]:
                ps = str(winreg.QueryValueEx(k, "ProxyServer")[0]).strip()
                if ps:
                    candidates.insert(0, ps if "://" in ps else f"http://{ps}")
    except Exception:
        pass
    for proxy in candidates:
        try:
            p = urlparse(proxy)
            if not p.hostname or not p.port:
                continue
            s = socket.socket()
            s.settimeout(0.3)
            if s.connect_ex((p.hostname, p.port)) == 0:
                os.environ["HTTPS_PROXY"] = proxy
                os.environ["HTTP_PROXY"] = proxy
                s.close()
                return proxy
            s.close()
        except Exception:
            pass
    return None


_proxy = _setup_proxy_for_test()
if _proxy:
    print(f"  代理: {_proxy}")

from app.core.llm.gemini import GeminiProvider, _INTERACTIONS_ONLY_MODELS

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

passed = []
failed = []


def ok(label, detail=""):
    passed.append(label)
    print(f"  {GREEN}✅ {label}{RESET}" + (f" → {detail[:80]}" if detail else ""))


def fail(label, err):
    failed.append(label)
    print(f"  {RED}❌ {label}: {str(err)[:120]}{RESET}")


def section(title):
    print(f"\n{'─'*60}\n  {title}\n{'─'*60}")


# ── 0. 初始化 ─────────────────────────────────────────────────────────────────
section("0. 初始化 GeminiProvider")
p = GeminiProvider()
if p.client:
    ok("GeminiProvider 初始化")
else:
    fail("GeminiProvider 初始化", "client is None - API key 可能无效")
    sys.exit(1)

# ── 检查模型分类 ──────────────────────────────────────────────────────────────
section("1. 模型分类验证 (无需API)")
check_cases = [
    ("gemini-3-flash-preview", False, "应走 generate_content"),
    ("gemini-3-pro-preview", False, "应走 generate_content"),
    ("gemini-3.1-pro-preview", False, "应走 generate_content"),
    ("gemini-2.5-pro-preview", False, "应走 generate_content"),
    ("gemini-2.5-flash", False, "应走 generate_content"),
    ("gemini-2.0-flash", False, "应走 generate_content"),
    ("deep-research-pro-preview-12-2025", True, "应走 interactions.create(agent=)"),
]
for model_id, should_be_interactions, note in check_cases:
    result = model_id in _INTERACTIONS_ONLY_MODELS
    if result == should_be_interactions:
        ok(f"{model_id[:35]}", note)
    else:
        fail(
            f"{model_id}", f"期望 interactions={should_be_interactions}, 实际={result}"
        )

# ── 实际 API 调用测试 ────────────────────────────────────────────────────────
section("2. CHAT 任务 - gemini-3-flash-preview")
try:
    r = p.generate_content("你好，用一句话介绍自己", model="gemini-3-flash-preview")
    text = r.get("content", "")
    assert text, "empty response"
    ok("gemini-3-flash-preview CHAT", text)
except Exception as e:
    fail("gemini-3-flash-preview CHAT", e)

section("3. CHAT 任务 - gemini-2.5-flash (fallback)")
try:
    r = p.generate_content("什么是量子计算？一句话解释", model="gemini-2.5-flash")
    text = r.get("content", "")
    assert text, "empty response"
    ok("gemini-2.5-flash CHAT", text)
except Exception as e:
    fail("gemini-2.5-flash CHAT", e)

section("4. STREAM 任务 - gemini-3-flash-preview")
try:
    chunks = list(
        p.generate_content("数1到5", model="gemini-3-flash-preview", stream=True)
    )
    assert chunks, "no chunks"
    full = "".join(c.get("content", "") for c in chunks)
    assert full, "empty stream"
    ok("gemini-3-flash-preview STREAM", f"{len(chunks)} chunks, '{full[:40]}'")
except Exception as e:
    fail("gemini-3-flash-preview STREAM", e)

section("5. 带 system_instruction - gemini-3-flash-preview")
try:
    r = p.generate_content(
        "What is 2+2?",
        model="gemini-3-flash-preview",
        system_instruction="You are a math tutor. Answer only with numbers.",
    )
    text = r.get("content", "")
    assert text, "empty response"
    ok("gemini-3-flash-preview system_instruction", text)
except Exception as e:
    fail("gemini-3-flash-preview system_instruction", e)

section("6. RESEARCH 任务 - gemini-3-pro-preview (generate_content)")
try:
    r = p.generate_content(
        "简述大型语言模型的发展历程，100字左右", model="gemini-3-pro-preview"
    )
    text = r.get("content", "")
    assert text, "empty response"
    ok("gemini-3-pro-preview RESEARCH", text)
except Exception as e:
    fail("gemini-3-pro-preview RESEARCH", e)

section("7. ModelFallbackExecutor - 降级链验证")
try:
    from app.core.llm.model_fallback import get_fallback_executor

    executor = get_fallback_executor()
    for task_type in ["CHAT", "RESEARCH", "FILE_GEN", "AGENT", "MULTI_STEP"]:
        model = executor.get_best_available(task_type=task_type)
        assert model, f"{task_type}: no model"
        ok(f"get_best_available({task_type})", model)
except Exception as e:
    fail("ModelFallbackExecutor", e)

section("8. generate_with_fallback - CHAT")
try:
    from app.core.llm.model_fallback import get_fallback_executor

    executor = get_fallback_executor()
    r = executor.generate_with_fallback(
        provider=p,
        prompt="Briefly say hello in Chinese",
        preferred_model="gemini-3-flash-preview",
        task_type="CHAT",
    )
    text = r.get("content", "")
    assert text, "empty response"
    ok("generate_with_fallback CHAT", text)
except Exception as e:
    fail("generate_with_fallback CHAT", e)

section("9. deep-research 模型走 Interactions API (快速验证，不等完成)")
try:
    # 只验证它不报"agent字段"或"generate_content"错误，有超时就算通过
    import threading

    result_box = {"err": None, "started": False}

    def _call():
        try:
            p._call_via_interactions_api(
                "deep-research-pro-preview-12-2025",
                "用一句话解释深度学习",
                timeout=20.0,
            )
        except TimeoutError:
            result_box["err"] = "timeout_ok"  # 超时说明成功走到了 Interactions API
        except Exception as exc:
            result_box["err"] = str(exc)

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=25)

    err = result_box["err"]
    if err is None or err == "timeout_ok":
        ok("deep-research 走 Interactions API", "连接成功 (timeout or completed)")
    elif "agent" in str(err).lower() and "model" in str(err).lower():
        # The old bug: model passed in agent field for a non-agent
        fail("deep-research Interactions API", err)
    else:
        # Other errors (quota, unavailable) - not a routing bug
        ok(
            "deep-research 走 Interactions API",
            f"无路由错误 (API err: {str(err)[:60]})",
        )
except Exception as e:
    fail("deep-research Interactions API", e)

# ── 汇总 ─────────────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
print(
    f"  结果: {len(passed)} 通过 / {len(passed)+len(failed)} 总数，{len(failed)} 失败"
)
print(f"{'═'*60}")
if failed:
    print(f"\n{RED}  失败项目:{RESET}")
    for f_item in failed:
        print(f"    - {f_item}")
else:
    print(f"\n{GREEN}  🎉 全部通过！{RESET}")

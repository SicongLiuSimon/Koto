"""
phase2_smoke_test.py — Phase 2 全模块烟雾测试
==============================================
验证所有 Phase 2 新增/修改模块的基础功能。
"""
import sys, os
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import json, tempfile, time, traceback

PASS = []
FAIL = []

def ok(label):
    PASS.append(label)
    print(f"  ✅ {label}")

def fail(label, err=None):
    FAIL.append(label)
    msg = f"  ❌ {label}"
    if err:
        msg += f" → {err}"
    print(msg)

# ── 1. token_tracker 扩展 ─────────────────────────────────────────────────────
print("\n[1] token_tracker 扩展测试")
try:
    _web_path = os.path.join(ROOT, "web")
    if _web_path not in sys.path:
        sys.path.insert(0, _web_path)
    import token_tracker
    token_tracker.record_usage_with_skill(
        model="gemini-2.5-flash",
        prompt_tokens=100,
        completion_tokens=50,
        skill_id="test_skill",
        session_id="sess_abc",
    )
    ok("record_usage_with_skill 无错误")

    stats = token_tracker.get_skill_stats("test_skill")
    assert "test_skill" in stats, f"expected test_skill in stats: {stats}"
    assert stats["test_skill"]["total_calls"] >= 1
    ok("get_skill_stats 返回 test_skill 数据")

    base_stats = token_tracker.get_stats()
    assert "today" in base_stats
    ok("get_stats 仍包含 today 字段")

    # !! 移除 web/ 以防 'app' 被 web/app.py 覆盖
    if _web_path in sys.path:
        sys.path.remove(_web_path)
    # 若 web/app.py 已被缓存为 'app' 模块，移除缓存
    sys.modules.pop("app", None)
except Exception as e:
    fail("token_tracker 扩展", traceback.format_exc(limit=2))

# ── 2. SkillRecorder ─────────────────────────────────────────────────────────
print("\n[2] SkillRecorder 测试")
try:
    from app.core.skills.skill_recorder import SkillRecorder, _make_skill_id

    sid = _make_skill_id("邮件起草助手")
    assert sid, f"skill_id 生成失败"
    ok(f"_make_skill_id → '{sid}'")

    sd = SkillRecorder.from_text(
        user_input="帮我写一封正式的商务邮件",
        ai_response="尊敬的xxx...",
        skill_name="邮件起草",
        description="帮助用户起草各类邮件",
    )
    assert sd.id, f"skill_id 为空"
    assert sd.input_variables, "input_variables 为空"
    assert sd.system_prompt_template, "system_prompt_template 为空"
    ok(f"from_text 构建 SkillDefinition 成功 (id='{sd.id}')")
    ok(f"  input_variables = {[v.name for v in sd.input_variables]}")
    ok(f"  tags = {sd.tags}")

    # 序列化往返
    d = sd.to_dict()
    assert "id" in d and ("system_prompt_template" in d or "system_prompt" in d)
    ok("to_dict 序列化正常")

except Exception as e:
    fail("SkillRecorder", traceback.format_exc(limit=3))

# ── 3. skill_routes Blueprint import ────────────────────────────────────────
print("\n[3] skill_routes Blueprint 导入测试")
try:
    from app.api.skill_routes import skill_bp
    assert skill_bp.name == "skills"
    ok("skill_bp Blueprint 导入成功")

    # 检查所有端点已定义
    from app.api.skill_routes import list_skills, create_skill, get_skill, update_skill, delete_skill, toggle_skill, record_from_session, export_mcp_tools, skill_stats
    ok("所有 CRUD 端点函数已定义 (9个)")
except Exception as e:
    fail("skill_routes", traceback.format_exc(limit=3))

# ── 4. LoRAPipeline ───────────────────────────────────────────────────────────
print("\n[4] LoRAPipeline 测试")
try:
    from app.core.learning.lora_pipeline import LoRAPipeline, TrainingConfig, AdapterMeta, get_pipeline

    pipeline = get_pipeline()
    ok("get_pipeline() 单例创建成功")

    all_ok, missing = pipeline.check_prerequisites()
    if all_ok:
        ok("所有 LoRA 依赖已安装")
    else:
        ok(f"骨架模式: 缺少依赖 {missing} (正常，可选安装)")

    # 骨架训练（无真实数据，直接测试 missing dataset 路径）
    result = pipeline.train("nonexistent_skill_xyz")
    assert not result["success"]
    assert "数据集" in result.get("error", "")
    ok("train() 在数据不足时返回 success=False (符合预期)")

    # AdapterMeta 序列化
    meta = AdapterMeta(
        skill_id="test", adapter_path="/tmp/adapter", base_model="qwen",
        trained_at="2025-01-01T00:00:00", num_samples=100, num_epochs=3
    )
    d = meta.to_dict()
    restored = AdapterMeta.from_dict(d)
    assert restored.skill_id == "test"
    ok("AdapterMeta 序列化/反序列化正常")

except Exception as e:
    fail("LoRAPipeline", traceback.format_exc(limit=3))

# ── 5. agent_routes 修改验证 ─────────────────────────────────────────────────
print("\n[5] agent_routes 修改验证")
try:
    # 检查文件是否包含新代码
    routes_path = os.path.join(ROOT, "app", "api", "agent_routes.py")
    with open(routes_path, "r", encoding="utf-8") as f:
        content = f.read()

    checks = [
        ("_lazy_pii", "PIIFilter 懒加载"),
        ("_lazy_validator", "OutputValidator 懒加载"),
        ("_lazy_tracer", "ShadowTracer 懒加载"),
        ("/feedback", "feedback 端点定义"),
        ("/stats/cost", "cost stats 端点"),
        ("pii_masked", "/chat 含 pii_masked 字段"),
        ("validation_action", "/chat 含 validation_action 字段"),
        ("process-stream", "/process-stream 路由"),
        ("safe_request", "/process-stream PII 接入"),
    ]
    for keyword, label in checks:
        if keyword in content:
            ok(label)
        else:
            fail(label, f"'{keyword}' 未找到")

except Exception as e:
    fail("agent_routes 验证", traceback.format_exc(limit=2))

# ── 6. token_tracker __init__ 导出 ───────────────────────────────────────────
print("\n[6] learning/__init__ 导出验证")
try:
    from app.core.learning import LoRAPipeline, ShadowTracer, TrainingConfig
    ok("learning.__init__ 导出 LoRAPipeline, ShadowTracer, TrainingConfig")
except Exception as e:
    fail("learning.__init__ 导出", traceback.format_exc(limit=2))

# ── 7. skills/__init__ 导出 ───────────────────────────────────────────────────
print("\n[7] skills/__init__ 导出验证")
try:
    from app.core.skills import SkillRecorder, SkillDefinition, SkillManager
    ok("skills.__init__ 导出 SkillRecorder, SkillDefinition, SkillManager")
except Exception as e:
    fail("skills.__init__ 导出", traceback.format_exc(limit=2))

# ── 8. web/app.py RouterDecision 接入 ────────────────────────────────────────
print("\n[8] web/app.py RouterDecision 接入验证")
try:
    app_path = os.path.join(ROOT, "web", "app.py")
    with open(app_path, "r", encoding="utf-8") as f:
        app_content = f.read()
    assert "classify_v2" in app_content
    ok("web/app.py 包含 classify_v2 调用")
    assert "_router_decision" in app_content
    ok("web/app.py 包含 _router_decision 变量")
    assert "skill_bp" in app_content
    ok("web/app.py 注册了 skill_bp Blueprint")
except Exception as e:
    fail("web/app.py RouterDecision", traceback.format_exc(limit=2))

# ── 结果摘要 ──────────────────────────────────────────────────────────────────
print("\n" + "="*60)
total = len(PASS) + len(FAIL)
print(f"Phase 2 烟雾测试结果: {len(PASS)}/{total} 通过")
if FAIL:
    print(f"\n❌ 失败项目:")
    for f_ in FAIL:
        print(f"  - {f_}")
    sys.exit(1)
else:
    print("\n🎉 所有测试通过！Phase 2 模块集成验证完成。")
    sys.exit(0)

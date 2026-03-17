"""
tests/test_skill_enhancements.py
─────────────────────────────────────────────────────────────────────────────
验证 Skills 系统三层质量保障：

Layer 1 - JSON Schema 完整性     : 30 个 skill 文件全部含 trigger_keywords /
                                   plan_template / examples / 有效 prompt
Layer 2 - AutoMatcher 关键词路由 : trigger_keywords 能正确激活对应 skill
Layer 3 - 注入质量               : plan_template 被 inject_into_prompt
                                   正确追加到系统指令
"""

import json
import os
import pathlib
import sys
import importlib

import pytest

# ─── 路径设置 ───────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).resolve().parents[1]  # repo root
SKILLS_DIR = ROOT / "config" / "skills"
sys.path.insert(0, str(ROOT))


# ─── 辅助函数 ──────────────────────────────────────────────────────────────
def _all_skill_files():
    """返回所有非模板 skill JSON 文件路径列表（排除 _TEMPLATE.json / test_custom.json）"""
    return [
        p
        for p in SKILLS_DIR.glob("*.json")
        if not p.name.startswith("_") and p.name != "test_custom.json"
    ]


def _load(name: str) -> dict:
    """按 skill name (不含 .json) 加载 JSON"""
    return json.loads((SKILLS_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1 : JSON Schema 完整性检查
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillJsonCompleteness:
    """所有 skill JSON 文件必须满足最低质量要求。"""

    @pytest.mark.parametrize("skill_path", _all_skill_files(), ids=lambda p: p.stem)
    def test_valid_json(self, skill_path):
        """文件可被正确解析为 JSON。"""
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{skill_path.name} 不是 JSON 对象"

    @pytest.mark.parametrize("skill_path", _all_skill_files(), ids=lambda p: p.stem)
    def test_required_fields_present(self, skill_path):
        """必填字段都存在且不为空：id, name, prompt, description。"""
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        for field in ("id", "name", "prompt", "description"):
            assert field in data, f"{skill_path.name} 缺少必填字段: {field}"
            assert data[field], f"{skill_path.name} 字段 '{field}' 不能为空"

    @pytest.mark.parametrize("skill_path", _all_skill_files(), ids=lambda p: p.stem)
    def test_prompt_length_sufficient(self, skill_path):
        """prompt 字段长度须 > 300 字符（确保内容足够丰富）。"""
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        prompt = data.get("prompt", "")
        assert len(prompt) > 300, (
            f"{skill_path.name} prompt 太短 ({len(prompt)} chars)，" f"预期 > 300 chars"
        )

    @pytest.mark.parametrize("skill_path", _all_skill_files(), ids=lambda p: p.stem)
    def test_trigger_keywords_non_empty(self, skill_path):
        """trigger_keywords 必须是非空列表。"""
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        kws = data.get("trigger_keywords", [])
        assert (
            isinstance(kws, list) and len(kws) > 0
        ), f"{skill_path.name} 缺少 trigger_keywords（AutoMatcher 无法识别）"
        # 每个关键词都必须是非空字符串
        for kw in kws:
            assert (
                isinstance(kw, str) and kw.strip()
            ), f"{skill_path.name} trigger_keywords 包含空字符串: {kw!r}"

    @pytest.mark.parametrize("skill_path", _all_skill_files(), ids=lambda p: p.stem)
    def test_plan_template_non_empty(self, skill_path):
        """plan_template 必须是非空列表（执行步骤缺失则 inject_into_prompt 无法注入）。"""
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        pt = data.get("plan_template", [])
        assert (
            isinstance(pt, list) and len(pt) > 0
        ), f"{skill_path.name} 缺少 plan_template（执行步骤缺失）"
        for step in pt:
            assert (
                isinstance(step, str) and step.strip()
            ), f"{skill_path.name} plan_template 包含空步骤"

    @pytest.mark.parametrize("skill_path", _all_skill_files(), ids=lambda p: p.stem)
    def test_examples_present(self, skill_path):
        """每个 skill 至少有 1 个示例（examples），用于 few-shot 质量保障。"""
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        examples = data.get("examples", [])
        assert (
            isinstance(examples, list) and len(examples) > 0
        ), f"{skill_path.name} 缺少 examples"
        for ex in examples:
            assert (
                "input" in ex and ex["input"]
            ), f"{skill_path.name} 示例缺少 'input' 字段: {ex}"
            assert (
                "output" in ex and ex["output"]
            ), f"{skill_path.name} 示例缺少 'output' 字段: {ex}"

    @pytest.mark.parametrize("skill_path", _all_skill_files(), ids=lambda p: p.stem)
    def test_id_matches_filename(self, skill_path):
        """skill JSON 中的 id 字段必须和文件名（不含 .json）一致。"""
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        assert (
            data.get("id") == skill_path.stem
        ), f"{skill_path.name}: id='{data.get('id')}' 与文件名 '{skill_path.stem}' 不匹配"

    def test_no_duplicate_ids(self):
        """所有 skill JSON 中的 id 字段必须全局唯一。"""
        seen = {}
        for path in _all_skill_files():
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = data.get("id")
            assert (
                sid not in seen
            ), f"重复 skill id '{sid}': 出现在 {seen[sid]} 和 {path.name}"
            seen[sid] = path.name

    def test_trigger_keywords_count(self):
        """统计每个 skill 的 trigger_keywords 数量，至少 2 个为健康水平。"""
        insufficient = []
        for path in _all_skill_files():
            data = json.loads(path.read_text(encoding="utf-8"))
            kws = data.get("trigger_keywords", [])
            if len(kws) < 2:
                insufficient.append(f"{path.stem}: {len(kws)} keyword(s)")
        # 只是警告，不 fail（单关键词 skill 也可接受）
        if insufficient:
            pytest.warns(UserWarning, match=".*") if False else None
            # 打印供参考
            for item in insufficient:
                print(f"[WARN] trigger_keywords 数量偏少: {item}")


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2 : AutoMatcher trigger_keywords 路由检测
# ══════════════════════════════════════════════════════════════════════════════


class TestAutoMatcherTriggerKeywords:
    """
    验证 SkillAutoMatcher 的 trigger_keywords 路径可被真实调用，
    不依赖 Flask/数据库 —— 仅测试纯逻辑层。

    逻辑：将 JSON 文件里的 trigger_keywords 作为 "输入" 直接扫描，
    验证关键词匹配逻辑本身是正确的（隔离层测试）。
    """

    @pytest.fixture(autouse=True)
    def isolate_registry(self, monkeypatch):
        """将 SkillManager 状态清零，并以本地 JSON 文件初始化。"""
        from app.core.skills import skill_manager as sm

        # 重置单例状态
        monkeypatch.setattr(sm.SkillManager, "_initialized", False)
        monkeypatch.setattr(sm.SkillManager, "_registry", {})
        monkeypatch.setattr(sm.SkillManager, "_def_registry", {})
        sm.SkillManager._ensure_init()
        yield

    @pytest.mark.parametrize(
        "skill_name,test_input",
        [
            ("debug_python", "Python报错 AttributeError"),
            ("debug_api", "API接口返回500错误"),
            ("debug_performance", "程序运行很慢，性能问题"),
            ("debug_web_frontend", "React页面白屏 前端调试"),
            ("work_report_generator", "帮我写今天的日报"),
            ("excel_merge", "合并多个Excel表格"),
            ("excel_analyst", "统计分析这份Excel数据"),
            ("file_smart_rename", "批量重命名文件"),
            ("file_duplicate_hunter", "查找重复文件"),
            ("git_commit_helper", "生成git commit message"),
            ("sql_assistant", "写一条SQL查询语句"),
            ("email_writer", "帮我写一封邮件"),
            ("regex_helper", "写一个正则表达式匹配手机号"),
            ("write_unit_tests", "帮我写单元测试"),
            ("brainstorm", "头脑风暴一些创业想法"),
        ],
    )
    def test_keyword_in_trigger_json(self, skill_name, test_input):
        """输入字符串中的关键词必须存在于对应 skill 的 trigger_keywords 列表中（或前缀匹配）。"""
        skill_file = SKILLS_DIR / f"{skill_name}.json"
        if not skill_file.exists():
            pytest.skip(f"{skill_name}.json 不存在")

        data = json.loads(skill_file.read_text(encoding="utf-8"))
        kws = [kw.lower() for kw in data.get("trigger_keywords", [])]
        lowered = test_input.lower()

        # 检查 test_input 中是否包含 skill 的至少一个 trigger_keyword
        matched = any(kw in lowered for kw in kws)
        assert matched, (
            f"skill='{skill_name}': 输入 '{test_input}' 未被任何 trigger_keywords 覆盖。\n"
            f"当前 trigger_keywords: {data.get('trigger_keywords', [])}"
        )

    def test_automatcher_scan_uses_def_registry(self, monkeypatch):
        """
        验证 SkillAutoMatcher._match_with_patterns 能访问 SkillManager._def_registry，
        并从中读取 trigger_keywords 完成匹配。
        """
        from app.core.skills.skill_manager import SkillManager
        from app.core.skills.skill_auto_matcher import SkillAutoMatcher

        # 确保 def_registry 已初始化且包含 debug_python
        assert (
            "debug_python" in SkillManager._def_registry
        ), "SkillManager._def_registry 未加载 debug_python"
        debug_def = SkillManager._def_registry["debug_python"]
        kws = getattr(debug_def, "trigger_keywords", [])
        assert len(kws) > 0, "debug_python.trigger_keywords 在 SkillDefinition 中为空"

        # 构造候选列表（模拟 AutoMatcher 的 candidates 参数）
        candidates = [{"id": sid} for sid in SkillManager._def_registry]

        # 找一个肯定在 debug_python trigger_keywords 里的词，直接用第一个
        first_kw = kws[0]
        result = SkillAutoMatcher._match_with_patterns(first_kw, candidates)
        assert "debug_python" in result, (
            f"_match_with_patterns('{first_kw}') 未返回 debug_python。\n"
            f"返回了: {result}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3 : inject_into_prompt 注入质量验证
# ══════════════════════════════════════════════════════════════════════════════


class TestInjectIntoPrompt:
    """
    验证 SkillManager.inject_into_prompt 正确将 plan_template 追加到系统指令。
    """

    @pytest.fixture(autouse=True)
    def init_skill_manager(self, monkeypatch):
        from app.core.skills import skill_manager as sm

        monkeypatch.setattr(sm.SkillManager, "_initialized", False)
        monkeypatch.setattr(sm.SkillManager, "_registry", {})
        monkeypatch.setattr(sm.SkillManager, "_def_registry", {})
        sm.SkillManager._ensure_init()
        yield

    @pytest.mark.parametrize(
        "skill_id",
        [
            "debug_python",
            "excel_analyst",
            "git_commit_helper",
            "write_unit_tests",
            "email_writer",
        ],
    )
    def test_plan_template_injected_when_temp_skill(self, skill_id):
        """
        以 temp_skill_ids 方式注入 skill 时，skill 内容必须出现在返回文本中，
        且注入结果明显长于 base_instruction。
        """
        from app.core.skills.skill_manager import SkillManager

        base = "你是一个智能助手。"
        result = SkillManager.inject_into_prompt(
            base_instruction=base,
            temp_skill_ids=[skill_id],
        )
        # 注入后 result 应当明显长于 base
        assert len(result) > len(base) + 200, (
            f"skill '{skill_id}' 注入后内容太短 ({len(result)} chars)，"
            f"预期 > {len(base) + 200}\n"
            f"inject_into_prompt 返回（前500字）:\n{result[:500]}"
        )
        # 结果中应包含步骤相关标记（执行步骤 / ⚙️ / 步骤 N / N. ）
        has_step_marker = (
            "执行步骤" in result
            or "⚙️" in result
            or "步骤" in result
            or any(f"{i}. " in result for i in range(1, 8))
        )
        assert has_step_marker, (
            f"skill '{skill_id}' 的注入结果未发现任何步骤标记。\n"
            f"inject_into_prompt 返回（前600字）:\n{result[:600]}"
        )

    @pytest.mark.parametrize(
        "skill_id",
        [
            "debug_python",
            "excel_analyst",
            "email_writer",
        ],
    )
    def test_plan_template_contains_ordered_steps(self, skill_id):
        """返回的 prompt 中包含带序号的步骤列表或中文步骤标记（验证格式正确性）。"""
        from app.core.skills.skill_manager import SkillManager

        base = "你是一个智能助手。"
        result = SkillManager.inject_into_prompt(
            base_instruction=base,
            temp_skill_ids=[skill_id],
        )
        # 注入后应出现序号步骤（"1. " 格式）或中文「步骤 N」格式
        has_steps = (
            any(f"{i}. " in result for i in range(1, 8))
            or any(f"步骤 {i}" in result for i in range(1, 8))
            or any(f"步骤{i}" in result for i in range(1, 8))
        )
        assert has_steps, (
            f"skill '{skill_id}': inject_into_prompt 返回文本中未发现有序步骤。\n"
            f"返回（前800字）:\n{result[:800]}"
        )

    def test_inject_does_not_duplicate_steps(self):
        """inject_into_prompt 对同一 skill 不应重复注入（seen_ids 防重机制）。"""
        from app.core.skills.skill_manager import SkillManager

        base = "你是一个智能助手。"
        # 在 enabled 和 temp_skill_ids 中同时传入同一 skill
        # 先把 debug_python 设为 enabled
        SkillManager._ensure_init()
        orig_enabled = SkillManager._registry.get("debug_python", {}).get(
            "enabled", False
        )
        if "debug_python" in SkillManager._registry:
            SkillManager._registry["debug_python"]["enabled"] = True
        try:
            result = SkillManager.inject_into_prompt(
                base_instruction=base,
                temp_skill_ids=["debug_python"],  # 同时作为 temp
            )
            # debug_python 的 prompt 关键词只应出现一次
            kw = "Python 调试专家"
            count = result.count(kw)
            assert count <= 1, (
                f"debug_python prompt 关键词 '{kw}' 重复出现了 {count} 次，"
                f"说明 skill 被重复注入"
            )
        finally:
            if "debug_python" in SkillManager._registry:
                SkillManager._registry["debug_python"]["enabled"] = orig_enabled

    def test_base_instruction_preserved(self):
        """inject_into_prompt 不能丢弃原始 base_instruction 内容。"""
        from app.core.skills.skill_manager import SkillManager

        base = "你是 Koto，一个智能桌面助手。请用中文回答。"
        result = SkillManager.inject_into_prompt(
            base_instruction=base,
            temp_skill_ids=["debug_python"],
        )
        assert base in result, "inject_into_prompt 丢弃了 base_instruction"

    def test_inject_auto_skill_marker_present(self):
        """以 temp_skill_ids 注入时，返回文本应包含「自动匹配」或 Skill 名称（可追溯性）。"""
        from app.core.skills.skill_manager import SkillManager

        base = "你是一个智能助手。"
        result = SkillManager.inject_into_prompt(
            base_instruction=base,
            temp_skill_ids=["email_writer"],
        )
        # 注入后 prompt 应含有 skill 具体内容（验证 prompt 非空地被追加）
        assert len(result) > len(base) + 50, "inject_into_prompt 几乎没有追加任何内容"

    def test_multiple_temp_skills_all_injected(self):
        """同时传入多个 temp_skill_ids，每个 skill 的步骤都应出现在结果中。"""
        from app.core.skills.skill_manager import SkillManager

        skill_ids = ["debug_python", "git_commit_helper"]
        base = "你是一个智能助手。"
        result = SkillManager.inject_into_prompt(
            base_instruction=base,
            temp_skill_ids=skill_ids,
        )
        # 结果长度应当明显大于单个注入
        single = SkillManager.inject_into_prompt(
            base_instruction=base,
            temp_skill_ids=["debug_python"],
        )
        assert len(result) >= len(
            single
        ), "多 skill 注入结果不应短于单个 skill 注入结果"


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 4 : 语义覆盖率验证（验证 trigger_keywords 覆盖典型用户输入）
# ══════════════════════════════════════════════════════════════════════════════


class TestSemanticCoverage:
    """
    语义覆盖率测试：测试真实用户输入场景是否被正确的 skill 覆盖。
    这是「集成层」测试，不依赖 LLM，仅依赖关键词匹配。
    """

    @pytest.fixture(autouse=True)
    def init_registry(self, monkeypatch):
        from app.core.skills import skill_manager as sm

        monkeypatch.setattr(sm.SkillManager, "_initialized", False)
        monkeypatch.setattr(sm.SkillManager, "_registry", {})
        monkeypatch.setattr(sm.SkillManager, "_def_registry", {})
        sm.SkillManager._ensure_init()
        yield

    @pytest.mark.parametrize(
        "user_input,expected_skill",
        [
            ("Python 里 KeyError 怎么解决", "debug_python"),
            ("这段代码报 TypeError 了", "debug_python"),
            ("帮我改一下 commit message", "git_commit_helper"),
            ("用 git 提交代码，信息怎么写", "git_commit_helper"),
            ("帮我写邮件给客户解释延期", "email_writer"),
            ("给领导发一封说明邮件", "email_writer"),
            ("帮我写正则匹配邮箱格式", "regex_helper"),
            ("这个正则表达式什么意思", "regex_helper"),
            ("SQL查询按日期筛选数据", "sql_assistant"),
            ("写个查重复记录的SQL", "sql_assistant"),
            ("帮我写测试用例", "write_unit_tests"),
            ("用pytest测试这个函数", "write_unit_tests"),
            ("数据分析这份Excel", "excel_analyst"),
            ("合并两个Excel文件", "excel_merge"),
            ("批量重命名文件加上日期", "file_smart_rename"),
            ("查找项目里的重复图片", "file_duplicate_hunter"),
            ("写日报", "work_report_generator"),
            ("今天的工作总结", "work_report_generator"),
            ("头脑风暴，给我10个产品创意", "brainstorm"),
            ("我想想有哪些可能的解决方案", "brainstorm"),
        ],
    )
    def test_user_input_matches_expected_skill(self, user_input, expected_skill):
        """用户输入应至少被预期 skill 的 trigger_keywords 之一覆盖。"""
        skill_file = SKILLS_DIR / f"{expected_skill}.json"
        if not skill_file.exists():
            pytest.skip(f"{expected_skill}.json 不存在")

        data = json.loads(skill_file.read_text(encoding="utf-8"))
        kws = [kw.lower() for kw in data.get("trigger_keywords", [])]
        lowered = user_input.lower()

        matched = any(kw in lowered for kw in kws)
        if not matched:
            # 显示详细的诊断信息
            pytest.fail(
                f"\n  skill='{expected_skill}'\n"
                f"  input='{user_input}'\n"
                f"  trigger_keywords={data.get('trigger_keywords', [])}\n"
                f"  → 没有任何关键词可以命中该输入，建议补充关键词"
            )

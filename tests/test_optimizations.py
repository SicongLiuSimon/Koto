# -*- coding: utf-8 -*-
"""
优化验证测试

覆盖4项改进：
1. ToolRouter 双层语义匹配
2. LocalPlanner.replan() 动态重规划
3. ToolRegistry 工具执行超时
4. UnifiedAgent 观测结果压缩
"""

import inspect
import sys
import time

import pytest

sys.path.insert(0, ".")


# ─────────────────────────────────────────────────────────────────────────────
# 1. ToolRouter
# ─────────────────────────────────────────────────────────────────────────────


class TestToolRouter:
    @pytest.fixture
    def router_and_tools(self):
        from app.core.routing.tool_router import ToolRouter

        router = ToolRouter(max_tools=20, semantic_topk=8)
        tools = [
            {
                "name": "analyze_excel_data",
                "description": "分析Excel数据，计算统计指标",
            },
            {"name": "web_search", "description": "Search the web 联网搜索"},
            {"name": "run_python_code", "description": "运行Python代码脚本"},
            {"name": "read_file", "description": "读取文件内容"},
            {"name": "write_file", "description": "写入文件内容"},
            {"name": "get_current_time", "description": "获取当前时间"},
            {"name": "take_screenshot", "description": "截图"},
            {"name": "shell_command", "description": "命令行执行"},
        ]
        return router, tools

    def test_token_builder_chinese(self):
        from app.core.routing.tool_router import _build_tokens

        toks = _build_tokens("分析这个CSV里的趋势")
        assert "分" in toks
        assert "析" in toks
        assert "分析" in toks  # bigram of 2-char Chinese word

    def test_token_builder_english(self):
        from app.core.routing.tool_router import _build_tokens

        toks = _build_tokens("analyze_excel_data")
        assert "analyze" in toks
        assert "excel" in toks
        assert "data" in toks

    def test_overlap_score_basic(self):
        from app.core.routing.tool_router import _build_tokens, _overlap_score

        q = _build_tokens("分析Excel数据")
        d1 = _build_tokens("分析Excel数据，计算统计指标 analyze excel data")
        d2 = _build_tokens("运行Python脚本 run python code")
        assert _overlap_score(q, d1) > _overlap_score(q, d2)

    def test_keyword_tier_web_search(self, router_and_tools):
        router, tools = router_and_tools
        selected = router.select(tools, "帮我搜索最新新闻")
        names = [t["name"] for t in selected]
        assert "web_search" in names

    def test_semantic_tier_csv_analysis(self, router_and_tools):
        """CSV分析查询无法命中关键词规则，应由语义层捞起 analyze_excel_data。"""
        router, tools = router_and_tools
        selected = router.select(tools, "分析这个CSV里的趋势")
        names = [t["name"] for t in selected]
        assert (
            "analyze_excel_data" in names
        ), f"Semantic tier should select analyze_excel_data. Got: {names}"

    def test_description_index_cached(self, router_and_tools):
        router, tools = router_and_tools
        router.select(tools, "first query")
        key_before = router._index_cache_key
        router.select(tools, "second query")
        assert (
            router._index_cache_key == key_before
        ), "Index should be reused when tool set unchanged"

    def test_index_rebuilds_on_tool_change(self, router_and_tools):
        router, tools = router_and_tools
        router.select(tools, "query")
        key_before = router._index_cache_key
        new_tools = tools + [{"name": "new_tool", "description": "新工具"}]
        router.select(new_tools, "query")
        assert (
            router._index_cache_key != key_before
        ), "Index should rebuild when tool set changes"

    def test_force_all_returns_capped(self, router_and_tools):
        router, tools = router_and_tools
        result = router.select(tools, "anything", force_all=True)
        assert len(result) == len(tools)  # 8 tools < 20 max


# ─────────────────────────────────────────────────────────────────────────────
# 2. LocalPlanner.replan()
# ─────────────────────────────────────────────────────────────────────────────


class TestLocalPlannerReplan:
    def test_replan_exists(self):
        from app.core.routing.local_planner import LocalPlanner

        assert callable(LocalPlanner.replan)

    def test_replan_signature(self):
        from app.core.routing.local_planner import LocalPlanner

        sig = inspect.signature(LocalPlanner.replan)
        params = list(sig.parameters.keys())
        for required in [
            "user_input",
            "completed_steps",
            "failed_step",
            "error",
            "next_id",
        ]:
            assert required in params, f"Missing param: {required}"

    def test_replan_prompt_format(self):
        from app.core.routing.local_planner import LocalPlanner

        assert hasattr(LocalPlanner, "REPLAN_PROMPT")
        rendered = LocalPlanner.REPLAN_PROMPT.format(
            goal="搜索黄金价格后生成Excel",
            completed_summary="步骤1 (WEB_SEARCH): 搜索黄金 → 已获取数据",
            failed_desc="步骤2 (FILE_GEN): 生成Excel失败",
            error="LibreOffice not found",
            remaining_desc="(无)",
            next_id=3,
        )
        assert "搜索黄金价格后生成Excel" in rendered
        assert "LibreOffice not found" in rendered
        assert "3" in rendered

    def test_replan_cloud_fallback_exists(self):
        from app.core.routing.local_planner import LocalPlanner

        assert callable(getattr(LocalPlanner, "_replan_with_cloud", None))


# ─────────────────────────────────────────────────────────────────────────────
# 3. PlanExecutor Dynamic Replan
# ─────────────────────────────────────────────────────────────────────────────


class TestPlanExecutorReplan:
    def test_execute_uses_step_queue(self):
        from app.core.routing.plan_executor import PlanExecutor

        src = inspect.getsource(PlanExecutor.execute)
        assert "step_queue" in src

    def test_execute_calls_attempt_replan(self):
        from app.core.routing.plan_executor import PlanExecutor

        src = inspect.getsource(PlanExecutor.execute)
        assert "_attempt_replan" in src

    def test_execute_yields_replan_event(self):
        from app.core.routing.plan_executor import PlanExecutor

        src = inspect.getsource(PlanExecutor.execute)
        assert "replan" in src

    def test_attempt_replan_exists(self):
        from app.core.routing.plan_executor import PlanExecutor

        assert hasattr(PlanExecutor, "_attempt_replan")

    def test_attempt_replan_signature(self):
        from app.core.routing.plan_executor import PlanExecutor

        sig = inspect.signature(PlanExecutor._attempt_replan)
        for p in ["failed_step", "remaining_steps", "next_id"]:
            assert p in sig.parameters, f"Missing param: {p}"

    @pytest.mark.anyio
    async def test_execute_injects_new_steps_on_failure(self):
        """Verify that new steps get injected when a step fails and replan returns steps."""
        from unittest.mock import AsyncMock, patch

        from app.core.routing.plan_executor import PlanExecutor

        steps = [
            {
                "id": 1,
                "task_type": "WEB_SEARCH",
                "description": "搜索",
                "output_key": "sr",
                "depends_on": [],
                "context_keys": [],
                "input": "搜索",
            },
            {
                "id": 2,
                "task_type": "FILE_GEN",
                "description": "生成",
                "output_key": "fg",
                "depends_on": [1],
                "context_keys": ["sr"],
                "input": "生成",
            },
        ]

        call_count = {"n": 0}

        async def failing_handler(**kwargs):
            call_count["n"] += 1
            if kwargs.get("step", {}).get("id") == 2 and call_count["n"] <= 2:
                raise RuntimeError("Simulated failure")
            return {"success": True, "output": "done"}

        handlers = {"WEB_SEARCH": failing_handler, "FILE_GEN": failing_handler}
        executor = PlanExecutor(
            steps=steps, user_input="test", handlers=handlers, max_retry=0
        )

        # Patch _attempt_replan to return a replacement step
        replacement = [
            {
                "id": 3,
                "task_type": "FILE_GEN",
                "description": "替代生成",
                "output_key": "fg2",
                "depends_on": [],
                "context_keys": [],
                "input": "替代生成",
            }
        ]
        executor._attempt_replan = AsyncMock(return_value=replacement)

        events = []
        async for event in executor.execute():
            events.append(event)

        event_types = [e.get("type") for e in events]
        assert "replan" in event_types, f"Expected replan event. Got: {event_types}"
        plan_done = next(e for e in events if e.get("type") == "plan_done")
        assert plan_done is not None


# ─────────────────────────────────────────────────────────────────────────────
# 4. ToolRegistry Timeout
# ─────────────────────────────────────────────────────────────────────────────


class TestToolRegistryTimeout:
    def test_timeout_constant_exists(self):
        from app.core.agent.tool_registry import _TOOL_TIMEOUT

        assert _TOOL_TIMEOUT == 60

    def test_fast_tool_executes_normally(self):
        from app.core.agent.tool_registry import ToolRegistry

        reg = ToolRegistry()
        reg.register_tool(
            "double",
            lambda x: x * 2,
            "double a number",
            {
                "type": "OBJECT",
                "properties": {"x": {"type": "INTEGER"}},
                "required": ["x"],
            },
        )
        result = reg.execute("double", {"x": 21})
        assert result == 42

    def test_timeout_raises_runtime_error(self):
        from app.core.agent.tool_registry import _TOOL_TIMEOUT, ToolRegistry

        reg = ToolRegistry()

        def hang():
            time.sleep(_TOOL_TIMEOUT + 30)

        reg.register_tool(
            "hang", hang, "hangs forever", {"type": "OBJECT", "properties": {}}
        )
        t0 = time.time()
        with pytest.raises(RuntimeError, match="timed out"):
            reg.execute("hang", {})
        elapsed = time.time() - t0
        assert (
            elapsed < _TOOL_TIMEOUT + 5
        ), f"Should timeout around {_TOOL_TIMEOUT}s, took {elapsed:.1f}s"

    def test_missing_tool_raises_value_error(self):
        from app.core.agent.tool_registry import ToolRegistry

        reg = ToolRegistry()
        with pytest.raises(ValueError, match="not found"):
            reg.execute("nonexistent_tool", {})


# ─────────────────────────────────────────────────────────────────────────────
# 5. UnifiedAgent Observation Compression
# ─────────────────────────────────────────────────────────────────────────────


class TestUnifiedAgentCompression:
    def test_threshold_constant(self):
        from app.core.agent.unified_agent import UnifiedAgent

        assert UnifiedAgent._OBS_COMPRESS_THRESHOLD == 3000

    def test_compress_observation_exists(self):
        from app.core.agent.unified_agent import UnifiedAgent

        assert callable(getattr(UnifiedAgent, "_compress_observation", None))

    def test_compress_observation_passthrough_short(self):
        """Short text should be returned unchanged without LLM call."""
        from unittest.mock import MagicMock

        from app.core.agent.unified_agent import UnifiedAgent

        agent = UnifiedAgent.__new__(UnifiedAgent)
        agent._OBS_COMPRESS_THRESHOLD = 3000
        agent.llm = MagicMock()
        short_text = "x" * 100
        result = agent._compress_observation(short_text, "test_tool")
        agent.llm.generate_content.assert_not_called()
        assert result == short_text

    def test_compress_observation_uses_flash_lite(self):
        from app.core.agent.unified_agent import UnifiedAgent

        src = inspect.getsource(UnifiedAgent._compress_observation)
        assert "gemini-2.0-flash-lite" in src

    def test_compress_integrated_in_exec_one(self):
        from app.core.agent.unified_agent import UnifiedAgent

        src = inspect.getsource(UnifiedAgent.run)
        assert "_compress_observation" in src

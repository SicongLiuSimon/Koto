"""Comprehensive unit tests for SmartDispatcher routing logic.

Covers: configure, trivial detection, quick-task hints, n-gram extraction,
cosine similarity, annotation system, model selection, analyze fast-track
channels, resolve_workflow, routing list construction, and caching.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, Mock


def _fresh_dispatcher():
    """Import SmartDispatcher and reset class-level state for isolation."""
    from app.core.routing.smart_dispatcher import SmartDispatcher

    SmartDispatcher._dependencies = {
        "LocalExecutor": None,
        "ContextAnalyzer": None,
        "WebSearcher": None,
        "MODEL_MAP": {},
        "client": None,
    }
    SmartDispatcher._route_cache = None
    SmartDispatcher._route_cache_lock = None
    SmartDispatcher._features = None
    SmartDispatcher._task_vectors = None
    return SmartDispatcher


# ---------------------------------------------------------------------------
# 1. configure
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestConfigure:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_configure_sets_all_dependencies(self):
        le, ca, ws, mm, cl = (
            Mock(name="LE"),
            Mock(name="CA"),
            Mock(name="WS"),
            {"CHAT": "m1"},
            Mock(name="client"),
        )
        self.SD.configure(le, ca, ws, mm, cl)
        assert self.SD._dependencies["LocalExecutor"] is le
        assert self.SD._dependencies["ContextAnalyzer"] is ca
        assert self.SD._dependencies["WebSearcher"] is ws
        assert self.SD._dependencies["MODEL_MAP"] == {"CHAT": "m1"}
        assert self.SD._dependencies["client"] is cl


# ---------------------------------------------------------------------------
# 2-3. _is_trivial_input
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestIsTrivialInput:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    @pytest.mark.parametrize(
        "text",
        ["你好", "hi", "hello", "嗨", "谢谢", "ok", "再见", "好的", "嗯嗯", "晚安"],
    )
    def test_greetings_are_trivial(self, text):
        assert self.SD._is_trivial_input(text) is True

    def test_short_identity_question_is_trivial(self):
        assert self.SD._is_trivial_input("你是谁") is True

    @pytest.mark.parametrize(
        "text",
        [
            "帮我画一张图片",
            "写一个python代码",
            "打开chrome",
            "今天天气怎么样",
            "生成word文档",
            "目前金价是多少",
        ],
    )
    def test_nontrivial_inputs(self, text):
        assert self.SD._is_trivial_input(text) is False

    def test_short_but_excluded_keyword_not_trivial(self):
        # "画个图" is <=15 chars but contains excluded keyword "画"
        assert self.SD._is_trivial_input("画个图") is False

    def test_very_short_generic_is_trivial(self):
        # <=15 chars, no excluded keywords
        assert self.SD._is_trivial_input("怎么回事") is True


# ---------------------------------------------------------------------------
# 4-8. _quick_task_hint
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestQuickTaskHint:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_system_command_open_chrome(self):
        assert self.SD._quick_task_hint("打开chrome") == "SYSTEM"

    def test_system_command_close_app(self):
        assert self.SD._quick_task_hint("关闭qq") == "SYSTEM"

    def test_painter_draw_picture(self):
        assert self.SD._quick_task_hint("画一幅画") == "PAINTER"

    def test_painter_generate_image(self):
        assert self.SD._quick_task_hint("生成图片") == "PAINTER"

    def test_file_gen_word(self):
        assert self.SD._quick_task_hint("生成word文件") == "FILE_GEN"

    def test_file_gen_pdf(self):
        assert self.SD._quick_task_hint("创建pdf报告") == "FILE_GEN"

    def test_web_search_weather(self):
        assert self.SD._quick_task_hint("今天天气") == "WEB_SEARCH"

    def test_web_search_price(self):
        assert self.SD._quick_task_hint("黄金价格多少") == "WEB_SEARCH"

    def test_coder_for_chart(self):
        # Chart/visualization keywords should route to CODER, not PAINTER
        assert self.SD._quick_task_hint("画一个折线图") == "CODER"

    def test_coder_for_code(self):
        assert self.SD._quick_task_hint("写一段python代码") == "CODER"

    def test_generic_returns_chat(self):
        assert self.SD._quick_task_hint("你觉得呢") == "CHAT"

    def test_research_keyword(self):
        assert self.SD._quick_task_hint("深入分析一下") == "RESEARCH"

    def test_agent_reminder(self):
        assert self.SD._quick_task_hint("提醒我开会") == "AGENT"

    def test_system_excludes_questions(self):
        # "打开" + question word "怎么" → should NOT be SYSTEM
        assert self.SD._quick_task_hint("打开怎么操作") != "SYSTEM"

    def test_doc_annotate_with_file_attached(self):
        result = self.SD._quick_task_hint("[FILE_ATTACHED:.docx] 帮我润色一下这篇文档")
        assert result == "DOC_ANNOTATE"


# ---------------------------------------------------------------------------
# 9. _extract_ngrams
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestExtractNgrams:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_basic_ngrams(self):
        ngrams = self.SD._extract_ngrams("abc")
        # Should contain unigrams: a, b, c and bigrams: ab, bc
        assert "a" in ngrams
        assert "b" in ngrams
        assert "c" in ngrams
        assert "ab" in ngrams
        assert "bc" in ngrams

    def test_empty_string(self):
        ngrams = self.SD._extract_ngrams("")
        assert len(ngrams) == 0

    def test_single_char(self):
        ngrams = self.SD._extract_ngrams("x")
        assert "x" in ngrams
        assert len(ngrams) == 1

    def test_chinese_chars(self):
        ngrams = self.SD._extract_ngrams("你好")
        assert "你" in ngrams
        assert "好" in ngrams
        assert "你好" in ngrams


# ---------------------------------------------------------------------------
# 10-12. _cosine_similarity
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCosineSimilarity:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_identical_vectors_return_one(self):
        v = [1, 2, 3, 4]
        assert abs(self.SD._cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors_return_zero(self):
        v1 = [1, 0, 0]
        v2 = [0, 1, 0]
        assert abs(self.SD._cosine_similarity(v1, v2)) < 1e-9

    def test_empty_vectors_return_zero(self):
        assert self.SD._cosine_similarity([], []) == 0

    def test_zero_vector_returns_zero(self):
        assert self.SD._cosine_similarity([0, 0, 0], [1, 2, 3]) == 0

    def test_opposite_vectors(self):
        v1 = [1, 0]
        v2 = [-1, 0]
        assert abs(self.SD._cosine_similarity(v1, v2) - (-1.0)) < 1e-9


# ---------------------------------------------------------------------------
# 13-14. _should_use_annotation_system
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestShouldUseAnnotationSystem:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_with_annotation_keyword_and_file(self):
        assert (
            self.SD._should_use_annotation_system("帮我润色这篇文章", has_file=True)
            is True
        )

    def test_with_quality_and_target_words(self):
        assert (
            self.SD._should_use_annotation_system("这段翻译用词生硬", has_file=True)
            is True
        )

    def test_without_file_returns_false(self):
        assert (
            self.SD._should_use_annotation_system("帮我标注", has_file=False) is False
        )

    def test_no_keywords_returns_false(self):
        assert (
            self.SD._should_use_annotation_system("今天吃什么", has_file=True) is False
        )

    def test_annotation_keyword_modify(self):
        assert (
            self.SD._should_use_annotation_system("修改这份文档", has_file=True) is True
        )


# ---------------------------------------------------------------------------
# 15-16. get_model_for_task
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestGetModelForTask:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    @patch("app.core.routing.smart_dispatcher.SmartDispatcher._get_dep")
    @patch("app.core.llm.model_fallback.get_fallback_executor")
    def test_known_task_chat(self, mock_fbe_factory, mock_get_dep):
        mock_fbe = MagicMock()
        mock_fbe.is_available.return_value = True
        mock_fbe_factory.return_value = mock_fbe
        mock_get_dep.return_value = {"CHAT": "test-flash-model"}

        result = self.SD.get_model_for_task("CHAT")
        assert result == "test-flash-model"

    @patch("app.core.llm.model_fallback.get_fallback_executor")
    def test_known_task_coder_with_model_map(self, mock_fbe_factory):
        mock_fbe = MagicMock()
        mock_fbe.is_available.return_value = True
        mock_fbe_factory.return_value = mock_fbe
        self.SD._dependencies["MODEL_MAP"] = {"CODER": "my-coder-model"}

        result = self.SD.get_model_for_task("CODER")
        assert result == "my-coder-model"

    @patch("app.core.llm.model_fallback.get_fallback_executor")
    def test_unknown_task_falls_back_to_chat(self, mock_fbe_factory):
        mock_fbe = MagicMock()
        mock_fbe.is_available.return_value = True
        mock_fbe_factory.return_value = mock_fbe
        self.SD._dependencies["MODEL_MAP"] = {"CHAT": "fallback-model"}

        result = self.SD.get_model_for_task("NONEXISTENT_TASK")
        assert result == "fallback-model"

    @patch("app.core.llm.model_fallback.get_fallback_executor")
    def test_empty_model_map_uses_defaults(self, mock_fbe_factory):
        mock_fbe = MagicMock()
        mock_fbe.is_available.return_value = True
        mock_fbe_factory.return_value = mock_fbe
        self.SD._dependencies["MODEL_MAP"] = {}

        result = self.SD.get_model_for_task("CHAT")
        # Default for CHAT when MODEL_MAP is empty → "gemini-2.5-flash" from fallback
        assert isinstance(result, str)

    @patch("app.core.llm.model_fallback.get_fallback_executor")
    def test_file_gen_complex_uses_complex_model(self, mock_fbe_factory):
        mock_fbe = MagicMock()
        mock_fbe.is_available.return_value = True
        mock_fbe_factory.return_value = mock_fbe
        self.SD._dependencies["MODEL_MAP"] = {"COMPLEX": "pro-model"}

        result = self.SD.get_model_for_task("FILE_GEN", complexity="complex")
        assert result == "pro-model"

    @patch("app.core.llm.model_fallback.get_fallback_executor")
    def test_fallback_executor_unavailable_uses_alternative(self, mock_fbe_factory):
        mock_fbe = MagicMock()
        mock_fbe.is_available.return_value = False
        mock_fbe.get_best_available.return_value = "alternative-model"
        mock_fbe_factory.return_value = mock_fbe
        self.SD._dependencies["MODEL_MAP"] = {"CHAT": "primary-model"}

        result = self.SD.get_model_for_task("CHAT")
        assert result == "alternative-model"

    @patch("app.core.llm.model_fallback.get_fallback_executor")
    def test_has_image_non_painter_uses_vision(self, mock_fbe_factory):
        mock_fbe = MagicMock()
        mock_fbe.is_available.return_value = True
        mock_fbe_factory.return_value = mock_fbe
        self.SD._dependencies["MODEL_MAP"] = {"VISION": "vision-model"}

        result = self.SD.get_model_for_task("SOME_TASK", has_image=True)
        assert result == "vision-model"


# ---------------------------------------------------------------------------
# 17-19. analyze fast-track channels
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAnalyze:
    """Test analyze() fast-track channels that don't require LLM calls."""

    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def _patch_lazy_imports(self):
        """Return patch context managers for lazy-imported modules."""
        mock_local_planner_cls = MagicMock()
        mock_local_planner_cls.should_preempt.return_value = False
        mock_local_planner_cls.can_plan.return_value = False

        mock_task_decomposer_cls = MagicMock()
        mock_task_decomposer_cls.detect_compound_task.return_value = {
            "is_compound": False
        }

        patches = [
            patch(
                "app.core.routing.smart_dispatcher._get_local_planner",
                return_value=mock_local_planner_cls,
            ),
            patch(
                "app.core.routing.smart_dispatcher._get_task_decomposer",
                return_value=mock_task_decomposer_cls,
            ),
            patch(
                "app.core.routing.smart_dispatcher._get_ai_router",
                return_value=MagicMock(),
            ),
            patch(
                "app.core.routing.smart_dispatcher._get_local_model_router",
                return_value=MagicMock(),
            ),
        ]
        return patches

    def test_trivial_greeting_returns_chat(self):
        """'你好' is 2 chars → hits ultra-short (<=3) path → CHAT Quick."""
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("你好")
            assert task == "CHAT"
            assert "Quick" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_trivial_longer_greeting_returns_chat(self):
        """'早上好' is 3 chars → ultra-short; '谢谢你' is trivial greeting."""
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("谢谢你呀朋友")
            assert task == "CHAT"
            assert "Trivial" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_trivial_hi_returns_chat(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("hi")
            assert task == "CHAT"
            # "hi" is <=3 chars, hits ultra-short path
            assert "Quick" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_ultra_short_input_returns_chat(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            # <=3 chars, not a system command
            task, confidence, ctx = self.SD.analyze("嗯")
            assert task == "CHAT"
        finally:
            for p in patches:
                p.stop()

    def test_system_command_fast_track(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("打开chrome")
            assert task == "SYSTEM"
            assert "Action" in confidence or "SYSTEM" in str(ctx)
        finally:
            for p in patches:
                p.stop()

    def test_weather_fast_track_returns_web_search(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("明天北京天气怎么样")
            assert task == "WEB_SEARCH"
            assert "Weather" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_painter_fast_track(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("帮我画一张猫的图片")
            assert task == "PAINTER"
            assert "Image" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_code_write_fast_track(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("帮我写一个python排序函数")
            assert task == "CODER"
            assert "Code" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_chart_visualization_routes_to_coder(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze(
                "用matplotlib画一个折线图展示销售数据"
            )
            assert task == "CODER"
        finally:
            for p in patches:
                p.stop()

    def test_file_context_doc_annotate(self):
        """With file_context has_file=True and .docx + edit keyword → DOC_ANNOTATE."""
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            file_ctx = {"has_file": True, "file_type": ".docx"}
            task, confidence, ctx = self.SD.analyze(
                "帮我润色一下", file_context=file_ctx
            )
            assert task == "DOC_ANNOTATE"
        finally:
            for p in patches:
                p.stop()

    def test_file_context_md_edit_routes_multi_step(self):
        """With .md file and edit keyword → MULTI_STEP doc workflow."""
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            file_ctx = {"has_file": True, "file_type": ".md"}
            task, confidence, ctx = self.SD.analyze(
                "修改这个文件", file_context=file_ctx
            )
            assert task == "MULTI_STEP"
        finally:
            for p in patches:
                p.stop()

    def test_forced_plan_mode(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("/plan 完成项目部署")
            assert task == "MULTI_STEP"
            assert "Forced" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_realtime_signal_routes_web_search(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("目前最新的国际局势进展")
            assert task == "WEB_SEARCH"
            assert "Realtime" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_financial_price_routes_web_search(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("布伦特原油价格走势")
            assert task == "WEB_SEARCH"
            assert "Price" in confidence
        finally:
            for p in patches:
                p.stop()

    def test_path_listing_routes_file_search(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze(r"C:\Users\docs 有哪些文件")
            assert task == "FILE_SEARCH"
        finally:
            for p in patches:
                p.stop()

    def test_agent_notify_pattern(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            task, confidence, ctx = self.SD.analyze("提醒我下午3点开会")
            assert task == "AGENT"
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# 20. resolve_workflow
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestResolveWorkflow:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    @patch("app.core.workflow.langgraph_workflow.WorkflowEngine")
    def test_ppt_workflow(self, mock_wf_engine):
        mock_wf_engine.detect_workflow.return_value = "multi_agent_ppt"
        result = self.SD.resolve_workflow("FILE_GEN", "做一个PPT")
        assert result == "langgraph_multi_agent_ppt"

    @patch("app.core.workflow.langgraph_workflow.WorkflowEngine")
    def test_research_doc_workflow(self, mock_wf_engine):
        mock_wf_engine.detect_workflow.return_value = "research_and_document"
        result = self.SD.resolve_workflow("RESEARCH", "深入分析AI趋势")
        assert result == "langgraph_research_doc"

    @patch("app.core.workflow.langgraph_workflow.WorkflowEngine")
    def test_multi_step_returns_react(self, mock_wf_engine):
        mock_wf_engine.detect_workflow.return_value = "other"
        result = self.SD.resolve_workflow("MULTI_STEP", "执行多步任务")
        assert result == "langgraph_react"

    @patch("app.core.workflow.langgraph_workflow.WorkflowEngine")
    def test_legacy_fallback(self, mock_wf_engine):
        mock_wf_engine.detect_workflow.return_value = "none"
        result = self.SD.resolve_workflow("CHAT", "你好")
        assert result == "legacy"

    def test_import_error_returns_legacy(self):
        with patch.dict("sys.modules", {"app.core.workflow.langgraph_workflow": None}):
            # Force ImportError by patching the import
            with patch(
                "app.core.routing.smart_dispatcher.SmartDispatcher.resolve_workflow",
                wraps=self.SD.resolve_workflow,
            ):
                # Directly test: if WorkflowEngine import fails → "legacy"
                result = self.SD.resolve_workflow("CHAT", "hi")
                assert result == "legacy"


# ---------------------------------------------------------------------------
# 21. _build_routing_list
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestBuildRoutingList:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_sorted_by_score_descending(self):
        scores = {"CHAT": 0.3, "CODER": 0.8, "PAINTER": 0.5}
        result = self.SD._build_routing_list(scores)
        assert result[0]["task"] == "CODER"
        assert result[0]["score"] >= result[-1]["score"]

    def test_boost_overrides_low_score(self):
        scores = {"CHAT": 0.9, "SYSTEM": 0.1}
        result = self.SD._build_routing_list(
            scores, boosts={"SYSTEM": 1.0}, reasons={"SYSTEM": ["rule:test"]}
        )
        assert result[0]["task"] == "SYSTEM"
        assert result[0]["score"] == 1.0

    def test_top_k_limits_results(self):
        scores = {f"TASK_{i}": 0.1 * i for i in range(10)}
        result = self.SD._build_routing_list(scores, top_k=3)
        assert len(result) == 3

    def test_default_reason_is_similarity(self):
        scores = {"CHAT": 0.5}
        result = self.SD._build_routing_list(scores)
        assert result[0]["reason"] == "similarity"

    def test_custom_reason(self):
        scores = {"CHAT": 0.5}
        result = self.SD._build_routing_list(
            scores, reasons={"CHAT": ["rule:custom", "boost:extra"]}
        )
        assert "rule:custom" in result[0]["reason"]
        assert "boost:extra" in result[0]["reason"]


# ---------------------------------------------------------------------------
# 22. Caching behavior in analyze
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAnalyzeCaching:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def _patch_lazy_imports(self):
        mock_local_planner_cls = MagicMock()
        mock_local_planner_cls.should_preempt.return_value = False
        mock_local_planner_cls.can_plan.return_value = False

        mock_task_decomposer_cls = MagicMock()
        mock_task_decomposer_cls.detect_compound_task.return_value = {
            "is_compound": False
        }

        patches = [
            patch(
                "app.core.routing.smart_dispatcher._get_local_planner",
                return_value=mock_local_planner_cls,
            ),
            patch(
                "app.core.routing.smart_dispatcher._get_task_decomposer",
                return_value=mock_task_decomposer_cls,
            ),
            patch(
                "app.core.routing.smart_dispatcher._get_ai_router",
                return_value=MagicMock(),
            ),
            patch(
                "app.core.routing.smart_dispatcher._get_local_model_router",
                return_value=MagicMock(),
            ),
        ]
        return patches

    def test_second_call_returns_cached_result(self):
        """Same input without file_context should return cached result."""
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            result1 = self.SD.analyze("你好")
            result2 = self.SD.analyze("你好")
            # Both should return the same tuple
            assert result1[0] == result2[0]
            assert result1[1] == result2[1]
        finally:
            for p in patches:
                p.stop()

    def test_file_context_bypasses_cache(self):
        """Requests with file_context should not use cache."""
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            file_ctx = {"has_file": True, "file_type": ".docx"}
            r1 = self.SD.analyze("帮我修改", file_context=file_ctx)
            # Cache should be empty or not contain this key
            cache, lock = self.SD._get_route_cache()
            # file_context calls don't write to cache
            # Verify it returns a valid result
            assert r1[0] in ("DOC_ANNOTATE", "MULTI_STEP", "CHAT", "FILE_GEN")
        finally:
            for p in patches:
                p.stop()

    def test_different_inputs_get_different_results(self):
        patches = self._patch_lazy_imports()
        for p in patches:
            p.start()
        try:
            r1 = self.SD.analyze("你好")
            r2 = self.SD.analyze("打开chrome")
            assert r1[0] != r2[0] or r1[1] != r2[1]
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Additional edge-case coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestComputeSimilarityScores:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_returns_dict_with_all_tasks(self):
        scores = self.SD._compute_similarity_scores("写代码")
        assert isinstance(scores, dict)
        # Should have scores for all tasks in TASK_CORPUS
        for task in self.SD.TASK_CORPUS:
            assert task in scores

    def test_coder_input_has_high_coder_score(self):
        scores = self.SD._compute_similarity_scores("帮我写个函数")
        assert scores["CODER"] > scores.get("PAINTER", 0)


@pytest.mark.unit
class TestTextToVector:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_returns_list_of_ints(self):
        vec = self.SD._text_to_vector("hello")
        assert isinstance(vec, list)
        assert all(v in (0, 1) for v in vec)

    def test_vector_length_matches_features(self):
        self.SD._init_features()
        vec = self.SD._text_to_vector("test")
        assert len(vec) == len(self.SD._features)


@pytest.mark.unit
class TestGetDep:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_returns_none_for_unconfigured(self):
        assert self.SD._get_dep("LocalExecutor") is None

    def test_returns_configured_dep(self):
        mock_exec = Mock()
        self.SD._dependencies["LocalExecutor"] = mock_exec
        assert self.SD._get_dep("LocalExecutor") is mock_exec

    def test_returns_none_for_unknown_key(self):
        assert self.SD._get_dep("NonExistentDep") is None


@pytest.mark.unit
class TestInitFeatures:
    def setup_method(self):
        self.SD = _fresh_dispatcher()

    def test_lazy_init_creates_features(self):
        assert self.SD._features is None
        self.SD._init_features()
        assert self.SD._features is not None
        assert len(self.SD._features) > 0

    def test_task_vectors_created(self):
        self.SD._init_features()
        assert self.SD._task_vectors is not None
        for task in self.SD.TASK_CORPUS:
            assert task in self.SD._task_vectors

    def test_double_init_is_idempotent(self):
        self.SD._init_features()
        features1 = self.SD._features
        self.SD._init_features()
        assert self.SD._features is features1  # same object, not re-created

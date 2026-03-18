"""Tests for app.core.agent.tree_of_thought module."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, Mock, patch

import pytest

_MOD = "app.core.agent.tree_of_thought"
_LLM_CLS = "app.core.llm.langchain_adapter.KotoLangChainLLM"


# ===================================================================
# 1. ThoughtBranch / ToTResult dataclass tests
# ===================================================================


class TestThoughtBranch:
    """Tests for the ThoughtBranch dataclass."""

    def test_default_values(self):
        from app.core.agent.tree_of_thought import ThoughtBranch

        b = ThoughtBranch(branch_id=1, label="test", content="hello")
        assert b.branch_id == 1
        assert b.score == 0.0
        assert b.critique == ""
        assert b.temperature == 0.7
        assert b.elapsed_sec == 0.0
        assert b.error is None

    def test_with_error(self):
        from app.core.agent.tree_of_thought import ThoughtBranch

        b = ThoughtBranch(branch_id=2, label="err", content="", error="timeout")
        assert b.error == "timeout"
        assert b.content == ""


class TestToTResult:
    """Tests for the ToTResult dataclass."""

    def test_creation(self):
        from app.core.agent.tree_of_thought import ThoughtBranch, ToTResult

        winner = ThoughtBranch(
            branch_id=1, label="w", content="winner content", score=9.0
        )
        result = ToTResult(
            winner=winner, total_elapsed_sec=5.0, critic_reason="Best quality"
        )
        assert result.winner.score == 9.0
        assert result.total_elapsed_sec == 5.0
        assert result.all_branches == []


# ===================================================================
# 2. TreeOfThought initialization
# ===================================================================


class TestTreeOfThoughtInit:
    """Tests for TreeOfThought constructor."""

    def test_default_params(self):
        from app.core.agent.tree_of_thought import TreeOfThought

        tot = TreeOfThought()
        assert tot.n_branches == 3
        assert tot.model_id == "gemini-3-flash-preview"
        assert tot.max_tokens == 6000
        assert tot.max_workers == 3
        assert tot.timeout_sec == 90

    def test_clamps_branches_to_perspectives_count(self):
        from app.core.agent.tree_of_thought import _BRANCH_PERSPECTIVES, TreeOfThought

        tot = TreeOfThought(n_branches=100)
        assert tot.n_branches == len(_BRANCH_PERSPECTIVES)

    def test_custom_model_id(self):
        from app.core.agent.tree_of_thought import TreeOfThought

        tot = TreeOfThought(model_id="custom-model")
        assert tot.model_id == "custom-model"

    def test_evaluator_model_defaults_to_model_id(self):
        from app.core.agent.tree_of_thought import TreeOfThought

        tot = TreeOfThought(model_id="my-model")
        assert tot.evaluator_model == "my-model"

    def test_evaluator_model_can_be_different(self):
        from app.core.agent.tree_of_thought import TreeOfThought

        tot = TreeOfThought(model_id="gen-model", evaluator_model="eval-model")
        assert tot.evaluator_model == "eval-model"


# ===================================================================
# 3. _llm_call
# ===================================================================


class TestLlmCall:
    """Tests for TreeOfThought._llm_call."""

    @patch(_LLM_CLS)
    def test_returns_content(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import TreeOfThought

        mock_resp = Mock()
        mock_resp.content = "LLM response text"
        mock_llm = Mock()
        mock_llm.invoke = Mock(return_value=mock_resp)
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought()
        result = tot._llm_call("system prompt", "user input", 0.5)
        assert result == "LLM response text"

    @patch(_LLM_CLS)
    def test_raises_on_failure(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import TreeOfThought

        mock_llm = Mock()
        mock_llm.invoke = Mock(side_effect=ConnectionError("network down"))
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought()
        with pytest.raises(ConnectionError):
            tot._llm_call("sys", "user", 0.7)


# ===================================================================
# 4. _generate_branch
# ===================================================================


class TestGenerateBranch:
    """Tests for TreeOfThought._generate_branch."""

    @patch(_LLM_CLS)
    def test_generates_successful_branch(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import TreeOfThought

        mock_resp = Mock(content="Branch content here")
        mock_llm = Mock()
        mock_llm.invoke = Mock(return_value=mock_resp)
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought()
        perspective = {
            "id": 1,
            "label": "Analytical",
            "directive": "Think analytically.",
            "temperature": 0.5,
        }
        branch = tot._generate_branch("What is AI?", perspective, "Base system prompt")

        assert branch.branch_id == 1
        assert branch.label == "Analytical"
        assert branch.content == "Branch content here"
        assert branch.error is None
        assert branch.elapsed_sec >= 0

    @patch(_LLM_CLS)
    def test_returns_error_branch_on_failure(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import TreeOfThought

        mock_llm = Mock()
        mock_llm.invoke = Mock(side_effect=RuntimeError("API down"))
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought()
        perspective = {
            "id": 2,
            "label": "Creative",
            "directive": "Be creative.",
            "temperature": 0.85,
        }
        branch = tot._generate_branch("topic", perspective, "sys")

        assert branch.branch_id == 2
        assert branch.error is not None
        assert "API down" in branch.error
        assert branch.content == ""


# ===================================================================
# 5. _evaluate_branches
# ===================================================================


class TestEvaluateBranches:
    """Tests for TreeOfThought._evaluate_branches (critic scoring)."""

    @patch(_LLM_CLS)
    def test_assigns_scores_from_critic(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import ThoughtBranch, TreeOfThought

        critic_json = json.dumps(
            {
                "evaluations": [
                    {"branch_id": 1, "score": 8.5, "critique": "Good analysis"},
                    {"branch_id": 2, "score": 9.2, "critique": "Great creativity"},
                ],
                "winner_id": 2,
                "reason": "Creative approach was better",
            }
        )
        mock_resp = Mock(content=critic_json)
        mock_llm = Mock()
        mock_llm.invoke = Mock(return_value=mock_resp)
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought(n_branches=2)
        branches = [
            ThoughtBranch(branch_id=1, label="Analytical", content="Branch 1 text"),
            ThoughtBranch(branch_id=2, label="Creative", content="Branch 2 text"),
        ]
        scored = tot._evaluate_branches("question", branches)
        assert scored[0].score == 8.5
        assert scored[1].score == 9.2
        assert scored[0].critique == "Good analysis"

    @patch(_LLM_CLS)
    def test_fallback_to_length_heuristic_on_failure(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import ThoughtBranch, TreeOfThought

        mock_llm = Mock()
        mock_llm.invoke = Mock(side_effect=Exception("Critic failed"))
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought(n_branches=2)
        branches = [
            ThoughtBranch(branch_id=1, label="A", content="short"),
            ThoughtBranch(branch_id=2, label="B", content="a" * 1000),
        ]
        scored = tot._evaluate_branches("q", branches)
        # Fallback: len(content) / 200.0
        assert scored[1].score > scored[0].score
        assert "长度评分" in scored[0].critique or "自动" in scored[0].critique

    def test_returns_unscored_when_all_branches_failed(self):
        from app.core.agent.tree_of_thought import ThoughtBranch, TreeOfThought

        tot = TreeOfThought(n_branches=2)
        branches = [
            ThoughtBranch(branch_id=1, label="A", content="", error="fail"),
            ThoughtBranch(branch_id=2, label="B", content="", error="fail"),
        ]
        result = tot._evaluate_branches("q", branches)
        # All errored, so scores remain 0
        assert all(b.score == 0.0 for b in result)


# ===================================================================
# 6. _pick_winner
# ===================================================================


class TestPickWinner:
    """Tests for TreeOfThought._pick_winner."""

    def test_picks_highest_score(self):
        from app.core.agent.tree_of_thought import ThoughtBranch, TreeOfThought

        tot = TreeOfThought()
        branches = [
            ThoughtBranch(branch_id=1, label="A", content="text1", score=7.0),
            ThoughtBranch(branch_id=2, label="B", content="text2", score=9.5),
            ThoughtBranch(branch_id=3, label="C", content="text3", score=8.0),
        ]
        winner = tot._pick_winner(branches)
        assert winner.branch_id == 2
        assert winner.score == 9.5

    def test_skips_errored_branches(self):
        from app.core.agent.tree_of_thought import ThoughtBranch, TreeOfThought

        tot = TreeOfThought()
        branches = [
            ThoughtBranch(branch_id=1, label="A", content="", error="boom", score=10.0),
            ThoughtBranch(branch_id=2, label="B", content="valid", score=5.0),
        ]
        winner = tot._pick_winner(branches)
        assert winner.branch_id == 2

    def test_raises_when_all_failed(self):
        from app.core.agent.tree_of_thought import ThoughtBranch, TreeOfThought

        tot = TreeOfThought()
        branches = [
            ThoughtBranch(branch_id=1, label="A", content="", error="fail"),
        ]
        with pytest.raises(RuntimeError, match="所有分支均失败"):
            tot._pick_winner(branches)


# ===================================================================
# 7. run() end-to-end (mocked)
# ===================================================================


class TestTreeOfThoughtRun:
    """Tests for the full TreeOfThought.run() pipeline."""

    @patch(_LLM_CLS)
    def test_run_returns_tot_result(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import ToTResult, TreeOfThought

        call_count = [0]

        def fake_invoke(msgs):
            call_count[0] += 1
            if call_count[0] <= 3:
                # Branch generation calls
                return Mock(content=f"Branch {call_count[0]} content " * 20)
            else:
                # Critic call
                return Mock(
                    content=json.dumps(
                        {
                            "evaluations": [
                                {"branch_id": 1, "score": 7.0, "critique": "ok"},
                                {"branch_id": 2, "score": 9.0, "critique": "great"},
                                {"branch_id": 3, "score": 8.0, "critique": "good"},
                            ],
                            "winner_id": 2,
                            "reason": "Best overall",
                        }
                    )
                )

        mock_llm = Mock()
        mock_llm.invoke = fake_invoke
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought(n_branches=3, max_workers=1)
        result = tot.run("test question", task_type="RESEARCH")

        assert isinstance(result, ToTResult)
        assert result.winner.branch_id == 2
        assert len(result.all_branches) == 3
        assert result.total_elapsed_sec >= 0


# ===================================================================
# 8. stream() events
# ===================================================================


class TestTreeOfThoughtStream:
    """Tests for TreeOfThought.stream() event generation."""

    @patch(_LLM_CLS)
    def test_stream_yields_expected_stages(self, mock_llm_cls):
        from app.core.agent.tree_of_thought import TreeOfThought

        call_count = [0]

        def fake_invoke(msgs):
            call_count[0] += 1
            if call_count[0] <= 3:
                return Mock(content=f"content {call_count[0]}" * 10)
            return Mock(
                content=json.dumps(
                    {
                        "evaluations": [
                            {"branch_id": i, "score": 7.0 + i, "critique": "ok"}
                            for i in range(1, 4)
                        ],
                        "winner_id": 3,
                        "reason": "best",
                    }
                )
            )

        mock_llm = Mock()
        mock_llm.invoke = fake_invoke
        mock_llm_cls.return_value = mock_llm

        tot = TreeOfThought(n_branches=3, max_workers=1)
        events = list(tot.stream("test", task_type="RESEARCH"))

        stages = [e["stage"] for e in events]
        assert "start" in stages
        assert "expand" in stages
        assert "evaluate" in stages
        assert "select" in stages


# ===================================================================
# 9. Helper functions
# ===================================================================


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_get_base_system_research(self):
        from app.core.agent.tree_of_thought import _get_base_system

        sys = _get_base_system("RESEARCH")
        assert "研究" in sys or "research" in sys.lower()

    def test_get_base_system_file_gen(self):
        from app.core.agent.tree_of_thought import _get_base_system

        sys = _get_base_system("FILE_GEN")
        assert "文档" in sys or "document" in sys.lower()

    def test_get_base_system_unknown_falls_back(self):
        from app.core.agent.tree_of_thought import _BASE_SYSTEMS, _get_base_system

        sys = _get_base_system("UNKNOWN_TYPE")
        assert sys == _BASE_SYSTEMS["RESEARCH"]

    def test_create_tot_research(self):
        from app.core.agent.tree_of_thought import create_tot

        tot = create_tot(task_type="RESEARCH", n_branches=3)
        assert tot.n_branches == 3

    def test_create_tot_file_gen_limits_branches(self):
        from app.core.agent.tree_of_thought import create_tot

        tot = create_tot(task_type="FILE_GEN", n_branches=3)
        assert tot.n_branches <= 2

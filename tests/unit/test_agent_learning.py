"""Tests for agent learning modules: rating_store and response_evaluator."""

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

_RS_MOD = "app.core.learning.rating_store"
_RE_MOD = "app.core.learning.response_evaluator"


@pytest.fixture
def db_dir():
    """Create a temporary directory for test databases, cleaned up after use."""
    d = tempfile.mkdtemp(prefix="koto_test_rating_")
    yield Path(d)
    # Best-effort cleanup; Windows may hold locks on .db files
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


# ===================================================================
# 1. RatingStore
# ===================================================================


class TestRatingStoreInit:
    """Tests for RatingStore initialization and schema."""

    def test_creates_db_file_and_schema(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        db_path = db_dir / "test_ratings.db"
        rs = RatingStore(db_path=db_path)
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        assert "user_ratings" in tables
        assert "model_evals" in tables

    def test_creates_parent_directories(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        db_path = db_dir / "deep" / "nested" / "ratings.db"
        rs = RatingStore(db_path=db_path)
        assert db_path.exists()


class TestMakeMsgId:
    """Tests for RatingStore.make_msg_id."""

    def test_returns_hex_string(self):
        from app.core.learning.rating_store import RatingStore

        msg_id = RatingStore.make_msg_id("session1", "hello world")
        assert isinstance(msg_id, str)
        assert len(msg_id) == 32  # MD5 hex digest length

    def test_deterministic(self):
        from app.core.learning.rating_store import RatingStore

        id1 = RatingStore.make_msg_id("s", "input")
        id2 = RatingStore.make_msg_id("s", "input")
        assert id1 == id2

    def test_different_sessions_different_ids(self):
        from app.core.learning.rating_store import RatingStore

        id1 = RatingStore.make_msg_id("session_a", "hello")
        id2 = RatingStore.make_msg_id("session_b", "hello")
        assert id1 != id2

    def test_truncates_long_input(self):
        from app.core.learning.rating_store import RatingStore

        long_input = "a" * 500
        id1 = RatingStore.make_msg_id("s", long_input)
        # Should use only first 120 chars
        id2 = RatingStore.make_msg_id("s", long_input[:120] + "DIFFERENT")
        assert id1 == id2


class TestSaveUserRating:
    """Tests for RatingStore.save_user_rating."""

    def test_saves_and_retrieves_rating(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        ok = rs.save_user_rating("msg1", stars=4, comment="Nice!", session_name="s1")
        assert ok is True

        row = rs.user_rating_for("msg1")
        assert row is not None
        assert row["stars"] == 4
        assert row["comment"] == "Nice!"

    def test_clamps_stars_to_valid_range(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")

        rs.save_user_rating("low", stars=0)
        assert rs.user_rating_for("low")["stars"] == 1

        rs.save_user_rating("high", stars=10)
        assert rs.user_rating_for("high")["stars"] == 5

    def test_upserts_on_conflict(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")

        rs.save_user_rating("msg1", stars=3, comment="ok")
        rs.save_user_rating("msg1", stars=5, comment="great")

        row = rs.user_rating_for("msg1")
        assert row["stars"] == 5
        assert row["comment"] == "great"


class TestSaveModelEval:
    """Tests for RatingStore.save_model_eval."""

    def test_saves_and_retrieves_eval(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        scores = {
            "accuracy": 0.9,
            "helpfulness": 0.8,
            "personalization": 0.7,
            "task_completion": 0.6,
        }
        ok = rs.save_model_eval("msg1", scores, session_name="s1", reasoning="Good")
        assert ok is True

        row = rs.model_eval_for("msg1")
        assert row is not None
        assert row["accuracy"] == 0.9
        # overall = 0.9*0.35 + 0.8*0.30 + 0.7*0.20 + 0.6*0.15 = 0.315+0.24+0.14+0.09 = 0.785
        assert abs(row["overall"] - 0.785) < 0.01

    def test_handles_missing_dimensions(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        rs.save_model_eval("msg1", {})
        row = rs.model_eval_for("msg1")
        assert row["overall"] == 0.0


class TestCombinedScore:
    """Tests for RatingStore.combined_score."""

    def test_both_tracks(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        rs.save_user_rating("msg1", stars=5)  # quality = 1.0
        rs.save_model_eval(
            "msg1",
            {
                "accuracy": 1.0,
                "helpfulness": 1.0,
                "personalization": 1.0,
                "task_completion": 1.0,
            },
        )
        score = rs.combined_score("msg1")
        # 1.0*0.55 + 1.0*0.45 = 1.0
        assert score is not None
        assert abs(score - 1.0) < 0.01

    def test_user_only(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        rs.save_user_rating("msg1", stars=3)  # quality = 0.55
        score = rs.combined_score("msg1")
        assert score is not None
        assert abs(score - 0.55) < 0.01

    def test_model_only(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        rs.save_model_eval(
            "msg1",
            {
                "accuracy": 0.8,
                "helpfulness": 0.8,
                "personalization": 0.8,
                "task_completion": 0.8,
            },
        )
        score = rs.combined_score("msg1")
        assert score is not None
        assert abs(score - 0.8) < 0.01

    def test_returns_none_when_no_data(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        assert rs.combined_score("nonexistent") is None


class TestGetStats:
    """Tests for RatingStore.get_stats."""

    def test_stats_with_data(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        rs.save_user_rating("m1", stars=5)
        rs.save_user_rating("m2", stars=3)
        rs.save_model_eval(
            "m1",
            {
                "accuracy": 0.9,
                "helpfulness": 0.9,
                "personalization": 0.9,
                "task_completion": 0.9,
            },
        )
        stats = rs.get_stats()
        assert stats["user_ratings"]["total"] == 2
        assert stats["model_evals"]["total"] == 1

    def test_stats_empty_db(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        stats = rs.get_stats()
        assert stats["user_ratings"]["total"] == 0
        assert stats["model_evals"]["total"] == 0
        assert stats["high_quality_paired"] == 0


class TestGetHighQualitySamples:
    """Tests for RatingStore.get_high_quality_samples."""

    def test_returns_high_quality_paired(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        rs.save_user_rating("m1", stars=5, user_input="hi", ai_response="hello")
        rs.save_model_eval(
            "m1",
            {
                "accuracy": 1.0,
                "helpfulness": 1.0,
                "personalization": 1.0,
                "task_completion": 1.0,
            },
        )
        samples = rs.get_high_quality_samples(min_combined=0.75)
        assert len(samples) == 1
        assert samples[0]["combined_score"] >= 0.75

    def test_excludes_low_quality(self, db_dir):
        from app.core.learning.rating_store import RatingStore

        rs = RatingStore(db_path=db_dir / "r.db")
        rs.save_user_rating("m1", stars=1, user_input="bad", ai_response="bad response")
        rs.save_model_eval(
            "m1",
            {
                "accuracy": 0.1,
                "helpfulness": 0.1,
                "personalization": 0.1,
                "task_completion": 0.1,
            },
        )
        samples = rs.get_high_quality_samples(min_combined=0.75)
        assert len(samples) == 0


# ===================================================================
# 2. ResponseEvaluator
# ===================================================================


class TestResponseEvaluatorShouldEvaluate:
    """Tests for ResponseEvaluator.should_evaluate."""

    def test_returns_true_for_normal_chat(self):
        from app.core.learning.response_evaluator import ResponseEvaluator

        assert (
            ResponseEvaluator.should_evaluate(
                "Tell me about AI",
                "AI stands for artificial intelligence...",
                "CHAT",
            )
            is True
        )

    def test_returns_false_for_short_response(self):
        from app.core.learning.response_evaluator import ResponseEvaluator

        assert ResponseEvaluator.should_evaluate("hi", "ok", "CHAT") is False

    def test_returns_false_for_system_task(self):
        from app.core.learning.response_evaluator import ResponseEvaluator

        assert (
            ResponseEvaluator.should_evaluate(
                "internal check",
                "System is running fine with all services up",
                "SYSTEM",
            )
            is False
        )

    def test_returns_false_for_very_short_combined(self):
        from app.core.learning.response_evaluator import ResponseEvaluator

        assert ResponseEvaluator.should_evaluate("h", "short answer", "CHAT") is False


class TestResponseEvaluatorDoEval:
    """Tests for ResponseEvaluator._do_eval."""

    @patch(f"{_RS_MOD}.get_rating_store")
    def test_parses_valid_json_response(self, mock_get_rs):
        from app.core.learning.response_evaluator import ResponseEvaluator

        mock_rs = Mock()
        mock_get_rs.return_value = mock_rs

        llm_response = json.dumps(
            {
                "accuracy": 0.85,
                "helpfulness": 0.9,
                "personalization": 0.7,
                "task_completion": 0.8,
                "reasoning": "Well-structured response",
            }
        )
        llm_fn = Mock(return_value=llm_response)

        scores = ResponseEvaluator._do_eval(
            "msg1",
            "What is AI?",
            "AI is...",
            "CHAT",
            "session1",
            llm_fn,
        )
        assert scores is not None
        assert scores["accuracy"] == 0.85
        assert scores["helpfulness"] == 0.9
        mock_rs.save_model_eval.assert_called_once()

    @patch(f"{_RS_MOD}.get_rating_store")
    def test_handles_json_with_code_fences(self, mock_get_rs):
        from app.core.learning.response_evaluator import ResponseEvaluator

        mock_get_rs.return_value = Mock()

        raw = '```json\n{"accuracy": 0.7, "helpfulness": 0.6, "personalization": 0.5, "task_completion": 0.4, "reasoning": "ok"}\n```'
        llm_fn = Mock(return_value=raw)

        scores = ResponseEvaluator._do_eval(
            "msg2",
            "input",
            "response",
            "CHAT",
            "s",
            llm_fn,
        )
        assert scores is not None
        assert scores["accuracy"] == 0.7

    def test_returns_none_on_llm_failure(self):
        from app.core.learning.response_evaluator import ResponseEvaluator

        llm_fn = Mock(side_effect=RuntimeError("API down"))

        scores = ResponseEvaluator._do_eval(
            "msg3",
            "input",
            "response",
            "CHAT",
            "s",
            llm_fn,
        )
        assert scores is None

    def test_returns_none_on_invalid_json(self):
        from app.core.learning.response_evaluator import ResponseEvaluator

        llm_fn = Mock(return_value="This is not JSON at all, no braces")

        scores = ResponseEvaluator._do_eval(
            "msg4",
            "input",
            "response",
            "CHAT",
            "s",
            llm_fn,
        )
        assert scores is None

    @patch(f"{_RS_MOD}.get_rating_store")
    def test_clamps_scores_to_01(self, mock_get_rs):
        from app.core.learning.response_evaluator import ResponseEvaluator

        mock_get_rs.return_value = Mock()

        raw = json.dumps(
            {
                "accuracy": 1.5,
                "helpfulness": -0.3,
                "personalization": 0.5,
                "task_completion": 0.5,
                "reasoning": "test",
            }
        )
        llm_fn = Mock(return_value=raw)

        scores = ResponseEvaluator._do_eval(
            "msg5",
            "input",
            "response",
            "CHAT",
            "s",
            llm_fn,
        )
        assert scores["accuracy"] == 1.0
        assert scores["helpfulness"] == 0.0


class TestResponseEvaluatorAsync:
    """Tests for ResponseEvaluator.evaluate_async."""

    def test_skips_evaluation_when_not_needed(self):
        from app.core.learning.response_evaluator import ResponseEvaluator

        with patch.object(ResponseEvaluator, "_do_eval") as mock_eval:
            ResponseEvaluator.evaluate_async(
                "msg1",
                "hi",
                "ok",
                "CHAT",
                "s",
                Mock(),
            )
            # should_evaluate returns False for short responses, _do_eval not called
            mock_eval.assert_not_called()

    @patch(f"{_RE_MOD}.threading.Thread")
    def test_starts_daemon_thread(self, mock_thread_cls):
        from app.core.learning.response_evaluator import ResponseEvaluator

        mock_thread = Mock()
        mock_thread_cls.return_value = mock_thread

        ResponseEvaluator.evaluate_async(
            "msg1",
            "Tell me about quantum computing",
            "Quantum computing uses quantum bits...",
            "CHAT",
            "session1",
            Mock(),
        )
        mock_thread_cls.assert_called_once()
        assert mock_thread_cls.call_args[1]["daemon"] is True
        mock_thread.start.assert_called_once()

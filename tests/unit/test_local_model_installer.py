"""Unit tests for the pure-logic helpers in src/local_model_installer.py.

tkinter is mocked before import so tests run headless.
"""

from __future__ import annotations

import sys
import unittest.mock

# ---------------------------------------------------------------------------
# Mock tkinter before the module is imported so the GUI class definition
# inside run_gui() doesn't fail in a headless environment.
# ---------------------------------------------------------------------------
sys.modules.setdefault("tkinter", unittest.mock.MagicMock())
sys.modules.setdefault("tkinter.ttk", unittest.mock.MagicMock())
sys.modules.setdefault("tkinter.scrolledtext", unittest.mock.MagicMock())

sys.path.insert(0, "src")

from local_model_installer import (  # noqa: E402
    MODEL_CATALOG,
    _strip_ansi,
    recommend_models,
)

# ---------------------------------------------------------------------------
# _strip_ansi
# ---------------------------------------------------------------------------


class TestStripAnsi:
    def test_removes_single_color_code(self):
        assert _strip_ansi("\x1b[31mred text\x1b[0m") == "red text"

    def test_plain_text_unchanged(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_empty_string_unchanged(self):
        assert _strip_ansi("") == ""

    def test_removes_multiple_codes(self):
        text = "\x1b[1m\x1b[32mbold green\x1b[0m\x1b[39m"
        assert _strip_ansi(text) == "bold green"

    def test_removes_cursor_movement_codes(self):
        # ESC[A (cursor up), ESC[2K (erase line)
        assert _strip_ansi("\x1b[A\x1b[2Kline") == "line"

    def test_string_with_only_ansi_codes_becomes_empty(self):
        assert _strip_ansi("\x1b[0m\x1b[1m") == ""


# ---------------------------------------------------------------------------
# recommend_models
# ---------------------------------------------------------------------------


class TestRecommendModels:
    def _make_info(self, ram_gb: float, gpu_vram_gb: float = 0.0) -> dict:
        return {"ram_gb": ram_gb, "gpu_vram_gb": gpu_vram_gb}

    # ── basic structure ──────────────────────────────────────────────────────

    def test_returns_non_empty_list(self):
        result = recommend_models(self._make_info(8))
        assert isinstance(result, list)
        assert len(result) > 0

    def test_each_entry_has_recommended_key(self):
        result = recommend_models(self._make_info(8))
        for entry in result:
            assert "recommended" in entry

    def test_exactly_one_recommended(self):
        result = recommend_models(self._make_info(8))
        assert sum(1 for m in result if m["recommended"]) == 1

    def test_exactly_one_recommended_high_resources(self):
        result = recommend_models(self._make_info(32, gpu_vram_gb=24))
        assert sum(1 for m in result if m["recommended"]) == 1

    # ── low-resource scenario ────────────────────────────────────────────────

    def test_low_ram_no_vram_recommends_smallest_model(self):
        """0.5 GB RAM, no VRAM → only MODEL_CATALOG[0] is feasible."""
        result = recommend_models(self._make_info(0.5, gpu_vram_gb=0.0))
        assert len(result) == 1
        recommended = next(m for m in result if m["recommended"])
        assert recommended["tag"] == MODEL_CATALOG[0]["tag"]

    # ── high-resource scenario ───────────────────────────────────────────────

    def test_high_ram_high_vram_recommends_large_model(self):
        """32 GB RAM + 24 GB VRAM → all models feasible, flagship recommended."""
        result = recommend_models(self._make_info(32, gpu_vram_gb=24))
        recommended = next(m for m in result if m["recommended"])
        # With 24 GB VRAM the sweet-spot logic selects the last catalog entry
        # whose vram requirement is met with ≥ 15 % headroom.
        assert recommended["tier"] == "flagship"

    # ── sweet-spot logic ─────────────────────────────────────────────────────

    def test_sweet_spot_gpu_headroom(self):
        """8 GB VRAM should recommend a model whose vram ≤ 8 / 1.15 ≈ 6.96."""
        result = recommend_models(self._make_info(8, gpu_vram_gb=8.0))
        recommended = next(m for m in result if m["recommended"])
        # GPU sweet-spot: vram >= model.vram * 1.15
        assert 8.0 >= recommended["vram"] * 1.15

    def test_sweet_spot_cpu_headroom(self):
        """16 GB RAM, no VRAM should recommend a model whose ram ≤ 16 / 1.30 ≈ 12.3."""
        result = recommend_models(self._make_info(16, gpu_vram_gb=0.0))
        recommended = next(m for m in result if m["recommended"])
        # CPU sweet-spot: ram >= model.ram * 1.30
        assert 16.0 >= recommended["ram"] * 1.30

    # ── catalog isolation ────────────────────────────────────────────────────

    def test_result_is_copy_not_original(self):
        """Mutating returned dicts must not affect MODEL_CATALOG."""
        original_tags = [m["tag"] for m in MODEL_CATALOG]
        result = recommend_models(self._make_info(8))
        for entry in result:
            entry["recommended"] = "MUTATED"
        # MODEL_CATALOG entries should not have a "recommended" key
        for m in MODEL_CATALOG:
            assert "recommended" not in m
        # Tags must be unchanged
        assert [m["tag"] for m in MODEL_CATALOG] == original_tags

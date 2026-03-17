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
    recommend_models,
)

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

    def test_each_entry_has_required_keys(self):
        result = recommend_models(self._make_info(8))
        for entry in result:
            assert "tag" in entry
            assert "ram" in entry
            assert "vram" in entry

    def test_filters_by_effective_resources(self):
        result = recommend_models(self._make_info(8))
        for m in result:
            assert 8 >= m["ram"] or 0 >= m["vram"]

    def test_high_resources_returns_all_models(self):
        result = recommend_models(self._make_info(32, gpu_vram_gb=24))
        assert len(result) == len(MODEL_CATALOG)

    # ── low-resource scenario ────────────────────────────────────────────────

    def test_low_ram_no_vram_recommends_smallest_model(self):
        """0.5 GB RAM, no VRAM → nothing matches, fallback to [MODEL_CATALOG[0]]."""
        result = recommend_models(self._make_info(0.5, gpu_vram_gb=0.0))
        assert len(result) == 1
        assert result[0]["tag"] == MODEL_CATALOG[0]["tag"]

    # ── high-resource scenario ───────────────────────────────────────────────

    def test_high_ram_high_vram_recommends_large_model(self):
        """32 GB RAM + 24 GB VRAM → all models feasible, flagship tier present."""
        result = recommend_models(self._make_info(32, gpu_vram_gb=24))
        assert len(result) == len(MODEL_CATALOG)
        tiers = {m["tier"] for m in result}
        assert "flagship" in tiers

    # ── filtering logic ──────────────────────────────────────────────────────

    def test_gpu_vram_scaling_factor(self):
        """8 GB VRAM → eff = max(8, 12) = 12, includes models with ram ≤ 12 or vram ≤ 8."""
        result = recommend_models(self._make_info(8, gpu_vram_gb=8.0))
        for m in result:
            assert 12 >= m["ram"] or 8 >= m["vram"]

    def test_cpu_only_filtering(self):
        """16 GB RAM, no VRAM → includes models with ram ≤ 16."""
        result = recommend_models(self._make_info(16, gpu_vram_gb=0.0))
        for m in result:
            assert 16 >= m["ram"] or 0 >= m["vram"]

    # ── catalog isolation ────────────────────────────────────────────────────

    def test_result_entries_come_from_catalog(self):
        """Returned entries should have tags found in MODEL_CATALOG."""
        catalog_tags = {m["tag"] for m in MODEL_CATALOG}
        result = recommend_models(self._make_info(8))
        for entry in result:
            assert entry["tag"] in catalog_tags

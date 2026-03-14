"""Thread-safety tests for singletons and shared state.

Validates that concurrent access to module-level singletons and
shared managers does not produce duplicate instances, crashes, or
inconsistent state.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

NUM_THREADS = 25


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_concurrently(target, num_threads=NUM_THREADS):
    """Launch *num_threads* threads behind a Barrier so they all start together.

    Returns a list of results (one per thread, positional).
    """
    barrier = threading.Barrier(num_threads)
    results = [None] * num_threads
    errors = [None] * num_threads

    def _worker(idx):
        try:
            barrier.wait(timeout=5)
            results[idx] = target()
        except Exception as exc:
            errors[idx] = exc

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    for i, err in enumerate(errors):
        if err is not None:
            raise AssertionError(f"Thread {i} raised: {err}") from err

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 1. get_fallback_executor() singleton thread safety
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestFallbackExecutorSingleton:

    def setup_method(self):
        import app.core.llm.model_fallback as _mod
        self._mod = _mod
        self._original = _mod._executor
        _mod._executor = None

    def teardown_method(self):
        self._mod._executor = self._original

    def test_concurrent_calls_return_same_instance(self):
        results = _run_concurrently(self._mod.get_fallback_executor)
        first = results[0]
        assert first is not None
        for idx, obj in enumerate(results[1:], start=1):
            assert obj is first, f"Thread {idx} got a different instance"

    def test_sequential_calls_return_same_instance(self):
        a = self._mod.get_fallback_executor()
        b = self._mod.get_fallback_executor()
        assert a is b


# ═══════════════════════════════════════════════════════════════════════════════
# 2. get_checkpointer() singleton thread safety
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCheckpointerSingleton:

    def setup_method(self):
        import app.core.agent.checkpoint_manager as _mod
        self._mod = _mod
        self._orig_instance = _mod._checkpointer_instance
        self._orig_type = _mod._checkpointer_type
        _mod._checkpointer_instance = None
        _mod._checkpointer_type = "none"

    def teardown_method(self):
        self._mod._checkpointer_instance = self._orig_instance
        self._mod._checkpointer_type = self._orig_type

    @patch("app.core.agent.checkpoint_manager.SqliteSaver", create=True)
    @patch("app.core.agent.checkpoint_manager._get_sqlite_conn")
    def test_concurrent_calls_return_same_instance(self, mock_conn, mock_saver_cls):
        """All threads get the identical checkpointer object."""
        sentinel = MagicMock(name="SqliteSaver-singleton")
        mock_saver_cls.return_value = sentinel

        # Patch the import inside get_checkpointer
        fake_sqlite_module = MagicMock()
        fake_sqlite_module.SqliteSaver = mock_saver_cls

        with patch.dict("sys.modules", {"langgraph.checkpoint.sqlite": fake_sqlite_module}):
            with patch("pathlib.Path.mkdir"):
                results = _run_concurrently(self._mod.get_checkpointer)

        first = results[0]
        assert first is not None
        for idx, obj in enumerate(results[1:], start=1):
            assert obj is first, f"Thread {idx} got a different instance"

    def test_memory_fallback_concurrent(self):
        """When SqliteSaver import fails, concurrent calls still return one MemorySaver."""
        fake_memory_module = MagicMock()
        sentinel = MagicMock(name="MemorySaver-singleton")
        fake_memory_module.MemorySaver.return_value = sentinel

        # Remove sqlite module so import fails, provide memory module
        modules_patch = {
            "langgraph.checkpoint.sqlite": None,
            "langgraph.checkpoint.memory": fake_memory_module,
        }
        with patch.dict("sys.modules", modules_patch):
            results = _run_concurrently(self._mod.get_checkpointer)

        first = results[0]
        assert first is not None
        for idx, obj in enumerate(results[1:], start=1):
            assert obj is first, f"Thread {idx} got a different instance"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. StreamInterruptManager thread safety
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestStreamInterruptManagerConcurrency:

    def setup_method(self):
        from web.app import StreamInterruptManager
        self.manager = StreamInterruptManager()

    # -- concurrent set + read ------------------------------------------------

    def test_concurrent_set_and_read(self):
        """Mixed set_interrupt / is_interrupted calls must not crash."""
        session = "sess-rw"

        def _writer():
            self.manager.set_interrupt(session)

        def _reader():
            self.manager.is_interrupted(session)

        barrier = threading.Barrier(NUM_THREADS)
        errors = []

        def _worker(fn):
            try:
                barrier.wait(timeout=5)
                fn()
            except Exception as exc:
                errors.append(exc)

        threads = []
        for i in range(NUM_THREADS):
            fn = _writer if i % 2 == 0 else _reader
            threads.append(threading.Thread(target=_worker, args=(fn,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Threads raised: {errors}"

    # -- concurrent cleanup ---------------------------------------------------

    def test_concurrent_cleanup_no_keyerror(self):
        """Multiple threads cleaning up the same session must not KeyError."""
        session = "sess-cleanup"
        self.manager.set_interrupt(session)

        errors = []
        barrier = threading.Barrier(NUM_THREADS)

        def _worker():
            try:
                barrier.wait(timeout=5)
                self.manager.cleanup(session)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(NUM_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Threads raised: {errors}"

    # -- state consistency after concurrent ops -------------------------------

    def test_state_consistent_after_concurrent_ops(self):
        """After set + reset across threads, state must be consistent."""
        session = "sess-consistent"
        barrier = threading.Barrier(NUM_THREADS)
        errors = []

        def _worker(idx):
            try:
                barrier.wait(timeout=5)
                if idx % 3 == 0:
                    self.manager.set_interrupt(session)
                elif idx % 3 == 1:
                    self.manager.reset(session)
                else:
                    self.manager.is_interrupted(session)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(NUM_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Threads raised: {errors}"
        # After all ops, calling any method should not crash
        result = self.manager.is_interrupted(session)
        assert isinstance(result, bool)

    def test_concurrent_different_sessions(self):
        """Operations on different sessions in parallel must not interfere."""
        barrier = threading.Barrier(NUM_THREADS)
        errors = []

        def _worker(idx):
            try:
                barrier.wait(timeout=5)
                s = f"sess-{idx}"
                self.manager.set_interrupt(s)
                assert self.manager.is_interrupted(s)
                self.manager.cleanup(s)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(NUM_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Threads raised: {errors}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. _user_settings_cache / _user_settings_lock
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestUserSettingsCacheConcurrency:

    def setup_method(self):
        import web.app as _mod
        self._mod = _mod
        self._orig_cache = _mod._user_settings_cache.copy()
        _mod._user_settings_cache.clear()

    def teardown_method(self):
        self._mod._user_settings_cache.clear()
        self._mod._user_settings_cache.update(self._orig_cache)

    def test_lock_exists_and_is_a_lock(self):
        assert hasattr(self._mod, "_user_settings_lock")
        assert isinstance(self._mod._user_settings_lock, type(threading.Lock()))

    def test_concurrent_load_returns_same_data(self):
        """All threads calling _load_user_settings get identical dict."""
        fake_data = {"theme": "dark", "lang": "en"}

        with patch("builtins.open", create=True), \
             patch("json.load", return_value=fake_data):
            results = _run_concurrently(self._mod._load_user_settings)

        first = results[0]
        assert first == fake_data
        for idx, obj in enumerate(results[1:], start=1):
            assert obj is first, f"Thread {idx} got a different dict object"

    def test_concurrent_cache_clear_and_read(self):
        """Clearing cache while others read must not crash."""
        self._mod._user_settings_cache["data"] = {"cached": True}

        barrier = threading.Barrier(NUM_THREADS)
        errors = []

        def _reader():
            try:
                barrier.wait(timeout=5)
                with self._mod._user_settings_lock:
                    _ = self._mod._user_settings_cache.get("data", {})
            except Exception as exc:
                errors.append(exc)

        def _clearer():
            try:
                barrier.wait(timeout=5)
                with self._mod._user_settings_lock:
                    self._mod._user_settings_cache.clear()
            except Exception as exc:
                errors.append(exc)

        threads = []
        for i in range(NUM_THREADS):
            fn = _clearer if i % 5 == 0 else _reader
            threads.append(threading.Thread(target=fn))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Threads raised: {errors}"

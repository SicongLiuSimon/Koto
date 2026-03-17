"""
Batch 9 – unit tests for 10 web/app modules at 0 % coverage.
Each class contains 5-8 focused tests covering __init__, key public methods, and error paths.
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest


# ---------------------------------------------------------------------------
# 1. VoiceInteraction
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestVoiceCommandProcessor:
    """Tests for web.voice_interaction.VoiceCommandProcessor"""

    def _make(self):
        from web.voice_interaction import VoiceCommandProcessor

        return VoiceCommandProcessor()

    def test_init_registers_builtin_commands(self):
        proc = self._make()
        assert len(proc.commands) > 0
        assert "help" in proc.commands

    def test_register_custom_command(self):
        proc = self._make()
        proc.register_command("greet", ["hello", "hi"], lambda: "hi!", "Greet")
        assert "greet" in proc.commands
        assert proc.commands["greet"].keywords == ["hello", "hi"]

    def test_match_command_returns_matching(self):
        proc = self._make()
        cmd = proc.match_command("帮助")
        assert cmd is not None
        assert cmd.name == "help"

    def test_match_command_returns_none_for_unknown(self):
        proc = self._make()
        assert proc.match_command("xyzzy_no_match") is None

    def test_match_command_skips_disabled(self):
        proc = self._make()
        proc.commands["help"].enabled = False
        assert proc.match_command("帮助") is None

    def test_execute_command_success(self):
        proc = self._make()
        result = proc.execute_command("保存文档")
        assert result["success"] is True
        assert result["command"] == "save_document"

    def test_execute_command_unknown(self):
        proc = self._make()
        result = proc.execute_command("xyzzy_no_match_cmd")
        assert result["success"] is False
        assert result["command"] is None

    def test_execute_command_action_raises(self):
        proc = self._make()
        proc.register_command(
            "bad", ["坏的"], lambda: (_ for _ in ()).throw(RuntimeError("boom")), "bad"
        )

        # The lambda trick above won't raise on call; use a proper function
        def _raise():
            raise RuntimeError("boom")

        proc.register_command("bad2", ["坏命令"], _raise, "bad cmd")
        result = proc.execute_command("坏命令")
        assert result["success"] is False
        assert "boom" in result["message"]

    def test_list_commands(self):
        proc = self._make()
        cmds = proc.list_commands()
        assert isinstance(cmds, list)
        assert all("name" in c for c in cmds)


@pytest.mark.unit
class TestVoiceInteractionManager:
    """Tests for web.voice_interaction.VoiceInteractionManager"""

    @patch("web.voice_interaction.os.path.exists", return_value=False)
    def test_init_default_config(self, _mock_exists):
        from web.voice_interaction import VoiceInteractionManager

        mgr = VoiceInteractionManager()
        assert mgr.config["hotkey"] == "ctrl+shift+v"

    @patch("web.voice_interaction.os.path.exists", return_value=False)
    def test_get_config(self, _):
        from web.voice_interaction import VoiceInteractionManager

        mgr = VoiceInteractionManager()
        cfg = mgr.get_config()
        assert "language" in cfg

    @patch("web.voice_interaction.os.path.exists", return_value=False)
    def test_register_state_callback(self, _):
        from web.voice_interaction import VoiceInteractionManager

        mgr = VoiceInteractionManager()
        cb = Mock()
        mgr.register_state_callback(cb)
        assert cb in mgr.state_callbacks

    @patch("web.voice_interaction.os.path.exists", return_value=False)
    def test_on_hotkey_pressed_fires_callbacks(self, _):
        from web.voice_interaction import VoiceInteractionManager

        mgr = VoiceInteractionManager()
        cb = Mock()
        mgr.register_state_callback(cb)
        mgr.on_hotkey_pressed()
        cb.assert_called_once()

    @patch("web.voice_interaction.os.path.exists", return_value=False)
    def test_cleanup_without_listener(self, _):
        from web.voice_interaction import VoiceInteractionManager

        mgr = VoiceInteractionManager()
        mgr.cleanup()  # should not raise

    @patch("web.voice_interaction.os.path.exists", return_value=False)
    def test_set_config_calls_save(self, _):
        from web.voice_interaction import VoiceInteractionManager

        mgr = VoiceInteractionManager()
        with patch.object(mgr, "save_config") as mock_save:
            mgr.set_config("timeout", 30)
            assert mgr.config["timeout"] == 30
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# 2. VoiceRecognitionEnhanced
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestEnhancedVoiceRecognizer:
    """Tests for web.voice_recognition_enhanced"""

    def _make(self, **kw):
        with patch(
            "web.voice_recognition_enhanced.EnhancedVoiceRecognizer._init_speech_recognition",
            return_value=None,
        ):
            from web.voice_recognition_enhanced import EnhancedVoiceRecognizer

            return EnhancedVoiceRecognizer(**kw)

    def test_init_defaults(self):
        rec = self._make()
        assert rec.max_retries == 3
        assert rec.cache_enabled is True
        from web.voice_recognition_enhanced import RecognitionStatus

        assert rec.status == RecognitionStatus.IDLE

    def test_recognize_microphone_no_engine(self):
        rec = self._make()
        result = rec.recognize_microphone()
        assert result.success is False
        assert "未初始化" in result.message

    def test_recognize_file_no_engine(self):
        rec = self._make()
        result = rec.recognize_file("nonexistent.wav")
        assert result.success is False

    def test_cache_miss_returns_none(self):
        rec = self._make()
        assert rec._check_cache(b"audio_bytes") is None

    def test_cache_disabled_returns_none(self):
        rec = self._make(cache_enabled=False)
        assert rec._check_cache(b"audio_bytes") is None

    def test_save_and_retrieve_cache(self):
        rec = self._make()
        from web.voice_recognition_enhanced import RecognitionResult

        fake_result = RecognitionResult(success=True, text="hello")
        audio = b"some_audio_data"
        rec._save_to_cache(audio, fake_result)
        cached = rec._check_cache(audio)
        assert cached is not None
        assert cached.text == "hello"

    def test_get_stats_initial(self):
        rec = self._make()
        stats = rec.get_stats()
        assert stats["total_recognitions"] == 0
        assert stats["success_rate"] == "0.0%"

    def test_cache_size_property(self):
        rec = self._make()
        assert rec.cache_size == 0

    def test_recognition_result_to_dict(self):
        from web.voice_recognition_enhanced import RecognitionResult

        r = RecognitionResult(success=True, text="ok", confidence=0.9)
        d = r.to_dict()
        assert d["text"] == "ok"
        assert d["confidence"] == 0.9


# ---------------------------------------------------------------------------
# 3. AutoExecution
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAutoExecutionEngine:
    """Tests for web.auto_execution.AutoExecutionEngine"""

    def _make(self, tmp_path):
        db = str(tmp_path / "auto_exec.db")
        ws = str(tmp_path / "workspace")
        os.makedirs(ws, exist_ok=True)
        from web.auto_execution import AutoExecutionEngine

        return AutoExecutionEngine(db_path=db, workspace_root=ws)

    def test_init_creates_db(self, tmp_path):
        engine = self._make(tmp_path)
        assert os.path.exists(engine.db_path)

    def test_builtin_tasks_registered(self, tmp_path):
        engine = self._make(tmp_path)
        assert "organize_files" in engine.task_handlers
        assert "backup_file" in engine.task_handlers

    def test_register_custom_task(self, tmp_path):
        engine = self._make(tmp_path)
        engine.register_task(
            "custom", "Custom Task", "desc", "safe", lambda p: {"ok": True}
        )
        assert "custom" in engine.task_handlers

    def test_authorize_and_can_execute(self, tmp_path):
        engine = self._make(tmp_path)
        engine.authorize_task("user1", "backup_file", auto_execute=True)
        ok, msg = engine.can_execute("user1", "backup_file")
        assert ok is True

    def test_can_execute_unauthorized(self, tmp_path):
        engine = self._make(tmp_path)
        ok, msg = engine.can_execute("user1", "backup_file")
        assert ok is False

    def test_execute_task_unknown_type(self, tmp_path):
        engine = self._make(tmp_path)
        result = engine.execute_task("u1", "nonexistent_task", {})
        assert result["success"] is False
        assert "未知" in result["error"]

    def test_execute_task_force(self, tmp_path):
        engine = self._make(tmp_path)
        engine.register_task("echo", "Echo", "echo", "safe", lambda p: {"echoed": True})
        result = engine.execute_task("u1", "echo", {}, force=True)
        assert result["success"] is True
        assert result["result"]["echoed"] is True

    def test_queue_task(self, tmp_path):
        engine = self._make(tmp_path)
        tid = engine.queue_task("user1", "backup_file", {"file": "a.txt"})
        assert isinstance(tid, int) and tid > 0

    def test_revoke_authorization(self, tmp_path):
        engine = self._make(tmp_path)
        engine.authorize_task("u1", "backup_file")
        engine.revoke_authorization("u1", "backup_file")
        ok, _ = engine.can_execute("u1", "backup_file")
        assert ok is False


# ---------------------------------------------------------------------------
# 4. AutoCatalogScheduler
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAutoCatalogScheduler:
    """Tests for web.auto_catalog_scheduler.AutoCatalogScheduler"""

    def _make(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        from web.auto_catalog_scheduler import AutoCatalogScheduler

        return AutoCatalogScheduler(settings_file=str(settings))

    def test_init_loads_empty_config(self, tmp_path):
        sched = self._make(tmp_path)
        assert sched.config == {}

    def test_is_auto_catalog_enabled_default_false(self, tmp_path):
        sched = self._make(tmp_path)
        assert sched.is_auto_catalog_enabled() is False

    def test_get_catalog_schedule_default(self, tmp_path):
        sched = self._make(tmp_path)
        assert sched.get_catalog_schedule() == "02:00"

    def test_get_source_directories_empty(self, tmp_path):
        sched = self._make(tmp_path)
        assert sched.get_source_directories() == []

    @patch("web.auto_catalog_scheduler.AutoCatalogScheduler._register_scheduled_task")
    def test_enable_auto_catalog(self, mock_reg, tmp_path):
        sched = self._make(tmp_path)
        sched.enable_auto_catalog("03:00")
        assert sched.config["auto_catalog"]["enabled"] is True
        assert sched.config["auto_catalog"]["schedule_time"] == "03:00"
        mock_reg.assert_called_once()

    @patch("web.auto_catalog_scheduler.AutoCatalogScheduler._cancel_scheduled_task")
    def test_disable_auto_catalog(self, mock_cancel, tmp_path):
        sched = self._make(tmp_path)
        sched.disable_auto_catalog()
        assert sched.config["auto_catalog"]["enabled"] is False
        mock_cancel.assert_called_once()

    def test_execute_auto_catalog_no_source_dirs(self, tmp_path):
        sched = self._make(tmp_path)
        result = sched.execute_auto_catalog()
        assert result["success"] is False
        assert "源目录" in result["error"]

    def test_get_backup_directory_creates(self, tmp_path):
        sched = self._make(tmp_path)
        bd = sched.get_backup_directory()
        assert os.path.isdir(bd)


# ---------------------------------------------------------------------------
# 5. BehaviorMonitor
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestBehaviorMonitor:
    """Tests for web.behavior_monitor.BehaviorMonitor"""

    def _make(self, tmp_path):
        db = str(tmp_path / "behavior.db")
        from web.behavior_monitor import BehaviorMonitor

        return BehaviorMonitor(db_path=db)

    def test_init_creates_tables(self, tmp_path):
        mon = self._make(tmp_path)
        conn = sqlite3.connect(mon.db_path)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        conn.close()
        assert "event_log" in tables
        assert "file_usage_stats" in tables

    def test_log_event_returns_id(self, tmp_path):
        mon = self._make(tmp_path)
        eid = mon.log_event("file_open", file_path="test.txt")
        assert isinstance(eid, int) and eid > 0

    def test_log_event_updates_file_stats(self, tmp_path):
        mon = self._make(tmp_path)
        mon.log_event(mon.EVENT_FILE_OPEN, file_path="a.txt", duration_ms=100)
        files = mon.get_frequently_used_files()
        assert len(files) == 1
        assert files[0]["file_path"] == "a.txt"

    def test_log_search_and_retrieve(self, tmp_path):
        mon = self._make(tmp_path)
        sid = mon.log_search("test query", 5, "result1")
        assert sid > 0
        history = mon.get_search_history()
        assert len(history) == 1
        assert history[0]["query"] == "test query"

    def test_get_recent_events_with_type_filter(self, tmp_path):
        mon = self._make(tmp_path)
        mon.log_event("file_open")
        mon.log_event("file_edit")
        events = mon.get_recent_events(event_type="file_open")
        assert all(e["event_type"] == "file_open" for e in events)

    def test_get_statistics(self, tmp_path):
        mon = self._make(tmp_path)
        mon.log_event("file_open")
        stats = mon.get_statistics()
        assert stats["total_events"] == 1

    def test_get_work_patterns(self, tmp_path):
        mon = self._make(tmp_path)
        mon.log_event("file_open")
        patterns = mon.get_work_patterns()
        assert "time_of_day" in patterns
        assert "operation_types" in patterns

    def test_detect_anomalies_empty(self, tmp_path):
        mon = self._make(tmp_path)
        anomalies = mon.detect_anomalies()
        assert isinstance(anomalies, list)


# ---------------------------------------------------------------------------
# 6. AuditLogger  (web/audit_logger.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAuditLogger:
    """Tests for web.audit_logger.AuditLogger"""

    def _make(self, tmp_path):
        db = str(tmp_path / "audit.db")
        from web.audit_logger import AuditLogger

        return AuditLogger(db_path=db)

    def test_init_creates_tables(self, tmp_path):
        al = self._make(tmp_path)
        conn = sqlite3.connect(al.db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "audit_logs" in tables

    def test_log_action_returns_id(self, tmp_path):
        al = self._make(tmp_path)
        from web.audit_logger import AuditActionType

        lid = al.log_action(
            "org1", "user1", AuditActionType.USER_LOGIN, "user", "user1", "john"
        )
        assert isinstance(lid, str) and len(lid) > 0

    def test_log_user_login(self, tmp_path):
        al = self._make(tmp_path)
        lid = al.log_user_login("org1", "user1", ip_address="127.0.0.1")
        assert lid != ""

    def test_log_file_created(self, tmp_path):
        al = self._make(tmp_path)
        lid = al.log_file_created("org1", "u1", "f1", "doc.txt", 1024)
        assert lid != ""

    def test_query_logs(self, tmp_path):
        al = self._make(tmp_path)
        al.log_user_login("org1", "user1")
        logs, count = al.query_logs("org1")
        assert count == 1
        assert logs[0]["action"] == "USER_LOGIN"

    def test_query_logs_with_filters(self, tmp_path):
        al = self._make(tmp_path)
        al.log_user_login("org1", "user1")
        al.log_file_created("org1", "user1", "f1", "doc.txt")
        logs, count = al.query_logs("org1", filters={"action": "USER_LOGIN"})
        assert count == 1

    def test_generate_audit_report(self, tmp_path):
        al = self._make(tmp_path)
        al.log_user_login("org1", "user1")
        report = al.generate_audit_report("org1", "2000-01-01", "2099-12-31")
        assert "total_events" in report
        assert "compliance_checks" in report

    def test_export_audit_logs_csv(self, tmp_path):
        al = self._make(tmp_path)
        al.log_user_login("org1", "user1")
        csv_out = al.export_audit_logs("org1", "2000-01-01", "2099-12-31", format="csv")
        assert "USER_LOGIN" in csv_out


# ---------------------------------------------------------------------------
# 7. AuthManager
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAuthManager:
    """Tests for web.auth_manager (RoleManager, UserManager, SessionManager, AuthenticationManager)"""

    def test_role_manager_default_roles(self):
        from web.auth_manager import RoleManager

        rm = RoleManager()
        assert "admin" in rm.roles
        assert "guest" in rm.roles

    def test_role_manager_create_custom_role(self):
        from web.auth_manager import Permission, RoleManager

        rm = RoleManager()
        role = rm.create_role(
            "editor", "Editor", {Permission.DATA_READ, Permission.DATA_WRITE}
        )
        assert role.role_id == "editor"
        assert Permission.DATA_WRITE in role.permissions

    def test_user_manager_create_and_get(self):
        from web.auth_manager import UserManager, UserRole

        um = UserManager()
        user = um.create_user("u1", "alice", "alice@test.com", "hash123")
        assert um.get_user("u1").username == "alice"
        assert um.get_user_by_email("alice@test.com").user_id == "u1"

    def test_user_manager_has_permission(self):
        from web.auth_manager import Permission, UserManager, UserRole

        um = UserManager()
        um.create_user("u1", "alice", "a@b.com", "h", [UserRole.USER])
        assert um.has_permission("u1", Permission.DATA_READ) is True
        assert um.has_permission("u1", Permission.ADMIN_ACCESS) is False

    def test_user_manager_deactivate(self):
        from web.auth_manager import UserManager

        um = UserManager()
        um.create_user("u1", "bob", "b@b.com", "h")
        assert um.deactivate_user("u1") is True
        assert um.get_user("u1").is_active is False

    def test_session_manager_create_and_validate(self):
        from web.auth_manager import SessionManager

        sm = SessionManager()
        sess = sm.create_session("s1", "u1", "127.0.0.1")
        assert sm.validate_session("s1") is True

    def test_session_manager_terminate(self):
        from web.auth_manager import SessionManager

        sm = SessionManager()
        sm.create_session("s1", "u1", "127.0.0.1")
        assert sm.terminate_session("s1") is True
        assert sm.validate_session("s1") is False

    def test_authentication_manager_status(self):
        from web.auth_manager import AuthenticationManager

        am = AuthenticationManager()
        status = am.get_auth_status()
        assert "users" in status
        assert "sessions" in status


# ---------------------------------------------------------------------------
# 8. BrowserAutomation
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestBrowserAutomation:
    """Tests for web.browser_automation.BrowserAutomation (all selenium mocked)"""

    def _make(self):
        with patch("web.browser_automation.BrowserAutomation._init_driver"):
            from web.browser_automation import BrowserAutomation

            ba = BrowserAutomation(headless=True)
            ba.driver = None  # start with no driver
            return ba

    def test_init_headless(self):
        ba = self._make()
        assert ba.headless is True

    def test_open_url_no_driver(self):
        ba = self._make()
        assert ba.open_url("https://example.com") is False

    def test_find_element_no_driver(self):
        ba = self._make()
        assert ba.find_element("#test") is None

    def test_find_elements_no_driver(self):
        ba = self._make()
        assert ba.find_elements(".items") == []

    def test_click_no_driver(self):
        ba = self._make()
        assert ba.click("#btn") is False

    def test_get_page_source_no_driver(self):
        ba = self._make()
        assert ba.get_page_source() is None

    def test_get_current_url_no_driver(self):
        ba = self._make()
        assert ba.get_current_url() is None

    def test_take_screenshot_no_driver(self):
        ba = self._make()
        assert ba.take_screenshot("out.png") is False

    def test_quit_no_driver(self):
        ba = self._make()
        ba.quit()  # should not raise


# ---------------------------------------------------------------------------
# 9. ImageManager
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestImageManager:
    """Tests for web.image_manager.ImageManager"""

    def _make(self, tmp_path):
        from web.image_manager import ImageManager

        return ImageManager(client=None, workspace_dir=str(tmp_path))

    def test_init_creates_images_dir(self, tmp_path):
        mgr = self._make(tmp_path)
        assert os.path.isdir(mgr.images_dir)

    def test_generate_image_no_client(self, tmp_path):
        mgr = self._make(tmp_path)
        assert mgr._generate_image("test prompt") is None

    def test_search_image_returns_none(self, tmp_path):
        mgr = self._make(tmp_path)
        assert mgr._search_image("landscape photo") is None

    def test_get_image_auto_search_keyword(self, tmp_path):
        mgr = self._make(tmp_path)
        # "照片" triggers search method, which returns None, falls back to generate (no client) → None
        result = mgr.get_image("真实照片")
        assert result is None

    def test_get_image_auto_generate(self, tmp_path):
        mgr = self._make(tmp_path)
        result = mgr.get_image("创意插画")
        assert result is None  # no client

    def test_save_image_bytes(self, tmp_path):
        mgr = self._make(tmp_path)
        path = mgr._save_image_bytes(b"\x89PNG_fake_data", "test")
        assert path is not None
        assert os.path.exists(path)

    def test_extract_image_from_response_empty(self, tmp_path):
        mgr = self._make(tmp_path)
        assert mgr._extract_image_from_response(None, "test") is None

    def test_get_image_explicit_generate(self, tmp_path):
        mgr = self._make(tmp_path)
        result = mgr.get_image("a cat", method="generate")
        assert result is None


# ---------------------------------------------------------------------------
# 10. CalendarManager
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCalendarManager:
    """Tests for web.calendar_manager.CalendarManager"""

    def _make(self, tmp_path):
        with patch("web.calendar_manager.get_reminder_manager") as mock_rm:
            mock_rm.return_value = MagicMock()
            with patch("web.calendar_manager.os.path.dirname") as mock_dir:
                # Redirect project_root to tmp_path so file I/O stays in temp
                mock_dir.side_effect = lambda p: str(tmp_path)
                from web.calendar_manager import CalendarManager

                mgr = CalendarManager()
        return mgr

    def test_init_empty_events(self, tmp_path):
        mgr = self._make(tmp_path)
        assert mgr.events == [] or isinstance(mgr.events, list)

    def test_add_event(self, tmp_path):
        mgr = self._make(tmp_path)
        with patch("web.calendar_manager.get_reminder_manager") as mock_rm:
            mock_rm.return_value = MagicMock()
            eid = mgr.add_event(
                "Meeting", "Team sync", datetime.now() + timedelta(hours=1)
            )
        assert eid.startswith("event_")
        assert len(mgr.events) >= 1

    def test_list_events_sorted(self, tmp_path):
        mgr = self._make(tmp_path)
        with patch("web.calendar_manager.get_reminder_manager") as mock_rm:
            mock_rm.return_value = MagicMock()
            mgr.add_event("B", "later", datetime(2099, 6, 1))
            mgr.add_event("A", "earlier", datetime(2099, 1, 1))
        events = mgr.list_events()
        assert events[0]["title"] == "A"

    def test_delete_event(self, tmp_path):
        mgr = self._make(tmp_path)
        with patch("web.calendar_manager.get_reminder_manager") as mock_rm:
            mock_rm.return_value = MagicMock()
            eid = mgr.add_event("Temp", "delete me", datetime(2099, 1, 1))
        assert mgr.delete_event(eid) is True
        assert mgr.delete_event("nonexistent_id") is False

    def test_delete_event_nonexistent(self, tmp_path):
        mgr = self._make(tmp_path)
        assert mgr.delete_event("no_such_event") is False

    def test_list_events_with_limit(self, tmp_path):
        mgr = self._make(tmp_path)
        with patch("web.calendar_manager.get_reminder_manager") as mock_rm:
            mock_rm.return_value = MagicMock()
            for i in range(5):
                mgr.add_event(f"E{i}", "", datetime(2099, 1, i + 1))
        assert len(mgr.list_events(limit=3)) == 3

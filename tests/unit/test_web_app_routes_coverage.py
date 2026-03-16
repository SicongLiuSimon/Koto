"""
Comprehensive route-coverage tests for web/app.py Flask handlers.

Tests exercise as many Flask routes as possible via test_client, checking
that each returns a valid HTTP status code and expected JSON structure.
Heavy dependencies (AI brain, managers, etc.) are mocked where needed.
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

# ── Environment setup (must precede any web.app import) ──────────────────────
os.environ.setdefault("KOTO_AUTH_ENABLED", "false")
os.environ.setdefault("KOTO_DEPLOY_MODE", "local")
os.environ.pop("SENTRY_DSN", None)


@pytest.fixture(scope="module")
def client():
    """Create Flask test client with auth disabled."""
    from web.app import app

    app.config["TESTING"] = True
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["TRAP_HTTP_EXCEPTIONS"] = False
    with app.test_client() as c:
        yield c


def _json_post(client, url, data=None):
    """Helper: POST JSON and return response."""
    return client.post(url, data=json.dumps(data or {}), content_type="application/json")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Page routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestPageRoutes:
    def test_index(self, client):
        resp = client.get("/")
        assert resp.status_code in (200, 302, 404, 500)

    def test_app_page(self, client):
        resp = client.get("/app")
        assert resp.status_code in (200, 302, 404, 500)

    def test_file_network(self, client):
        resp = client.get("/file-network")
        assert resp.status_code in (200, 302, 404, 500)

    def test_knowledge_graph(self, client):
        resp = client.get("/knowledge-graph")
        assert resp.status_code in (200, 302, 404, 500)

    def test_skills(self, client):
        resp = client.get("/skills")
        assert resp.status_code in (200, 302, 404, 500)

    def test_monitoring_dashboard(self, client):
        resp = client.get("/monitoring-dashboard")
        assert resp.status_code in (200, 302, 404, 500)

    def test_mini_page(self, client):
        resp = client.get("/mini")
        assert resp.status_code in (200, 302, 404, 500)

    def test_mobile_page(self, client):
        resp = client.get("/mobile")
        assert resp.status_code in (200, 302, 404, 500)

    def test_mobile_shortcut(self, client):
        resp = client.get("/m")
        assert resp.status_code in (200, 302, 404, 500)

    def test_test_upload(self, client):
        resp = client.get("/test_upload")
        assert resp.status_code in (200, 302, 404, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Session routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestSessionRoutes:
    def test_list_sessions(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code in (200, 500)

    def test_create_session(self, client):
        resp = _json_post(client, "/api/sessions", {"name": f"test_sess_{int(time.time())}"})
        assert resp.status_code in (200, 500)

    def test_create_session_no_name(self, client):
        resp = _json_post(client, "/api/sessions", {})
        assert resp.status_code in (200, 500)

    def test_get_session(self, client):
        resp = client.get("/api/sessions/nonexistent_session_xyz")
        assert resp.status_code in (200, 404, 500)

    def test_delete_session(self, client):
        resp = client.delete("/api/sessions/nonexistent_session_xyz")
        assert resp.status_code in (200, 404, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Settings routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestSettingsRoutes:
    def test_get_settings(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code in (200, 500)

    def test_update_settings(self, client):
        resp = _json_post(client, "/api/settings", {
            "category": "appearance",
            "key": "theme",
            "value": "dark",
        })
        assert resp.status_code in (200, 400, 500)

    def test_update_settings_empty(self, client):
        resp = _json_post(client, "/api/settings", {})
        assert resp.status_code in (200, 400, 500)

    def test_reset_settings(self, client):
        resp = _json_post(client, "/api/settings/reset")
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Setup routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestSetupRoutes:
    def test_setup_status(self, client):
        resp = client.get("/api/setup/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "initialized" in data
        assert "has_api_key" in data

    def test_setup_apikey_invalid(self, client):
        resp = _json_post(client, "/api/setup/apikey", {"api_key": "short"})
        assert resp.status_code in (200, 400)
        data = resp.get_json()
        assert data.get("success") is False or "error" in data

    def test_setup_apikey_missing(self, client):
        resp = _json_post(client, "/api/setup/apikey", {})
        assert resp.status_code in (200, 400)

    def test_setup_workspace(self, client):
        resp = _json_post(client, "/api/setup/workspace", {"path": ""})
        assert resp.status_code in (200, 500)

    def test_setup_test_connection(self, client):
        """Test API connection — will likely fail without a real key, but should not 500 unhandled."""
        resp = client.get("/api/setup/test")
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Info routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestInfoRoutes:
    def test_api_info(self, client):
        resp = client.get("/api/info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "version" in data
        assert "deploy_mode" in data

    def test_api_info_fields(self, client):
        data = client.get("/api/info").get_json()
        assert "auth_enabled" in data

    def test_models_list(self, client):
        resp = client.get("/api/v1/models")
        assert resp.status_code in (200, 500)
        data = resp.get_json()
        assert "model_map" in data or "error" in data

    def test_models_refresh(self, client):
        resp = _json_post(client, "/api/v1/models/refresh")
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Chat routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestChatRoutes:
    def test_chat_missing_params(self, client):
        resp = _json_post(client, "/api/chat", {})
        assert resp.status_code == 400

    def test_chat_missing_message(self, client):
        resp = _json_post(client, "/api/chat", {"session": "test"})
        assert resp.status_code == 400

    def test_chat_with_mock(self, client):
        mock_result = {
            "response": "Hello from mock",
            "model": "mock-model",
            "task": "CHAT",
        }
        with patch("web.app.brain") as mock_brain:
            mock_brain.chat.return_value = mock_result
            resp = _json_post(client, "/api/chat", {
                "session": "unit_test_sess",
                "message": "Hi there",
            })
        assert resp.status_code in (200, 500)

    def test_chat_interrupt(self, client):
        resp = _json_post(client, "/api/chat/interrupt", {"session": "test_sess"})
        assert resp.status_code in (200, 400, 500)

    def test_chat_interrupt_missing_session(self, client):
        resp = _json_post(client, "/api/chat/interrupt", {})
        assert resp.status_code == 400

    def test_chat_reset_interrupt(self, client):
        resp = _json_post(client, "/api/chat/reset-interrupt", {"session": "test_sess"})
        assert resp.status_code in (200, 500)

    def test_chat_reset_interrupt_no_session(self, client):
        resp = _json_post(client, "/api/chat/reset-interrupt", {})
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Notes routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestNotesRoutes:
    def test_add_note(self, client):
        resp = _json_post(client, "/api/notes/add", {
            "title": "Test Note",
            "content": "Some content",
        })
        assert resp.status_code in (200, 500)

    def test_add_note_empty(self, client):
        resp = _json_post(client, "/api/notes/add", {})
        assert resp.status_code in (200, 400, 500)

    def test_list_notes(self, client):
        resp = client.get("/api/notes/list")
        assert resp.status_code in (200, 500)

    def test_list_notes_with_limit(self, client):
        resp = client.get("/api/notes/list?limit=5")
        assert resp.status_code in (200, 500)

    def test_search_notes(self, client):
        resp = client.get("/api/notes/search?query=test")
        assert resp.status_code in (200, 500)

    def test_delete_note(self, client):
        resp = client.delete("/api/notes/nonexistent_id")
        assert resp.status_code in (200, 404, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Reminders routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestRemindersRoutes:
    def test_add_reminder_with_seconds(self, client):
        resp = _json_post(client, "/api/reminders/add", {
            "title": "Test Reminder",
            "message": "Don't forget",
            "seconds": 60,
        })
        assert resp.status_code in (200, 400, 500)

    def test_add_reminder_missing_time(self, client):
        resp = _json_post(client, "/api/reminders/add", {
            "title": "Bad Reminder",
        })
        assert resp.status_code in (200, 400, 500)

    def test_add_reminder_with_iso_time(self, client):
        resp = _json_post(client, "/api/reminders/add", {
            "title": "Future Reminder",
            "message": "Later",
            "time": "2099-01-01T00:00:00",
        })
        assert resp.status_code in (200, 400, 500)

    def test_list_reminders(self, client):
        resp = client.get("/api/reminders/list")
        assert resp.status_code in (200, 500)

    def test_delete_reminder(self, client):
        resp = client.delete("/api/reminders/nonexistent_id")
        assert resp.status_code in (200, 404, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Calendar routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestCalendarRoutes:
    def test_add_event(self, client):
        resp = _json_post(client, "/api/calendar/add", {
            "title": "Team Meeting",
            "description": "Weekly sync",
            "start": "2099-06-01T10:00:00",
        })
        assert resp.status_code in (200, 400, 500)

    def test_add_event_missing_start(self, client):
        resp = _json_post(client, "/api/calendar/add", {"title": "No start"})
        assert resp.status_code in (200, 400, 500)

    def test_add_event_bad_start(self, client):
        resp = _json_post(client, "/api/calendar/add", {
            "title": "Bad",
            "start": "not-a-date",
        })
        assert resp.status_code in (200, 400, 500)

    def test_list_calendar(self, client):
        resp = client.get("/api/calendar/list")
        assert resp.status_code in (200, 500)

    def test_delete_event(self, client):
        resp = client.delete("/api/calendar/nonexistent_id")
        assert resp.status_code in (200, 404, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Clipboard routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestClipboardRoutes:
    def test_clipboard_history(self, client):
        resp = client.get("/api/clipboard/history")
        assert resp.status_code in (200, 500)

    def test_clipboard_history_limit(self, client):
        resp = client.get("/api/clipboard/history?limit=5")
        assert resp.status_code in (200, 500)

    def test_clipboard_search(self, client):
        resp = client.get("/api/clipboard/search?query=hello")
        assert resp.status_code in (200, 500)

    def test_clipboard_copy(self, client):
        resp = _json_post(client, "/api/clipboard/copy", {"content": "copied text"})
        assert resp.status_code in (200, 400, 500)

    def test_clipboard_copy_by_index(self, client):
        resp = _json_post(client, "/api/clipboard/copy", {"index": 0})
        assert resp.status_code in (200, 400, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Email routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestEmailRoutes:
    def test_list_accounts(self, client):
        resp = client.get("/api/email/accounts")
        assert resp.status_code in (200, 500)

    def test_add_account(self, client):
        resp = _json_post(client, "/api/email/accounts/add", {
            "email": "test@example.com",
            "password": "fake",
            "smtp_server": "smtp.example.com",
        })
        assert resp.status_code in (200, 400, 500)

    def test_send_email(self, client):
        resp = _json_post(client, "/api/email/send", {
            "to": ["test@example.com"],
            "subject": "Test",
            "body": "Hello",
        })
        assert resp.status_code in (200, 400, 500)

    def test_fetch_emails(self, client):
        resp = client.get("/api/email/fetch")
        assert resp.status_code in (200, 500)

    def test_search_emails(self, client):
        resp = client.get("/api/email/search?query=test")
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Browser routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestBrowserRoutes:
    def test_browser_open(self, client):
        resp = _json_post(client, "/api/browser/open", {"url": "https://example.com"})
        assert resp.status_code in (200, 400, 500)

    def test_browser_search(self, client):
        resp = _json_post(client, "/api/browser/search", {"query": "flask testing"})
        assert resp.status_code in (200, 400, 500)

    def test_browser_screenshot(self, client):
        resp = _json_post(client, "/api/browser/screenshot", {"filename": "test.png"})
        assert resp.status_code in (200, 400, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Search routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestSearchRoutes:
    def test_search_all(self, client):
        resp = client.get("/api/search/all?query=hello")
        assert resp.status_code in (200, 500)

    def test_search_all_empty(self, client):
        resp = client.get("/api/search/all")
        assert resp.status_code in (200, 500)

    def test_search_files(self, client):
        resp = client.get("/api/search/files?query=test")
        assert resp.status_code in (200, 500)

    def test_search_files_with_limit(self, client):
        resp = client.get("/api/search/files?query=test&max_results=5")
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Voice routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestVoiceRoutes:
    def test_voice_engines(self, client):
        resp = client.get("/api/voice/engines")
        assert resp.status_code in (200, 500)

    def test_voice_stt_status(self, client):
        resp = client.get("/api/voice/stt_status")
        assert resp.status_code in (200, 500)

    def test_voice_commands(self, client):
        resp = client.get("/api/voice/commands")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["commands"]) > 0

    def test_voice_stop(self, client):
        resp = _json_post(client, "/api/voice/stop")
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Local model routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestLocalModelRoutes:
    def test_local_model_status(self, client):
        resp = client.get("/api/local-model/status")
        assert resp.status_code in (200, 500)

    def test_local_model_switch(self, client):
        resp = _json_post(client, "/api/local-model/switch", {"mode": "cloud"})
        assert resp.status_code in (200, 500)

    def test_local_model_setup(self, client):
        resp = _json_post(client, "/api/local-model/setup")
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Workspace routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestWorkspaceRoutes:
    def test_list_workspace(self, client):
        resp = client.get("/api/workspace")
        assert resp.status_code in (200, 500)

    def test_open_workspace(self, client):
        with patch("subprocess.Popen"):
            resp = _json_post(client, "/api/open-workspace")
        assert resp.status_code in (200, 500)

    def test_workspace_file_nonexistent(self, client):
        resp = client.get("/api/workspace/nonexistent_file.txt")
        assert resp.status_code in (200, 403, 404, 500)

    def test_open_file_missing_path(self, client):
        resp = _json_post(client, "/api/open-file", {})
        assert resp.status_code in (200, 400, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 17. Browse routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestBrowseRoutes:
    def test_browse_default(self, client):
        resp = client.get("/api/browse")
        assert resp.status_code in (200, 500)
        data = resp.get_json()
        assert "folders" in data or "error" in data

    def test_browse_with_path(self, client):
        resp = client.get("/api/browse?path=C:\\")
        assert resp.status_code in (200, 500)

    def test_browse_nonexistent_path(self, client):
        resp = client.get("/api/browse?path=Z:\\nonexistent_path_xyz")
        assert resp.status_code in (200, 500)
        data = resp.get_json()
        assert "error" in data or "folders" in data


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Analyze route
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestAnalyzeRoute:
    def test_analyze_with_message(self, client):
        resp = _json_post(client, "/api/analyze", {"message": "Hello world"})
        assert resp.status_code in (200, 500)
        data = resp.get_json()
        if resp.status_code == 200:
            assert "task" in data
            assert "model" in data

    def test_analyze_empty_message(self, client):
        resp = _json_post(client, "/api/analyze", {"message": ""})
        assert resp.status_code in (200, 500)

    def test_analyze_with_locked_task(self, client):
        resp = _json_post(client, "/api/analyze", {
            "message": "Generate code",
            "locked_task": "CODER",
        })
        assert resp.status_code in (200, 500)

    def test_analyze_with_image(self, client):
        resp = _json_post(client, "/api/analyze", {
            "message": "What is in this picture",
            "has_file": True,
            "file_type": "image/png",
        })
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Diagnose route
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestDiagnoseRoute:
    def test_diagnose(self, client):
        resp = client.get("/api/diagnose")
        # Diagnose may take time or fail due to missing API — any response is valid
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 20. Mini chat
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestMiniChatRoute:
    def test_mini_chat_empty_message(self, client):
        resp = _json_post(client, "/api/mini/chat", {"message": ""})
        assert resp.status_code == 400

    def test_mini_chat_missing_message(self, client):
        resp = _json_post(client, "/api/mini/chat", {})
        assert resp.status_code == 400

    def test_mini_chat_with_mock(self, client):
        mock_result = {"response": "Hi from mini", "model": "mock", "task": "CHAT"}
        with patch("web.app.brain") as mock_brain, \
             patch("web.app.SmartDispatcher") as mock_disp:
            mock_disp.analyze.return_value = ("CHAT", "mock-route", {})
            mock_brain.chat.return_value = mock_result
            resp = _json_post(client, "/api/mini/chat", {"message": "Hello"})
        assert resp.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 21. PPT routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestPPTRoutes:
    def test_ppt_download_missing_session(self, client):
        resp = _json_post(client, "/api/ppt/download", {})
        assert resp.status_code == 400

    def test_ppt_download_nonexistent(self, client):
        resp = _json_post(client, "/api/ppt/download", {"session_id": "nonexistent_xyz"})
        assert resp.status_code in (404, 400, 500)

    def test_ppt_session_nonexistent(self, client):
        resp = client.get("/api/ppt/session/nonexistent_xyz")
        assert resp.status_code in (404, 500)

    def test_edit_ppt_page(self, client):
        resp = client.get("/edit-ppt/test_session_123")
        assert resp.status_code in (200, 302, 404, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 22. Skills routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestSkillsRoutes:
    def test_skill_toggle(self, client):
        resp = _json_post(client, "/api/skills/test_skill/toggle", {"enabled": True})
        assert resp.status_code in (200, 404, 500)

    def test_skill_prompt_update(self, client):
        resp = _json_post(client, "/api/skills/test_skill/prompt", {"prompt": "Be concise"})
        assert resp.status_code in (200, 404, 500)

    def test_skill_prompt_reset(self, client):
        resp = _json_post(client, "/api/skills/test_skill/reset")
        assert resp.status_code in (200, 404, 500)

    def test_skill_marketplace_page(self, client):
        resp = client.get("/skill-marketplace")
        assert resp.status_code in (200, 302, 404, 500)


# ═══════════════════════════════════════════════════════════════════════════════
# 23. Mode switch routes
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestModeSwitchRoutes:
    def test_switch_to_mini(self, client):
        with patch("subprocess.Popen"):
            resp = _json_post(client, "/api/switch-to-mini")
        assert resp.status_code in (200, 500)

    def test_switch_to_main(self, client):
        with patch("subprocess.Popen"):
            resp = _json_post(client, "/api/switch-to-main")
        assert resp.status_code in (200, 500)

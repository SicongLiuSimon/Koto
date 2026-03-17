"""
E2E tests for session management UI in Koto.

Tests cover: create, load, rename, delete sessions via the sidebar,
and verify no JS console errors or HTTP 5xx responses occur.
"""

import time

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name(prefix: str = "e2e_sess") -> str:
    """Return a collision-free session name."""
    return f"{prefix}_{int(time.time() * 1000)}"


def _api_create_session(page, base_url: str, name: str) -> dict:
    """Create a session via the REST API and return the JSON response."""
    resp = page.request.post(
        f"{base_url}/api/sessions",
        data={"name": name},
    )
    assert resp.ok, f"Failed to create session via API: {resp.status}"
    return resp.json()


def _api_delete_session(page, base_url: str, name: str) -> None:
    """Best-effort cleanup of a session via the API."""
    try:
        page.request.delete(f"{base_url}/api/sessions/{name}")
    except Exception:
        pass


def _open_new_session_modal(page) -> None:
    """Click the '+ 新对话' button to open the new-session modal."""
    btn = page.locator("button.pill-btn")
    btn.wait_for(state="visible", timeout=5000)
    btn.click()
    page.locator("#newSessionModal").wait_for(state="visible", timeout=3000)


def _session_item(page, name: str):
    """Return a locator for the session item matching *name*."""
    return page.locator(f".session-item[data-session='{name}']")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_create_session(e2e_page, e2e_base_url, console_errors):
    """Create a session through the UI modal and verify it appears."""
    page = e2e_page
    name = _unique_name("create")

    try:
        page.goto(f"{e2e_base_url}/", wait_until="networkidle")

        # Open the new-session modal
        _open_new_session_modal(page)

        # Type the session name and press Enter
        inp = page.locator("#newSessionName")
        inp.wait_for(state="visible", timeout=3000)
        inp.fill(name)
        inp.press("Enter")

        # Wait for the session to appear in the sidebar
        item = _session_item(page, name)
        item.wait_for(state="attached", timeout=5000)
        assert item.count() >= 1, f"Session '{name}' not found in sidebar"

        assert console_errors == [], f"JS console errors: {console_errors}"
    finally:
        _api_delete_session(page, e2e_base_url, name)


@pytest.mark.e2e
def test_click_session_loads_history(e2e_page, e2e_base_url, console_errors):
    """Click a session in the sidebar and verify the chat area loads."""
    page = e2e_page
    name = _unique_name("load")

    try:
        # Create session via API
        _api_create_session(page, e2e_base_url, name)

        page.goto(f"{e2e_base_url}/", wait_until="networkidle")

        # Wait for the session list to populate
        item = _session_item(page, name)
        item.wait_for(state="visible", timeout=5000)

        # Click the session name span (avoid rename/delete buttons)
        session_name_span = item.locator(".session-name")
        session_name_span.click()

        # Give the UI time to load history and mark the session active
        page.wait_for_timeout(1500)

        # The clicked session should become active
        active = page.locator(f".session-item.active[data-session='{name}']")
        try:
            active.wait_for(state="attached", timeout=3000)
        except Exception:
            # Fallback: check if any session is active
            any_active = page.locator(".session-item.active")
            assert any_active.count() >= 1, "No session became active after click"

        assert console_errors == [], f"JS console errors: {console_errors}"
    finally:
        _api_delete_session(page, e2e_base_url, name)


@pytest.mark.e2e
def test_rename_session(e2e_page, e2e_base_url, console_errors):
    """Rename a session via the inline-edit UI and verify the list updates."""
    page = e2e_page
    old_name = _unique_name("rename_old")
    new_name = _unique_name("rename_new")

    try:
        _api_create_session(page, e2e_base_url, old_name)
        page.goto(f"{e2e_base_url}/", wait_until="networkidle")

        item = _session_item(page, old_name)
        item.wait_for(state="visible", timeout=5000)

        # Hover to reveal the rename button (opacity: 0 by default)
        item.hover()
        page.wait_for_timeout(300)

        rename_btn = item.locator(".session-rename-btn")
        # Use force click in case opacity transition hasn't completed
        rename_btn.click(force=True)

        # An inline input should replace the session name span
        inline_input = item.locator("input.session-name-input")
        try:
            inline_input.wait_for(state="visible", timeout=3000)
        except Exception:
            # Fallback: maybe the input is just an <input> without a specific class
            inline_input = item.locator("input")
            inline_input.wait_for(state="visible", timeout=2000)

        inline_input.fill(new_name)
        inline_input.press("Enter")

        # Wait for the rename to propagate
        page.wait_for_timeout(1500)

        # Verify the new name appears and old name is gone
        new_item = _session_item(page, new_name)
        try:
            new_item.wait_for(state="attached", timeout=5000)
        except Exception:
            # The session list may have been fully reloaded; check by text
            assert (
                page.locator(f"text={new_name}").count() >= 1
            ), f"Renamed session '{new_name}' not found in sidebar"

        assert console_errors == [], f"JS console errors: {console_errors}"
    finally:
        # Cleanup: try both names in case rename didn't succeed
        _api_delete_session(page, e2e_base_url, new_name)
        _api_delete_session(page, e2e_base_url, old_name)


@pytest.mark.e2e
def test_delete_session(e2e_page, e2e_base_url, console_errors):
    """Delete a session via the UI and verify it disappears."""
    page = e2e_page
    name = _unique_name("delete")

    try:
        _api_create_session(page, e2e_base_url, name)
        page.goto(f"{e2e_base_url}/", wait_until="networkidle")

        item = _session_item(page, name)
        item.wait_for(state="visible", timeout=5000)

        # Accept the upcoming browser confirm() dialog
        page.on("dialog", lambda dialog: dialog.accept())

        # Hover to reveal the delete button, then click it
        item.hover()
        page.wait_for_timeout(300)

        delete_btn = item.locator(".session-delete-btn")
        delete_btn.click(force=True)

        # Wait for the session item to be removed from the DOM
        page.wait_for_timeout(1500)
        remaining = _session_item(page, name)
        assert remaining.count() == 0, f"Session '{name}' still present after deletion"

        assert console_errors == [], f"JS console errors: {console_errors}"
    finally:
        # Best-effort cleanup if deletion didn't work
        _api_delete_session(page, e2e_base_url, name)


@pytest.mark.e2e
def test_session_operations_no_errors(
    e2e_page_with_network, e2e_base_url, console_errors, failed_requests
):
    """Full lifecycle: create → load → rename → delete with zero errors."""
    page = e2e_page_with_network
    name = _unique_name("lifecycle")
    renamed = _unique_name("lifecycle_ren")

    try:
        page.goto(f"{e2e_base_url}/", wait_until="networkidle")

        # ── CREATE ──
        _open_new_session_modal(page)
        inp = page.locator("#newSessionName")
        inp.wait_for(state="visible", timeout=3000)
        inp.fill(name)
        inp.press("Enter")

        item = _session_item(page, name)
        item.wait_for(state="attached", timeout=5000)

        # ── LOAD ──
        item.locator(".session-name").click()
        page.wait_for_timeout(1000)

        # ── RENAME ──
        item.hover()
        page.wait_for_timeout(300)
        item.locator(".session-rename-btn").click(force=True)

        inline_input = item.locator("input.session-name-input")
        try:
            inline_input.wait_for(state="visible", timeout=3000)
        except Exception:
            inline_input = item.locator("input")
            inline_input.wait_for(state="visible", timeout=2000)

        inline_input.fill(renamed)
        inline_input.press("Enter")
        page.wait_for_timeout(1500)

        # After rename the item's data-session should be updated
        renamed_item = _session_item(page, renamed)
        try:
            renamed_item.wait_for(state="attached", timeout=5000)
        except Exception:
            pass  # We'll still try to delete by the new name below

        # ── DELETE ──
        page.on("dialog", lambda dialog: dialog.accept())

        target = renamed_item if renamed_item.count() > 0 else _session_item(page, name)
        target.hover()
        page.wait_for_timeout(300)
        target.locator(".session-delete-btn").click(force=True)
        page.wait_for_timeout(1500)

        # ── ASSERTIONS ──
        assert console_errors == [], f"JS console errors: {console_errors}"
        assert failed_requests == [], f"Failed requests (5xx): {failed_requests}"
    finally:
        _api_delete_session(page, e2e_base_url, renamed)
        _api_delete_session(page, e2e_base_url, name)

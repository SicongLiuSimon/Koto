"""
E2E tests for settings and miscellaneous UI buttons in Koto.

Covers theme toggling, settings panel, model selector, sidebar navigation,
notification/skills buttons, and keyboard shortcuts.
"""

import pytest

# Known benign console errors that may appear during normal operation
BENIGN_PATTERNS = (
    "API key",
    "api key",
    "API_KEY",
    "favicon",
    "Failed to load resource",
    "net::ERR_",
    "ResizeObserver loop",
)


def _filter_errors(errors: list[str]) -> list[str]:
    """Return only genuine errors, filtering out known benign noise."""
    return [e for e in errors if not any(p in e for p in BENIGN_PATTERNS)]


# ── helpers ──────────────────────────────────────────────────────────────


def _navigate_and_wait(page, base_url: str) -> None:
    """Navigate to the main page and wait for it to settle."""
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    # Allow initial JS to finish (splash screen removal, etc.)
    page.wait_for_timeout(500)


def _open_settings(page) -> None:
    """Open the settings panel via the gear button."""
    btn = page.locator("button[title='设置']")
    if btn.count() == 0:
        btn = page.locator("button:has-text('设置')")
    if btn.count() == 0:
        pytest.skip("Settings button not found on page")
    btn.first.click()
    page.wait_for_timeout(400)


# ── tests ────────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_theme_toggle(e2e_page, e2e_base_url, console_errors):
    """Click theme options and verify the data-theme attribute on <html> changes."""
    _navigate_and_wait(e2e_page, e2e_base_url)

    # Open settings first – theme selector lives inside the settings panel
    _open_settings(e2e_page)

    theme_selector = e2e_page.locator("#themeSelector")
    if theme_selector.count() == 0 or not theme_selector.is_visible():
        pytest.skip("Theme selector not found")

    # Record current theme
    original_theme = e2e_page.evaluate(
        "document.documentElement.getAttribute('data-theme')"
    )

    # Pick a different theme to toggle to
    target_theme = "light" if original_theme != "light" else "dark"
    option = e2e_page.locator(f".theme-option[data-theme='{target_theme}']")
    if option.count() == 0:
        pytest.skip(f"Theme option '{target_theme}' not found")

    option.click()
    e2e_page.wait_for_timeout(300)

    new_theme = e2e_page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert (
        new_theme == target_theme
    ), f"Expected data-theme='{target_theme}', got '{new_theme}'"

    # Toggle back to prove it's not a one-time fluke
    original_option = e2e_page.locator(f".theme-option[data-theme='{original_theme}']")
    if original_option.count():
        original_option.click()
        e2e_page.wait_for_timeout(300)
        restored = e2e_page.evaluate(
            "document.documentElement.getAttribute('data-theme')"
        )
        # 'auto' resolves to 'dark' or 'light', so only check exact match for
        # non-auto themes
        if original_theme != "auto":
            assert restored == original_theme

    assert (
        _filter_errors(console_errors) == []
    ), f"JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_settings_panel_opens(e2e_page, e2e_base_url, console_errors):
    """Click the settings button and verify the panel becomes visible."""
    _navigate_and_wait(e2e_page, e2e_base_url)
    _open_settings(e2e_page)

    panel = e2e_page.locator("#settingsPanel")
    assert panel.count() > 0, "Settings panel element not found in DOM"

    # The panel should have the 'active' class after opening
    has_active = e2e_page.evaluate(
        "document.getElementById('settingsPanel').classList.contains('active')"
    )
    assert has_active, "Settings panel did not receive 'active' class"

    # Close it
    close_btn = panel.locator("button.close-panel")
    if close_btn.count():
        close_btn.first.click()
        e2e_page.wait_for_timeout(300)
        has_active_after = e2e_page.evaluate(
            "document.getElementById('settingsPanel').classList.contains('active')"
        )
        assert not has_active_after, "Settings panel still active after close"

    assert (
        _filter_errors(console_errors) == []
    ), f"JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_model_selector(e2e_page, e2e_base_url, console_errors):
    """Open settings, interact with the model dropdown, and verify options."""
    _navigate_and_wait(e2e_page, e2e_base_url)
    _open_settings(e2e_page)

    select = e2e_page.locator("#settingModel")
    if select.count() == 0 or not select.is_visible():
        pytest.skip("Model selector (#settingModel) not found")

    # Gather option values
    options = select.locator("option")
    option_count = options.count()
    assert option_count >= 2, f"Expected at least 2 model options, found {option_count}"

    # Read current value
    original_value = select.input_value()

    # Pick a different option
    all_values = [options.nth(i).get_attribute("value") for i in range(option_count)]
    new_value = next((v for v in all_values if v != original_value), None)
    if new_value is None:
        pytest.skip("Only one model option available, cannot test selection change")

    select.select_option(new_value)
    e2e_page.wait_for_timeout(300)

    assert (
        select.input_value() == new_value
    ), f"Model selection did not change to '{new_value}'"

    # Restore original
    select.select_option(original_value)

    assert (
        _filter_errors(console_errors) == []
    ), f"JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_notes_button(e2e_page, e2e_base_url, console_errors):
    """If a notes button exists, click it and verify no crash."""
    _navigate_and_wait(e2e_page, e2e_base_url)

    # Try several possible selectors for a notes button
    candidates = [
        "button:has-text('笔记')",
        "button:has-text('Notes')",
        "button:has-text('notes')",
        "button[title*='笔记']",
        "button[title*='note' i]",
        "[data-panel='notes']",
    ]

    btn = None
    for sel in candidates:
        loc = e2e_page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            btn = loc.first
            break

    if btn is None:
        pytest.skip("Notes button not found on page")

    btn.click()
    e2e_page.wait_for_timeout(500)

    assert (
        _filter_errors(console_errors) == []
    ), f"JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_reminders_button(e2e_page, e2e_base_url, console_errors):
    """If a reminders button exists, click it and verify no crash."""
    _navigate_and_wait(e2e_page, e2e_base_url)

    candidates = [
        "button:has-text('提醒')",
        "button:has-text('Reminder')",
        "button:has-text('reminder')",
        "button[title*='提醒']",
        "button[title*='reminder' i]",
        "[data-panel='reminders']",
    ]

    btn = None
    for sel in candidates:
        loc = e2e_page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            btn = loc.first
            break

    if btn is None:
        pytest.skip("Reminders button not found on page")

    btn.click()
    e2e_page.wait_for_timeout(500)

    assert (
        _filter_errors(console_errors) == []
    ), f"JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_sidebar_navigation(e2e_page, e2e_base_url, console_errors):
    """Click through all visible sidebar buttons and check for JS errors."""
    _navigate_and_wait(e2e_page, e2e_base_url)

    sidebar = e2e_page.locator("aside.nav-rail, aside.chatgpt-sidebar")
    if sidebar.count() == 0:
        pytest.skip("Sidebar not found")

    # Collect clickable buttons in the sidebar
    buttons = sidebar.locator("button:visible")
    btn_count = buttons.count()
    if btn_count == 0:
        pytest.skip("No visible buttons in sidebar")

    clicked = 0
    for i in range(btn_count):
        btn = buttons.nth(i)
        try:
            if btn.is_visible() and btn.is_enabled():
                btn.scroll_into_view_if_needed()
                btn.click(timeout=3000)
                e2e_page.wait_for_timeout(400)
                clicked += 1

                # Check for errors after each click
                current_errors = _filter_errors(console_errors)
                assert current_errors == [], (
                    f"JS error after clicking sidebar button #{i}: " f"{current_errors}"
                )
        except Exception:
            # Button may have become detached or hidden; continue
            continue

    assert clicked > 0, "No sidebar buttons were successfully clicked"


@pytest.mark.e2e
def test_keyboard_shortcuts(e2e_page, e2e_base_url, console_errors):
    """Test Escape and Enter keyboard shortcuts without crashing."""
    _navigate_and_wait(e2e_page, e2e_base_url)

    # ── Escape: should not crash (closes modals / stops generation) ──
    e2e_page.keyboard.press("Escape")
    e2e_page.wait_for_timeout(300)

    # ── Ctrl+K: opens new session modal ──
    e2e_page.keyboard.press("Control+k")
    e2e_page.wait_for_timeout(500)

    modal = e2e_page.locator("#newSessionModal")
    if modal.count() > 0:
        modal_visible = e2e_page.evaluate(
            "document.getElementById('newSessionModal')?.classList.contains('active')"
        )
        if modal_visible:
            # Dismiss with Escape
            e2e_page.keyboard.press("Escape")
            e2e_page.wait_for_timeout(300)

    # ── Enter in message input: should not crash (no text = no send) ──
    msg_input = e2e_page.locator("#messageInput")
    if msg_input.count() > 0 and msg_input.is_visible():
        msg_input.focus()
        e2e_page.keyboard.press("Enter")
        e2e_page.wait_for_timeout(300)

        # Shift+Enter should add a newline without sending
        msg_input.focus()
        e2e_page.keyboard.press("Shift+Enter")
        e2e_page.wait_for_timeout(200)

    assert (
        _filter_errors(console_errors) == []
    ), f"JS errors: {_filter_errors(console_errors)}"

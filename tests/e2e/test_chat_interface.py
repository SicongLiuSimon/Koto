"""
E2E tests for the Koto chat interface buttons and interactions.

Validates that core chat UI controls (input, send, copy, file upload,
interrupt, rapid send) work without crashing.  The AI backend is NOT
available during these tests, so error toasts / failed responses are
expected — we only assert that the UI does not throw uncaught JS errors.
"""

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PAGE_TIMEOUT = 15_000  # ms

# Console messages that are expected when no LLM backend is configured.
BENIGN_PATTERNS = (
    "api key",
    "API key",
    "model not found",
    "Failed to fetch",
    "NetworkError",
    "AbortError",
    "Interrupt signal failed",
    "ERR_CONNECTION",
    "net::ERR_",
    "interrupt",
    "INTERRUPT",
    "Reset interrupt failed",
    "stream",
    "STREAM",
    "422",
    "500",
    "Unprocessable",
    "fetch",
)


def _is_benign(error_text: str) -> bool:
    """Return True if the console error is a known/benign backend-related message."""
    return any(p in error_text for p in BENIGN_PATTERNS)


def _filter_errors(console_errors: list[str]) -> list[str]:
    """Return only genuine, unexpected JS errors."""
    return [e for e in console_errors if not _is_benign(e)]


def _goto(page, url):
    """Navigate and return the Playwright Response."""
    return page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")


def _wait_ready(page):
    """Wait for the page to settle after navigation."""
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _fill_and_send(page, message: str = "Hello from E2E test"):
    """Type a message into the chat input and submit via Enter key."""
    textarea = page.locator("#messageInput")
    textarea.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    textarea.fill(message)
    textarea.press("Enter")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_chat_input_exists(e2e_page, console_errors, e2e_base_url):
    """/ — chat input element is visible and interactable."""
    _goto(e2e_page, f"{e2e_base_url}/")
    _wait_ready(e2e_page)

    textarea = e2e_page.locator("#messageInput")
    assert textarea.count() > 0, "Missing #messageInput textarea"
    assert textarea.is_visible(), "#messageInput is not visible"
    assert textarea.is_enabled(), "#messageInput is not enabled"

    # Verify we can type into it
    textarea.fill("typing test")
    assert textarea.input_value() == "typing test"

    assert (
        _filter_errors(console_errors) == []
    ), f"Unexpected JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_send_message_button(e2e_page, console_errors, e2e_base_url):
    """Type a message and press Enter to send. The user message should
    appear in the chat area. AI response may fail — that's expected."""
    _goto(e2e_page, f"{e2e_base_url}/")
    _wait_ready(e2e_page)

    test_msg = "Hello from E2E send test"
    _fill_and_send(e2e_page, test_msg)

    # Give the UI time to render the user message bubble
    e2e_page.wait_for_timeout(2000)

    # The user message should appear inside #chatMessages
    chat_area = e2e_page.locator("#chatMessages")
    assert chat_area.count() > 0, "Missing #chatMessages"

    user_bubble = chat_area.locator(".message.user")
    assert user_bubble.count() > 0, "User message bubble not found after send"

    # Verify the text we sent appears in one of the user bubbles
    assert (
        chat_area.locator(".message.user .message-body")
        .filter(has_text=test_msg)
        .count()
        > 0
    ), "Sent text not found in user message bubble"

    # Wait a bit for any async errors to surface
    e2e_page.wait_for_timeout(2000)

    assert (
        _filter_errors(console_errors) == []
    ), f"Unexpected JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_send_empty_message(e2e_page, console_errors, e2e_base_url):
    """Click send without typing anything — nothing should crash."""
    _goto(e2e_page, f"{e2e_base_url}/")
    _wait_ready(e2e_page)

    send_btn = e2e_page.locator("#sendBtn")
    send_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    send_btn.click()

    e2e_page.wait_for_timeout(1000)

    # Also try pressing Enter on an empty input
    textarea = e2e_page.locator("#messageInput")
    textarea.focus()
    textarea.press("Enter")

    e2e_page.wait_for_timeout(1000)

    # No user message should have been created
    user_messages = e2e_page.locator("#chatMessages .message.user")
    assert user_messages.count() == 0, "Empty message should not create a user bubble"

    assert (
        _filter_errors(console_errors) == []
    ), f"Unexpected JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_file_upload_button(e2e_page, console_errors, e2e_base_url):
    """Set a small test file on the hidden file input — no crash expected."""
    _goto(e2e_page, f"{e2e_base_url}/")
    _wait_ready(e2e_page)

    file_input = e2e_page.locator("#fileInput")
    assert file_input.count() > 0, "Missing #fileInput"

    # Playwright can set files on hidden inputs directly
    file_input.set_input_files(
        {
            "name": "test_upload.txt",
            "mimeType": "text/plain",
            "buffer": b"Hello from E2E file upload test",
        }
    )

    e2e_page.wait_for_timeout(2000)

    # File preview area should become visible (or at least not crash)
    # The file list or preview div may appear
    file_preview = e2e_page.locator("#filePreview")
    if file_preview.count() > 0:
        # It may or may not be visible depending on the UI, but no crash
        pass

    assert (
        _filter_errors(console_errors) == []
    ), f"Unexpected JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_message_copy_button(e2e_page, console_errors, e2e_base_url):
    """Send a message, then click the resend (重发) button on the user
    message bubble. Copy button only appears on assistant messages which
    won't render without a backend, so we test the resend button on the
    user bubble instead — both are .msg-action-btn elements."""
    _goto(e2e_page, f"{e2e_base_url}/")
    _wait_ready(e2e_page)

    test_msg = "Copy button test message"
    _fill_and_send(e2e_page, test_msg)

    e2e_page.wait_for_timeout(3000)

    # Try to find any .msg-action-btn on a user message (resend button)
    action_btn = e2e_page.locator(".message.user .msg-action-btn").first
    if action_btn.count() > 0 and action_btn.is_visible():
        action_btn.click()
        e2e_page.wait_for_timeout(1000)

    # Also look for copy button on assistant messages (may exist if
    # the backend returned an error message rendered as assistant bubble)
    copy_btn = e2e_page.locator(
        '.message.assistant .msg-action-btn[title="复制回复"]'
    ).first
    if copy_btn.count() > 0 and copy_btn.is_visible():
        copy_btn.click()
        e2e_page.wait_for_timeout(1000)

    assert (
        _filter_errors(console_errors) == []
    ), f"Unexpected JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_chat_interrupt_button(e2e_page, console_errors, e2e_base_url):
    """Send a message and immediately attempt to interrupt/stop generation.
    The send button toggles to stop mode (gains 'generating' class) when
    streaming.  The button may not appear if the response fails instantly,
    so handle gracefully."""
    _goto(e2e_page, f"{e2e_base_url}/")
    _wait_ready(e2e_page)

    _fill_and_send(e2e_page, "Interrupt test message")

    # The sendBtn gets class 'generating' during streaming, which makes
    # it act as a stop button.  Try clicking it quickly.
    send_btn = e2e_page.locator("#sendBtn")
    e2e_page.wait_for_timeout(500)

    # Attempt to click the stop button — it may or may not be in
    # generating mode depending on timing.
    try:
        send_btn.click(timeout=3000)
    except Exception:
        pass  # Button may be disabled or state already resolved

    e2e_page.wait_for_timeout(2000)

    assert (
        _filter_errors(console_errors) == []
    ), f"Unexpected JS errors: {_filter_errors(console_errors)}"


@pytest.mark.e2e
def test_rapid_message_send(e2e_page, console_errors, e2e_base_url):
    """Send 3 messages in rapid succession — no JS errors or page crash."""
    _goto(e2e_page, f"{e2e_base_url}/")
    _wait_ready(e2e_page)

    for i in range(3):
        textarea = e2e_page.locator("#messageInput")
        textarea.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        textarea.fill(f"Rapid message {i + 1}")
        textarea.press("Enter")
        # Small pause so the UI can process the send
        e2e_page.wait_for_timeout(800)

    # Wait for async processing to settle
    e2e_page.wait_for_timeout(4000)

    # At least one user message bubble should exist
    user_messages = e2e_page.locator("#chatMessages .message.user")
    assert user_messages.count() >= 1, "No user messages found after rapid send"

    assert (
        _filter_errors(console_errors) == []
    ), f"Unexpected JS errors: {_filter_errors(console_errors)}"

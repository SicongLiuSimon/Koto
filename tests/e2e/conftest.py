"""
E2E test fixtures for Koto UI testing with Playwright.

Starts the Flask app on a dedicated test port and provides
browser fixtures with automatic JS console error collection.
"""

import os
import signal
import subprocess
import sys
import time

import pytest
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
E2E_PORT = int(os.environ.get("KOTO_E2E_PORT", 9876))
E2E_BASE_URL = f"http://127.0.0.1:{E2E_PORT}"
APP_STARTUP_TIMEOUT = 60  # seconds
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _wait_for_server(base_url: str, timeout: int) -> bool:
    """Poll the health endpoint until the server is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/api/ping", timeout=5)
            if r.status_code == 200:
                return True
        except (requests.ConnectionError, requests.ReadTimeout, requests.Timeout):
            pass
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Session-scoped fixtures (one server per test session)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def e2e_base_url():
    """Return the base URL for the E2E test server."""
    return E2E_BASE_URL


@pytest.fixture(scope="session")
def _flask_server(e2e_base_url):
    """Start Flask in a child process, wait for readiness, then tear down."""
    env = os.environ.copy()
    env.update(
        {
            "KOTO_PORT": str(E2E_PORT),
            "KOTO_DEPLOY_MODE": "local",
            "KOTO_AUTH_ENABLED": "false",
            "FLASK_DEBUG": "false",
            "GEMINI_API_KEY": env.get("GEMINI_API_KEY", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join([REPO_ROOT, os.path.join(REPO_ROOT, "src")]),
        }
    )
    stderr_path = os.path.join(REPO_ROOT, "logs", "e2e_server.log")
    os.makedirs(os.path.dirname(stderr_path), exist_ok=True)
    stderr_file = open(stderr_path, "w", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO_ROOT, "src", "server.py")],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
    )

    if not _wait_for_server(e2e_base_url, APP_STARTUP_TIMEOUT):
        proc.terminate()
        try:
            stderr_file.close()
            err_text = open(stderr_path, encoding="utf-8", errors="replace").read()[
                -2000:
            ]
        except Exception:
            err_text = "(could not read stderr)"
        pytest.fail(
            f"Flask server did not become ready within {APP_STARTUP_TIMEOUT}s "
            f"on {e2e_base_url}\n\nServer stderr:\n{err_text}"
        )

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        stderr_file.close()


@pytest.fixture(scope="session")
def browser_context_args():
    """Playwright browser context defaults."""
    return {
        "viewport": {"width": 1280, "height": 720},
        "ignore_https_errors": True,
    }


# ---------------------------------------------------------------------------
# Known benign console errors to ignore
# ---------------------------------------------------------------------------
BENIGN_ERROR_PATTERNS = [
    "WebSocket",
    "ws://",
    "wss://",
    "net::ERR_",
    "favicon.ico",
    "API key",
    "api key",
    "Failed to load resource",
    "ERR_CONNECTION_REFUSED",
]


def _is_benign(msg: str) -> bool:
    """Return True if the console error is a known benign message."""
    return any(pat in msg for pat in BENIGN_ERROR_PATTERNS)


# ---------------------------------------------------------------------------
# Per-test fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def console_errors():
    """Collector for JS console errors. Tests should assert this list is empty."""
    return []


@pytest.fixture()
def e2e_page(page, _flask_server, e2e_base_url, console_errors):
    """
    A Playwright page wired to the running Flask server with
    automatic console-error capture (filters benign errors).

    Also auto-dismisses the setup wizard by mocking /api/setup/status
    to return initialized=true, so the modal never blocks clicks.

    Usage in tests:
        def test_something(e2e_page, console_errors, e2e_base_url):
            e2e_page.goto(f"{e2e_base_url}/")
            ...
            assert console_errors == [], f"JS errors: {console_errors}"
    """
    # Mock the setup status API so the setup wizard never appears
    page.route(
        "**/api/setup/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"initialized": true, "has_api_key": true}',
        ),
    )

    page.on(
        "console",
        lambda msg: (
            console_errors.append(msg.text)
            if msg.type == "error" and not _is_benign(msg.text)
            else None
        ),
    )
    page.on(
        "pageerror",
        lambda exc: (
            console_errors.append(str(exc)) if not _is_benign(str(exc)) else None
        ),
    )
    yield page


@pytest.fixture()
def failed_requests():
    """Collector for failed network requests (HTTP 500+)."""
    return []


@pytest.fixture()
def e2e_page_with_network(e2e_page, failed_requests):
    """
    Like e2e_page but also captures failed network requests (5xx).
    """
    e2e_page.on(
        "response",
        lambda resp: (
            failed_requests.append(f"{resp.status} {resp.url}")
            if resp.status >= 500
            else None
        ),
    )
    yield e2e_page

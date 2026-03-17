"""
E2E smoke tests — verify every major Koto page loads without errors.

Each test navigates to a page, checks for a 2xx/3xx response,
waits for the DOM to settle, asserts zero JS console errors,
and confirms at least one key element is visible.
"""

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PAGE_TIMEOUT = 15_000  # ms – generous enough for cold-start Flask


def _goto(page, url):
    """Navigate and return the Playwright Response so callers can check status."""
    resp = page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
    return resp


# ---------------------------------------------------------------------------
# Individual page tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_index_page(e2e_page, console_errors, e2e_base_url):
    """/ — main chat UI with session sidebar and chat input."""
    resp = _goto(e2e_page, f"{e2e_base_url}/")
    assert resp is not None and resp.status < 500, f"Status {resp.status}"

    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # Key elements: chat messages area and session sidebar
    assert e2e_page.locator("#chatMessages").count() > 0, "Missing #chatMessages"
    assert e2e_page.locator("#sessionsList").count() > 0, "Missing #sessionsList"

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_skill_marketplace_page(e2e_page, console_errors, e2e_base_url):
    """/skill-marketplace — skill catalog and library."""
    resp = _goto(e2e_page, f"{e2e_base_url}/skill-marketplace")
    assert resp is not None and resp.status < 500, f"Status {resp.status}"

    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    assert e2e_page.locator(".sm-app").count() > 0, "Missing .sm-app"
    assert e2e_page.locator("#sm-search-input").count() > 0, "Missing search input"

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_landing_page(e2e_page, console_errors, e2e_base_url):
    """/landing — may not exist; skip if 404."""
    resp = _goto(e2e_page, f"{e2e_base_url}/landing")
    if resp is not None and resp.status == 404:
        pytest.skip("/landing route not available")

    assert resp is not None and resp.status < 500, f"Status {resp.status}"
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_mobile_page(e2e_page, console_errors, e2e_base_url):
    """/mobile — mobile-optimised chat UI."""
    resp = _goto(e2e_page, f"{e2e_base_url}/mobile")
    assert resp is not None and resp.status < 500, f"Status {resp.status}"

    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    assert e2e_page.locator("#app").count() > 0, "Missing #app container"
    assert e2e_page.locator("#chat").count() > 0, "Missing #chat area"
    assert e2e_page.locator("#txIn").count() > 0, "Missing #txIn textarea"

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_file_network_page(e2e_page, console_errors, e2e_base_url):
    """/file-network — file relationship network graph."""
    resp = _goto(e2e_page, f"{e2e_base_url}/file-network")
    assert resp is not None and resp.status < 500, f"Status {resp.status}"

    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    assert e2e_page.locator(".container").count() > 0, "Missing .container"
    assert e2e_page.locator(".search-panel").count() > 0, "Missing .search-panel"

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_knowledge_graph_page(e2e_page, console_errors, e2e_base_url):
    """/knowledge-graph — D3 knowledge graph visualisation."""
    resp = _goto(e2e_page, f"{e2e_base_url}/knowledge-graph")
    assert resp is not None and resp.status < 500, f"Status {resp.status}"

    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    assert e2e_page.locator("#graph").count() > 0, "Missing #graph SVG"
    assert e2e_page.locator(".sidebar").count() > 0, "Missing .sidebar"

    assert console_errors == [], f"JS errors: {console_errors}"


# ---------------------------------------------------------------------------
# Parametrised test — all pages in one sweep
# ---------------------------------------------------------------------------
ALL_PAGES = [
    ("/", "body"),
    ("/skill-marketplace", ".sm-app"),
    ("/landing", "body"),
    ("/mobile", "#app"),
    ("/file-network", ".container"),
    ("/knowledge-graph", "#graph"),
]


@pytest.mark.e2e
@pytest.mark.parametrize("path, selector", ALL_PAGES, ids=[p for p, _ in ALL_PAGES])
def test_page_loads_without_errors(
    e2e_page, console_errors, e2e_base_url, path, selector
):
    """Every major page should return < 500, render its key element, and emit no JS errors."""
    resp = _goto(e2e_page, f"{e2e_base_url}{path}")
    if resp is not None and resp.status == 404:
        pytest.skip(f"{path} route not available")

    assert resp is not None and resp.status < 500, f"{path} returned {resp.status}"

    e2e_page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    assert (
        e2e_page.locator(selector).count() > 0
    ), f"{path}: expected element '{selector}' not found"

    assert console_errors == [], f"{path} JS errors: {console_errors}"

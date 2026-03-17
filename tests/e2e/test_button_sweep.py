"""
E2E "button sweep" tests — auto-discover and click ALL interactive elements
on each Koto page, capturing any errors as a safety-net regression check.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import pytest

# ---------------------------------------------------------------------------
# Selectors that cover all common interactive elements
# ---------------------------------------------------------------------------
INTERACTIVE_SELECTORS = ", ".join(
    [
        "button",
        "a[href]",
        "[onclick]",
        '[role="button"]',
        ".btn",
        'input[type="submit"]',
        'input[type="button"]',
    ]
)

# Benign console-error patterns we can safely ignore
BENIGN_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"api[_\-]?key", re.IGNORECASE),
    re.compile(r"API key", re.IGNORECASE),
    re.compile(r"ERR_CONNECTION_REFUSED", re.IGNORECASE),
    re.compile(r"Failed to load resource.*404", re.IGNORECASE),
    re.compile(r"net::ERR_", re.IGNORECASE),
    re.compile(r"favicon\.ico", re.IGNORECASE),
    re.compile(r"ResizeObserver loop", re.IGNORECASE),
    re.compile(r"model.*not found", re.IGNORECASE),
    re.compile(r"optional endpoint", re.IGNORECASE),
    re.compile(r"Permissions-Policy", re.IGNORECASE),
]

# Pages to sweep — path and human-readable label
SWEEP_PAGES: list[tuple[str, str]] = [
    ("/", "main_page"),
    ("/skill-marketplace", "skill_marketplace"),
    ("/landing", "landing_page"),
    ("/mobile", "mobile_page"),
]


def _is_benign_error(msg: str) -> bool:
    """Return True if the console error matches a known benign pattern."""
    return any(pat.search(msg) for pat in BENIGN_ERROR_PATTERNS)


def _element_descriptor(tag: str, text: str, el_id: str, classes: str) -> str:
    """Build a short human-readable descriptor for an element."""
    parts = [tag]
    if el_id:
        parts.append(f"#{el_id}")
    if classes:
        parts.append(f".{classes.split()[0]}")
    label = text.strip()[:40] if text else ""
    if label:
        parts.append(f'"{label}"')
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Core sweep helper
# ---------------------------------------------------------------------------
def sweep_page_buttons(
    page,
    url: str,
    console_errors: list[str],
    failed_requests: list[str],
) -> dict[str, Any]:
    """
    Navigate to *url*, discover every interactive element, click each one,
    and return a summary dict::

        {
            "total": int,       # elements discovered
            "clicked": int,     # elements successfully clicked
            "errors": [...],    # per-element errors (element descriptor + message)
            "skipped": [...],   # elements skipped with reason
        }

    The function is intentionally **very** defensive — a single element that
    detaches, overlaps, or triggers a JS error must never crash the sweep.
    """
    result: dict[str, Any] = {
        "total": 0,
        "clicked": 0,
        "errors": [],
        "skipped": [],
    }

    # Auto-dismiss alert / confirm / prompt dialogs
    page.on("dialog", lambda dialog: dialog.dismiss())

    # Navigate
    resp = page.goto(url, wait_until="domcontentloaded", timeout=15_000)
    if resp is None or resp.status >= 400:
        result["errors"].append(
            f"Page load failed: {url} (status={getattr(resp, 'status', 'N/A')})"
        )
        return result

    # Let async JS settle
    page.wait_for_timeout(1_000)

    base_origin = urlparse(url).netloc

    # Snapshot interactive elements
    elements = page.query_selector_all(INTERACTIVE_SELECTORS)
    result["total"] = len(elements)

    for idx, el in enumerate(elements):
        descriptor = f"element[{idx}]"
        try:
            # Re-check the element is still attached to the DOM
            tag = (el.evaluate("e => e.tagName") or "").lower()
            text = (el.evaluate("e => e.textContent") or "").strip()
            el_id = el.evaluate("e => e.id") or ""
            classes = el.evaluate("e => e.className") or ""
            if isinstance(classes, dict):
                classes = ""
            descriptor = _element_descriptor(tag, text, el_id, str(classes))
        except Exception:
            result["skipped"].append((descriptor, "detached before inspect"))
            continue

        # --- Filtering ---

        # Skip external links (different origin)
        try:
            href = el.get_attribute("href") or ""
        except Exception:
            href = ""

        if href:
            parsed = urlparse(href)
            if (
                parsed.scheme in ("http", "https")
                and parsed.netloc
                and parsed.netloc != base_origin
            ):
                result["skipped"].append((descriptor, "external link"))
                continue
            # Skip download-style links
            if any(
                href.lower().endswith(ext)
                for ext in (".pdf", ".zip", ".tar", ".gz", ".exe", ".dmg", ".msi")
            ):
                result["skipped"].append((descriptor, "file download link"))
                continue

        # Skip invisible / tiny elements
        try:
            bbox = el.bounding_box()
        except Exception:
            result["skipped"].append((descriptor, "no bounding box (detached)"))
            continue

        if bbox is None:
            result["skipped"].append((descriptor, "not visible (no bbox)"))
            continue

        if bbox["width"] < 5 or bbox["height"] < 5:
            result["skipped"].append((descriptor, "too small (<5×5)"))
            continue

        # --- Click ---
        errors_before = len(console_errors)
        try:
            el.scroll_into_view_if_needed(timeout=2_000)
            el.click(force=True, timeout=3_000)
        except Exception as exc:
            # Element may have detached, become hidden, etc.
            result["errors"].append((descriptor, f"click failed: {exc}"))
            # Still count as an attempt — continue
            _safe_navigate_back(page, url)
            continue

        result["clicked"] += 1

        # Brief pause for async effects (modals, spinners, etc.)
        page.wait_for_timeout(300)

        # Check for new console errors attributable to this click
        new_errors = console_errors[errors_before:]
        real_errors = [e for e in new_errors if not _is_benign_error(e)]
        if real_errors:
            result["errors"].append((descriptor, f"console errors: {real_errors}"))

        # Press Escape to close any modal / dropdown that may have opened
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            pass

        # If the click navigated us away, go back to the sweep URL
        _safe_navigate_back(page, url)

    return result


def _safe_navigate_back(page, expected_url: str) -> None:
    """If the page URL changed, navigate back to *expected_url*."""
    try:
        current = page.url
        if urlparse(current).path != urlparse(expected_url).path:
            page.goto(expected_url, wait_until="domcontentloaded", timeout=10_000)
            page.wait_for_timeout(500)
    except Exception:
        # Best effort — don't let navigation issues crash the sweep
        try:
            page.goto(expected_url, wait_until="domcontentloaded", timeout=10_000)
        except Exception:
            pass


def _print_sweep_report(label: str, result: dict[str, Any]) -> None:
    """Print a human-readable sweep report to stdout (captured by pytest -s)."""
    print(f"\n{'=' * 60}")
    print(f"  BUTTON SWEEP: {label}")
    print(f"{'=' * 60}")
    print(f"  Total elements found : {result['total']}")
    print(f"  Successfully clicked : {result['clicked']}")
    print(f"  Skipped              : {len(result['skipped'])}")
    print(f"  Errors               : {len(result['errors'])}")

    if result["skipped"]:
        print(f"\n  Skipped elements:")
        for desc, reason in result["skipped"]:
            print(f"    • {desc} — {reason}")

    if result["errors"]:
        print(f"\n  Errors:")
        for item in result["errors"]:
            if isinstance(item, tuple):
                desc, msg = item
                print(f"    ✖ {desc} — {msg}")
            else:
                print(f"    ✖ {item}")

    print(f"{'=' * 60}\n")


def _assert_no_uncaught_exceptions(
    result: dict[str, Any],
    console_errors: list[str],
) -> None:
    """Assert that no *real* uncaught JS exceptions occurred."""
    uncaught = [e for e in console_errors if not _is_benign_error(e)]
    # Filter further: only treat "Uncaught" / "TypeError" / "ReferenceError" as fatal
    fatal_patterns = re.compile(
        r"Uncaught|TypeError|ReferenceError|SyntaxError",
        re.IGNORECASE,
    )
    fatal = [e for e in uncaught if fatal_patterns.search(e)]
    assert fatal == [], f"Uncaught JS exceptions detected during sweep:\n" + "\n".join(
        f"  • {e}" for e in fatal
    )


# ===================================================================
# Individual page sweep tests
# ===================================================================


@pytest.mark.e2e
class TestButtonSweepMainPage:
    """Sweep all interactive elements on the main page (/)."""

    def test_sweep_main_page(
        self,
        e2e_page_with_network,
        e2e_base_url,
        console_errors,
        failed_requests,
    ):
        url = f"{e2e_base_url}/"
        result = sweep_page_buttons(
            e2e_page_with_network, url, console_errors, failed_requests
        )

        _print_sweep_report("Main Page (/)", result)

        assert result["total"] > 0, "No interactive elements found on /"
        _assert_no_uncaught_exceptions(result, console_errors)


@pytest.mark.e2e
class TestButtonSweepSkillMarketplace:
    """Sweep all interactive elements on /skill-marketplace."""

    def test_sweep_skill_marketplace(
        self,
        e2e_page_with_network,
        e2e_base_url,
        console_errors,
        failed_requests,
    ):
        url = f"{e2e_base_url}/skill-marketplace"
        result = sweep_page_buttons(
            e2e_page_with_network, url, console_errors, failed_requests
        )

        _print_sweep_report("Skill Marketplace (/skill-marketplace)", result)

        assert (
            result["total"] > 0
        ), "No interactive elements found on /skill-marketplace"
        _assert_no_uncaught_exceptions(result, console_errors)


@pytest.mark.e2e
class TestButtonSweepLandingPage:
    """Sweep all interactive elements on /landing."""

    def test_sweep_landing_page(
        self,
        e2e_page_with_network,
        e2e_base_url,
        console_errors,
        failed_requests,
    ):
        url = f"{e2e_base_url}/landing"
        result = sweep_page_buttons(
            e2e_page_with_network, url, console_errors, failed_requests
        )

        _print_sweep_report("Landing Page (/landing)", result)

        # /landing may not exist as a standalone route in local mode — if so,
        # the page-load failure is already captured in result["errors"].
        if result["total"] > 0:
            _assert_no_uncaught_exceptions(result, console_errors)


# ===================================================================
# Parametrised summary sweep across all pages
# ===================================================================


@pytest.mark.e2e
@pytest.mark.parametrize(
    "path, label",
    SWEEP_PAGES,
    ids=[label for _, label in SWEEP_PAGES],
)
def test_sweep_summary(
    path: str,
    label: str,
    e2e_page_with_network,
    e2e_base_url,
    console_errors,
    failed_requests,
):
    """Parametrised button sweep across every Koto page with summary report."""
    url = f"{e2e_base_url}{path}"
    result = sweep_page_buttons(
        e2e_page_with_network, url, console_errors, failed_requests
    )

    _print_sweep_report(f"Summary — {label} ({path})", result)

    # Lenient assertion: page load failures are OK for routes that may not
    # exist in every deployment mode; we only fail on JS exceptions.
    if result["total"] > 0:
        _assert_no_uncaught_exceptions(result, console_errors)

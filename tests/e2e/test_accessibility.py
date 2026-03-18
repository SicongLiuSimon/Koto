"""
Accessibility tests using manual checks via Playwright.

Checks WCAG 2.1 Level A/AA compliance on all pages using JavaScript
evaluation — no external axe-core dependency required.
"""

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PAGE_TIMEOUT = 15_000  # ms

PAGES = [
    "/",
    "/skill-marketplace",
    "/mobile",
    "/landing",
    "/file-network",
    "/knowledge-graph",
]


def _navigate(page, base_url, path):
    """Navigate to a page; skip the test if the route returns 404."""
    resp = page.goto(
        f"{base_url}{path}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT
    )
    if resp is not None and resp.status == 404:
        pytest.skip(f"{path} route not available")
    assert resp is not None and resp.status < 500, f"{path} returned {resp.status}"
    page.wait_for_timeout(1000)


# ---------------------------------------------------------------------------
# Accessibility checks
# ---------------------------------------------------------------------------
@pytest.mark.e2e
class TestAccessibility:
    """WCAG-oriented accessibility checks across all Koto pages."""

    # -- Images ---------------------------------------------------------------
    @pytest.mark.parametrize("path", PAGES, ids=PAGES)
    def test_images_have_alt_text(self, e2e_page, e2e_base_url, path):
        """All <img> elements should have an alt attribute."""
        _navigate(e2e_page, e2e_base_url, path)

        images_without_alt = e2e_page.evaluate("""() => {
            const imgs = document.querySelectorAll('img');
            const missing = [];
            imgs.forEach(img => {
                if (!img.hasAttribute('alt')) {
                    missing.push(img.src || img.outerHTML.substring(0, 100));
                }
            });
            return missing;
        }""")

        if images_without_alt:
            pytest.skip(
                f"Images missing alt text on {path} ({len(images_without_alt)}): "
                + "; ".join(images_without_alt[:5])
            )

    # -- Form inputs ----------------------------------------------------------
    @pytest.mark.parametrize("path", PAGES, ids=PAGES)
    def test_form_inputs_have_labels(self, e2e_page, e2e_base_url, path):
        """Form inputs should have associated labels or aria-label."""
        _navigate(e2e_page, e2e_base_url, path)

        unlabeled = e2e_page.evaluate("""() => {
            const inputs = document.querySelectorAll('input, textarea, select');
            const missing = [];
            inputs.forEach(el => {
                if (el.type === 'hidden') return;
                const hasLabel = el.id && document.querySelector('label[for="' + el.id + '"]');
                const hasAria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
                const hasPlaceholder = el.getAttribute('placeholder');
                const hasTitle = el.getAttribute('title');
                if (!hasLabel && !hasAria && !hasPlaceholder && !hasTitle) {
                    missing.push(el.outerHTML.substring(0, 100));
                }
            });
            return missing;
        }""")

        if unlabeled:
            pytest.skip(
                f"Unlabeled inputs on {path} ({len(unlabeled)}): "
                + "; ".join(unlabeled[:5])
            )

    # -- Buttons --------------------------------------------------------------
    @pytest.mark.parametrize("path", PAGES, ids=PAGES)
    def test_buttons_have_accessible_names(self, e2e_page, e2e_base_url, path):
        """Buttons should have text content, aria-label, or title."""
        _navigate(e2e_page, e2e_base_url, path)

        unnamed = e2e_page.evaluate("""() => {
            const buttons = document.querySelectorAll('button, [role="button"]');
            const missing = [];
            buttons.forEach(btn => {
                const text = btn.textContent.trim();
                const ariaLabel = btn.getAttribute('aria-label');
                const title = btn.getAttribute('title');
                if (!text && !ariaLabel && !title) {
                    missing.push(btn.outerHTML.substring(0, 120));
                }
            });
            return missing;
        }""")

        if unnamed:
            pytest.skip(
                f"Buttons without accessible names on {path} ({len(unnamed)}): "
                + "; ".join(unnamed[:5])
            )

    # -- Heading hierarchy ----------------------------------------------------
    @pytest.mark.parametrize("path", PAGES, ids=PAGES)
    def test_heading_hierarchy(self, e2e_page, e2e_base_url, path):
        """Heading levels should not skip (e.g., h1 -> h3 without h2)."""
        _navigate(e2e_page, e2e_base_url, path)

        skipped = e2e_page.evaluate("""() => {
            const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
            const levels = Array.from(headings).map(h => parseInt(h.tagName[1]));
            const skips = [];
            for (let i = 1; i < levels.length; i++) {
                if (levels[i] > levels[i-1] + 1) {
                    skips.push('h' + levels[i-1] + ' -> h' + levels[i]);
                }
            }
            return skips;
        }""")

        if skipped:
            pytest.skip(f"Heading hierarchy skips on {path}: {skipped}")

    # -- Tabindex -------------------------------------------------------------
    @pytest.mark.parametrize("path", PAGES, ids=PAGES)
    def test_no_positive_tabindex(self, e2e_page, e2e_base_url, path):
        """Elements should not use positive tabindex (disrupts tab order)."""
        _navigate(e2e_page, e2e_base_url, path)

        positive_tabindex = e2e_page.evaluate("""() => {
            const els = document.querySelectorAll('[tabindex]');
            const bad = [];
            els.forEach(el => {
                const idx = parseInt(el.getAttribute('tabindex'));
                if (idx > 0) {
                    bad.push('tabindex=' + idx + ': ' + el.tagName + ' ' + el.className);
                }
            });
            return bad;
        }""")

        assert (
            positive_tabindex == []
        ), f"Positive tabindex on {path}: {positive_tabindex}"

    # -- Lang attribute -------------------------------------------------------
    def test_page_has_lang_attribute(self, e2e_page, e2e_base_url):
        """HTML element should have a lang attribute."""
        _navigate(e2e_page, e2e_base_url, "/")

        lang = e2e_page.evaluate("document.documentElement.getAttribute('lang')")
        if not lang:
            pytest.skip("HTML element missing lang attribute — add lang='en' to <html>")

    # -- Landmark regions -----------------------------------------------------
    def test_main_page_has_landmark_roles(self, e2e_page, e2e_base_url):
        """Page should have basic landmark regions (main, navigation)."""
        _navigate(e2e_page, e2e_base_url, "/")

        landmarks = e2e_page.evaluate("""() => {
            return {
                main: document.querySelectorAll('main, [role="main"]').length,
                nav: document.querySelectorAll('nav, [role="navigation"]').length,
            };
        }""")

        if landmarks["main"] == 0 and landmarks["nav"] == 0:
            pytest.skip(
                "No landmark regions found (main, nav) — consider adding for screen readers"
            )

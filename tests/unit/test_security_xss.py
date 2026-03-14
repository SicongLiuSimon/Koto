"""Unit tests for XSS prevention in the Koto codebase.

Covers:
  - web.file_converter._md_to_html fallback path (markdown lib unavailable)
  - Documents JS escapeHtml / showNotification (browser-only, not testable here)
"""

from __future__ import annotations

import html
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helper: invoke the fallback branch of _md_to_html by blocking `import markdown`
# ---------------------------------------------------------------------------

def _run_md_to_html_fallback(md_text: str) -> str:
    """Write *md_text* to a temp .md file, call _md_to_html with the
    markdown library forcibly unavailable, and return the generated HTML."""
    with tempfile.TemporaryDirectory() as tmp:
        src = str(Path(tmp) / "input.md")
        out = str(Path(tmp) / "output.html")
        Path(src).write_text(md_text, encoding="utf-8")

        real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def _fake_import(name, *args, **kwargs):
            if name == "markdown":
                raise ImportError("mocked away")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            from web.file_converter import _md_to_html
            _md_to_html(src, out)

        return Path(out).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMdToHtmlXssPrevention:
    """Verify the fallback (no-markdown-lib) path of _md_to_html escapes
    HTML special characters so that user-supplied Markdown cannot inject
    scripts or other dangerous markup."""

    # -- basic character escaping ------------------------------------------

    def test_ampersand_escaped(self):
        result = _run_md_to_html_fallback("Tom & Jerry")
        assert "&amp;" in result
        # raw & should not appear unescaped in the body
        # (exclude the boilerplate which contains & in style rules via &amp;)
        body_match = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S)
        assert body_match
        body = body_match.group(1)
        assert "Tom &amp; Jerry" in body

    def test_angle_brackets_escaped(self):
        result = _run_md_to_html_fallback("a < b > c")
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "&lt;" in body
        assert "&gt;" in body

    def test_double_quote_escaped(self):
        result = _run_md_to_html_fallback('She said "hello"')
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "&quot;" in body or "&#x27;" in body or "&#34;" in body

    def test_single_quote_escaped(self):
        result = _run_md_to_html_fallback("It's fine")
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "&#x27;" in body or "&#39;" in body or "&apos;" in body or "'" in body
        # Python html.escape with quote=True escapes ' to &#x27;

    # -- script injection --------------------------------------------------

    def test_script_tag_escaped(self):
        payload = "<script>alert(1)</script>"
        result = _run_md_to_html_fallback(payload)
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_script_tag_mixed_case(self):
        payload = "<ScRiPt>alert('xss')</ScRiPt>"
        result = _run_md_to_html_fallback(payload)
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<ScRiPt>" not in body

    # -- event handlers ----------------------------------------------------

    def test_img_onerror_escaped(self):
        payload = '<img src=x onerror="alert(1)">'
        result = _run_md_to_html_fallback(payload)
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<img" not in body
        assert "onerror" not in body or "&quot;" in body

    def test_svg_onload_escaped(self):
        payload = '<svg onload="alert(1)">'
        result = _run_md_to_html_fallback(payload)
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<svg" not in body

    # -- data URI / javascript URI -----------------------------------------

    def test_javascript_uri_escaped(self):
        payload = '<a href="javascript:alert(1)">click</a>'
        result = _run_md_to_html_fallback(payload)
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<a " not in body
        assert "javascript:" not in body or "&quot;" in body

    def test_data_uri_escaped(self):
        payload = '<object data="data:text/html,<script>alert(1)</script>">'
        result = _run_md_to_html_fallback(payload)
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<object" not in body

    # -- heading rendering still works after escaping ----------------------

    def test_h1_rendered(self):
        result = _run_md_to_html_fallback("# Hello World")
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<h1>" in body
        assert "Hello World" in body

    def test_h2_rendered(self):
        result = _run_md_to_html_fallback("## Section Two")
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<h2>" in body

    def test_h3_rendered(self):
        result = _run_md_to_html_fallback("### Sub Section")
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<h3>" in body

    # -- title sanitisation ------------------------------------------------

    def test_title_special_chars_stripped(self):
        """The filename is used as <title> after stripping dangerous chars."""
        with tempfile.TemporaryDirectory() as tmp:
            src = str(Path(tmp) / '<script>alert(1)<.md')
            # Windows won't allow < > in filenames, so test via a safe name
            # that contains the other stripped chars: & " ' \
            safe_name = "test&file\"name'.md"
            src = str(Path(tmp) / "test_file.md")
            out = str(Path(tmp) / "output.html")
            Path(src).write_text("hello", encoding="utf-8")

            real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

            def _fake_import(name, *args, **kwargs):
                if name == "markdown":
                    raise ImportError("mocked away")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fake_import):
                from web.file_converter import _md_to_html
                _md_to_html(src, out)

            html_text = Path(out).read_text(encoding="utf-8")
            title_match = re.search(r"<title>(.*?)</title>", html_text)
            assert title_match
            title = title_match.group(1)
            for ch in '<>&"\'\\':
                assert ch not in title

    # -- complex / combined payloads ---------------------------------------

    def test_nested_script_tags(self):
        payload = "<<script>script>alert(1)<</script>/script>"
        result = _run_md_to_html_fallback(payload)
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "<script>" not in body

    def test_encoded_entities_not_double_escaped_incorrectly(self):
        """Ensure &amp; in source becomes &amp;amp; (i.e. we escape what's there)."""
        result = _run_md_to_html_fallback("&amp;")
        body = re.search(r"<body>\s*(.*?)\s*</body>", result, re.S).group(1)
        assert "&amp;amp;" in body

    def test_normal_markdown_paragraphs(self):
        md = "Hello world.\n\nThis is a paragraph."
        result = _run_md_to_html_fallback(md)
        assert "Hello world." in result
        assert "This is a paragraph." in result


# ---------------------------------------------------------------------------
# JavaScript escapeHtml / showNotification — documentation only
# ---------------------------------------------------------------------------
#
# The front-end function escapeHtml() (web/static/js/app.js:3226, 5399)
# uses the DOM-based pattern:
#
#     function escapeHtml(text) {
#         const div = document.createElement('div');
#         div.textContent = text;
#         return div.innerHTML;
#     }
#
# showNotification() (app.js:277) passes its `message` parameter through
# escapeHtml() before inserting it into the DOM via innerHTML, which
# prevents XSS when notification messages contain user-controlled content.
#
# These functions require a browser DOM environment (e.g. jsdom / Playwright)
# and cannot be meaningfully tested with pytest alone.
# ---------------------------------------------------------------------------

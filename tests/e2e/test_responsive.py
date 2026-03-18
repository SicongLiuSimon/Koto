"""
Mobile and tablet responsive tests.
Verify key pages render correctly at different viewport sizes.
"""

import pytest

VIEWPORTS = {
    "phone": {"width": 375, "height": 667},  # iPhone SE
    "phone_lg": {"width": 414, "height": 896},  # iPhone 11
    "tablet": {"width": 768, "height": 1024},  # iPad
    "tablet_landscape": {"width": 1024, "height": 768},
}

PAGES = ["/", "/skill-marketplace", "/mobile"]

PAGE_TIMEOUT = 15_000  # ms


@pytest.mark.e2e
class TestResponsive:
    """Test pages render at mobile/tablet viewports without errors."""

    @pytest.mark.parametrize(
        "vp_name,viewport",
        VIEWPORTS.items(),
        ids=list(VIEWPORTS.keys()),
    )
    @pytest.mark.parametrize("path", PAGES)
    def test_page_loads_at_viewport(
        self, e2e_page, console_errors, e2e_base_url, path, vp_name, viewport
    ):
        e2e_page.set_viewport_size(viewport)
        resp = e2e_page.goto(
            f"{e2e_base_url}{path}",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        # Page should load successfully
        assert (
            resp is not None and resp.status < 500
        ), f"{path} at {vp_name}: HTTP {resp.status}"

        # Let the page settle
        e2e_page.wait_for_timeout(500)

        # No horizontal overflow (page wider than viewport = broken layout)
        body_width = e2e_page.evaluate("document.body.scrollWidth")
        viewport_width = viewport["width"]
        # Allow 5px tolerance for scrollbars
        assert (
            body_width <= viewport_width + 5
        ), f"{path} at {vp_name}: body width {body_width}px exceeds viewport {viewport_width}px"

        # No JS errors
        assert console_errors == [], f"{path} at {vp_name} JS errors: {console_errors}"

    @pytest.mark.parametrize(
        "vp_name,viewport",
        [("phone", VIEWPORTS["phone"])],
        ids=["phone"],
    )
    def test_main_page_elements_visible_mobile(
        self, e2e_page, e2e_base_url, vp_name, viewport
    ):
        """Key elements on main page should still be visible at phone size."""
        e2e_page.set_viewport_size(viewport)
        e2e_page.goto(
            f"{e2e_base_url}/",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        e2e_page.wait_for_timeout(1000)

        # Chat input should be visible (the core feature)
        chat_input = e2e_page.locator("#chatInput, textarea[placeholder], .chat-input")
        if chat_input.count() == 0:
            pytest.skip("No chat input element found on main page")

        box = chat_input.first.bounding_box()
        if box is None:
            pytest.skip("Chat input not visible (no bounding box)")

        assert box["width"] > 50, "Chat input too narrow on mobile"

    @pytest.mark.parametrize(
        "vp_name,viewport",
        [("phone", VIEWPORTS["phone"])],
        ids=["phone"],
    )
    def test_buttons_not_clipped_mobile(
        self, e2e_page, e2e_base_url, vp_name, viewport
    ):
        """Buttons shouldn't be clipped off-screen at phone viewport."""
        e2e_page.set_viewport_size(viewport)
        e2e_page.goto(
            f"{e2e_base_url}/",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        e2e_page.wait_for_timeout(1000)

        buttons = e2e_page.locator("button:visible")
        btn_count = buttons.count()
        if btn_count == 0:
            pytest.skip("No visible buttons found on main page")

        clipped = []
        for i in range(min(btn_count, 20)):
            btn = buttons.nth(i)
            box = btn.bounding_box()
            if box and (box["x"] + box["width"] > viewport["width"] + 10):
                text = (
                    btn.text_content() or btn.get_attribute("title") or f"button[{i}]"
                )
                clipped.append(
                    f"{text.strip()} (x={box['x']:.0f}, w={box['width']:.0f})"
                )

        assert clipped == [], f"Buttons clipped off-screen: {clipped}"

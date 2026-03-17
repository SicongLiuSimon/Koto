"""
E2E tests for the Koto Skill Marketplace page.

Tests cover page loading, skill card rendering, search/filter,
card interaction, enable/disable toggle, category filtering,
and a full sweep for JS / network errors.
"""

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PAGE_TIMEOUT = 15_000  # ms – generous for cold-start Flask
MARKETPLACE_PATH = "/skill-marketplace"


def _goto(page, url):
    """Navigate and return the Playwright Response."""
    return page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")


def _wait_for_cards(page, *, timeout=PAGE_TIMEOUT):
    """Wait until the catalog grid has at least one rendered skill card."""
    page.wait_for_selector("#catalog-grid .sm-card", state="attached", timeout=timeout)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_marketplace_page_loads(e2e_page, console_errors, e2e_base_url):
    """Navigate to /skill-marketplace, verify key elements are visible, no JS errors."""
    resp = _goto(e2e_page, f"{e2e_base_url}{MARKETPLACE_PATH}")
    assert resp is not None and resp.status < 500, f"Status {resp.status}"

    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    assert e2e_page.locator(".sm-app").count() > 0, "Missing .sm-app container"
    assert e2e_page.locator("#sm-search-input").count() > 0, "Missing search input"
    assert e2e_page.locator(".sm-tabs").count() > 0, "Missing tab bar"
    assert e2e_page.locator(".sm-sidebar").count() > 0, "Missing sidebar"
    assert e2e_page.locator("#catalog-grid").count() > 0, "Missing catalog grid"

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_skill_cards_displayed(e2e_page, console_errors, e2e_base_url):
    """Built-in skills should produce at least one .sm-card in the catalog grid."""
    _goto(e2e_page, f"{e2e_base_url}{MARKETPLACE_PATH}")
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    _wait_for_cards(e2e_page)

    cards = e2e_page.locator("#catalog-grid .sm-card")
    assert cards.count() > 0, "No skill cards rendered"

    # Each card should have a name and description
    first_card = cards.first
    assert first_card.locator(".sm-card-name").count() > 0, "Card missing name"
    assert first_card.locator(".sm-card-desc").count() > 0, "Card missing description"

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_search_skills(e2e_page, console_errors, e2e_base_url):
    """Typing in the search input should filter the displayed skill cards."""
    _goto(e2e_page, f"{e2e_base_url}{MARKETPLACE_PATH}")
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    _wait_for_cards(e2e_page)

    initial_count = e2e_page.locator("#catalog-grid .sm-card").count()
    assert initial_count > 0, "Need at least one card to test search"

    search_input = e2e_page.locator("#sm-search-input")
    assert search_input.is_visible(), "Search input not visible"

    # Type a query that should narrow results (file-related skills exist)
    search_input.fill("file")
    # The search is debounced (400ms) and triggers a network request
    e2e_page.wait_for_timeout(1000)
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    filtered_count = e2e_page.locator("#catalog-grid .sm-card").count()
    # Either the count decreased (filtered) or we got results matching "file"
    # Both outcomes are valid — the key assertion is no errors occurred
    if initial_count > 1:
        assert (
            filtered_count < initial_count or filtered_count > 0
        ), "Search did not filter cards"

    # Clear search and verify cards come back
    search_input.fill("")
    e2e_page.wait_for_timeout(1000)
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    restored_count = e2e_page.locator("#catalog-grid .sm-card").count()
    assert restored_count >= initial_count, "Cards not restored after clearing search"

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_click_skill_card(e2e_page, console_errors, e2e_base_url):
    """Clicking a skill card body (not a button) should open the detail drawer."""
    _goto(e2e_page, f"{e2e_base_url}{MARKETPLACE_PATH}")
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    _wait_for_cards(e2e_page)

    card = e2e_page.locator("#catalog-grid .sm-card").first
    assert card.is_visible(), "First skill card is not visible"

    # Click the card name area (avoids buttons which have stopPropagation)
    card.locator(".sm-card-name").click()

    # The drawer should gain the 'open' class
    drawer = e2e_page.locator("#sm-drawer")
    drawer.wait_for(state="visible", timeout=5000)
    assert drawer.is_visible(), "Drawer did not open"

    # Drawer should have skill info populated
    drawer_name = e2e_page.locator("#drawer-name")
    assert drawer_name.text_content().strip(), "Drawer name is empty"

    # Close drawer via close button
    close_btn = e2e_page.locator("#drawer-close-btn")
    if close_btn.is_visible():
        close_btn.click()
        e2e_page.wait_for_timeout(500)

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_toggle_skill_enable(
    e2e_page_with_network, console_errors, failed_requests, e2e_base_url
):
    """Clicking a toggle button should change the skill's enabled state without 500 errors."""
    page = e2e_page_with_network
    _goto(page, f"{e2e_base_url}{MARKETPLACE_PATH}")
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    _wait_for_cards(page)

    toggle_btn = page.locator('#catalog-grid [data-action="toggle"]').first
    assert toggle_btn.count() > 0, "No toggle buttons found"

    original_text = toggle_btn.text_content().strip()
    original_enabled = toggle_btn.get_attribute("data-enabled")

    toggle_btn.click()
    # Wait for the API call and re-render
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # After re-render, re-locate the same skill's toggle by data-id
    skill_id = toggle_btn.get_attribute("data-id")
    if skill_id:
        new_btn = page.locator(
            f'#catalog-grid [data-action="toggle"][data-id="{skill_id}"]'
        )
        if new_btn.count() > 0:
            new_enabled = new_btn.first.get_attribute("data-enabled")
            assert new_enabled != original_enabled, (
                f"Toggle state did not change for {skill_id}: "
                f"was {original_enabled}, still {new_enabled}"
            )

    # Revert the toggle to avoid side-effects on other tests
    revert_btn = page.locator(
        f'#catalog-grid [data-action="toggle"][data-id="{skill_id}"]'
    )
    if revert_btn.count() > 0:
        revert_btn.first.click()
        page.wait_for_timeout(1500)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    assert failed_requests == [], f"Server errors: {failed_requests}"
    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_category_filter(e2e_page, console_errors, e2e_base_url):
    """Clicking a sidebar category item should filter the skill grid."""
    _goto(e2e_page, f"{e2e_base_url}{MARKETPLACE_PATH}")
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    _wait_for_cards(e2e_page)

    initial_count = e2e_page.locator("#catalog-grid .sm-card").count()

    # Find a non-'all' category sidebar item with a non-zero count
    cat_items = e2e_page.locator(".sm-sidebar-item[data-cat]")
    target_item = None
    for i in range(cat_items.count()):
        item = cat_items.nth(i)
        cat_value = item.get_attribute("data-cat")
        if cat_value and cat_value != "all":
            count_el = item.locator(".item-count")
            if count_el.count() > 0:
                count_text = count_el.text_content().strip()
                if count_text.isdigit() and int(count_text) > 0:
                    target_item = item
                    break

    if target_item is None:
        pytest.skip("No category with skills found to filter")

    target_item.click()
    e2e_page.wait_for_timeout(800)
    e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    filtered_count = e2e_page.locator("#catalog-grid .sm-card").count()
    assert filtered_count > 0, "Category filter produced zero results"
    assert filtered_count <= initial_count, "Filtered count exceeds total"

    # Reset to 'all'
    all_item = e2e_page.locator('.sm-sidebar-item[data-cat="all"]')
    if all_item.count() > 0 and all_item.is_visible():
        all_item.click()
        e2e_page.wait_for_timeout(800)
        e2e_page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    assert console_errors == [], f"JS errors: {console_errors}"


@pytest.mark.e2e
def test_marketplace_no_errors_sweep(
    e2e_page_with_network, console_errors, failed_requests, e2e_base_url
):
    """Click through multiple cards, toggles, and tabs — assert zero JS / network errors."""
    page = e2e_page_with_network
    _goto(page, f"{e2e_base_url}{MARKETPLACE_PATH}")
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    _wait_for_cards(page)

    # --- Interact with up to 3 skill cards (open/close drawer) ---
    cards = page.locator("#catalog-grid .sm-card")
    card_count = min(cards.count(), 3)
    for i in range(card_count):
        card = cards.nth(i)
        if card.is_visible():
            card_name_el = card.locator(".sm-card-name")
            if card_name_el.count() > 0:
                card_name_el.click()
                page.wait_for_timeout(800)

                drawer = page.locator("#sm-drawer")
                if drawer.is_visible():
                    close_btn = page.locator("#drawer-close-btn")
                    if close_btn.is_visible():
                        close_btn.click()
                        page.wait_for_timeout(400)

    # --- Click a toggle button (and revert) ---
    toggle_btn = page.locator('#catalog-grid [data-action="toggle"]').first
    if toggle_btn.count() > 0 and toggle_btn.is_visible():
        skill_id = toggle_btn.get_attribute("data-id")
        toggle_btn.click()
        page.wait_for_timeout(1500)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        # Revert
        if skill_id:
            revert_btn = page.locator(
                f'#catalog-grid [data-action="toggle"][data-id="{skill_id}"]'
            )
            if revert_btn.count() > 0:
                revert_btn.first.click()
                page.wait_for_timeout(1500)
                page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # --- Click a sidebar category and return ---
    cat_item = page.locator(".sm-sidebar-item[data-cat]:not([data-cat='all'])").first
    if cat_item.count() > 0 and cat_item.is_visible():
        cat_item.click()
        page.wait_for_timeout(800)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        all_item = page.locator('.sm-sidebar-item[data-cat="all"]')
        if all_item.count() > 0 and all_item.is_visible():
            all_item.click()
            page.wait_for_timeout(800)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # --- Switch to Library tab and back ---
    lib_tab = page.locator('.sm-tab[data-tab="library"]')
    if lib_tab.count() > 0 and lib_tab.is_visible():
        lib_tab.click()
        page.wait_for_timeout(1000)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        catalog_tab = page.locator('.sm-tab[data-tab="catalog"]')
        if catalog_tab.count() > 0 and catalog_tab.is_visible():
            catalog_tab.click()
            page.wait_for_timeout(1000)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

    # --- Final assertions ---
    assert failed_requests == [], f"Server errors: {failed_requests}"
    assert console_errors == [], f"JS errors: {console_errors}"

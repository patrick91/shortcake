import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import open_diff_switcher, select_diff_option

pytestmark = pytest.mark.e2e


def test_branches_display(ui_page: Page):
    """Both branch_a and branch_b are visible in the diff switcher."""
    open_diff_switcher(ui_page)
    listbox = ui_page.locator("#sc-diff-listbox")
    expect(listbox.get_by_text("branch_a")).to_be_visible()
    expect(listbox.get_by_text("branch_b")).to_be_visible()


def test_current_branch_badge(ui_page: Page):
    """branch_b (current branch) has the 'current' badge."""
    open_diff_switcher(ui_page)
    branch_b_btn = (
        ui_page.locator("#sc-diff-listbox [role='option']")
        .filter(has_text="branch_b")
        .first
    )
    expect(branch_b_btn.get_by_text("current")).to_be_visible()


def test_working_changes_button(ui_page: Page):
    """Working Changes option is always visible in the diff switcher."""
    open_diff_switcher(ui_page)
    expect(ui_page.get_by_text("Working Changes")).to_be_visible()


def test_diff_switcher_filters_and_selection_persists(page: Page, ui_url: str):
    """The diff switcher filters branches and preserves selection in the URL."""
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(ui_url)
    open_diff_switcher(page)

    search = page.get_by_role("combobox")
    search.fill("branch_a")
    listbox = page.locator("#sc-diff-listbox")
    expect(listbox.get_by_text("branch_a")).to_be_visible()
    expect(listbox.get_by_text("branch_b")).not_to_be_visible()

    listbox.locator("[role='option']").filter(has_text="branch_a").click()
    page.wait_for_selector(".diff-content", timeout=10_000)
    expect(page.locator("header")).to_contain_text("branch_a")
    expect(page.locator("header")).to_contain_text("main")
    assert page.url.endswith("#/branch/branch_a")

    page.reload()
    expect(page.locator("header")).to_contain_text("branch_a")
    expect(page.locator("header")).to_contain_text("main")


def test_branch_selection_highlights(ui_page: Page):
    """Clicking a branch highlights it (active bg) and updates the diff header."""
    select_diff_option(ui_page, "branch_a")

    open_diff_switcher(ui_page)
    branch_a_btn = (
        ui_page.locator("#sc-diff-listbox [role='option']")
        .filter(has_text="branch_a")
        .first
    )
    expect(branch_a_btn).to_have_class(re.compile(r"bg-accent-bg"))

    # The diff header should update to show branch_a -> main
    header = ui_page.locator("header")
    expect(header).to_contain_text("branch_a")
    expect(header).to_contain_text("main")


def test_default_selection(page: Page, ui_url: str):
    """On load, working changes is auto-selected by default."""
    page.goto(ui_url)
    expect(page.locator("header")).to_contain_text("Uncommitted changes")

    open_diff_switcher(page)
    working_btn = (
        page.locator("#sc-diff-listbox [role='option']")
        .filter(has_text="Working Changes")
        .first
    )
    expect(working_btn).to_have_class(re.compile(r"bg-accent-bg"))

    # The diff header should show "Uncommitted changes"
    header = page.locator("header")
    expect(header).to_contain_text("Uncommitted changes")


def test_no_branches_message(page: Page, ui_url: str):
    """With no tracked branches, shows the empty state message."""
    # Intercept the stack API to return empty branches
    page.route(
        "**/api/stack",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"currentBranch": "main", "branches": []}',
        ),
    )
    page.goto(ui_url)
    open_diff_switcher(page)
    expect(page.get_by_text("No tracked branches found")).to_be_visible(timeout=10_000)

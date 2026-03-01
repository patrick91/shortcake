import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_branches_display(ui_page: Page):
    """Both branch_a and branch_b are visible in the sidebar."""
    sidebar = ui_page.locator("section").first
    expect(sidebar.locator("button", has_text="branch_a")).to_be_visible()
    expect(sidebar.locator("button", has_text="branch_b")).to_be_visible()


def test_current_branch_badge(ui_page: Page):
    """branch_b (current branch) has the 'current' badge."""
    branch_b_btn = ui_page.locator("button", has_text="branch_b").first
    expect(branch_b_btn.get_by_text("current")).to_be_visible()


def test_working_changes_button(ui_page: Page):
    """Working Changes button is always visible in the sidebar."""
    expect(ui_page.get_by_text("Working Changes")).to_be_visible()


def test_branch_selection_highlights(ui_page: Page):
    """Clicking a branch highlights it (active bg) and updates the diff header."""
    branch_a_btn = ui_page.locator("button", has_text="branch_a").first
    branch_a_btn.click()

    # The clicked branch should receive the active background
    expect(branch_a_btn).to_have_class(re.compile(r"bg-accent-bg"))

    # The diff header should update to show branch_a -> main
    header = ui_page.locator("header h2")
    expect(header).to_contain_text("branch_a")
    expect(header).to_contain_text("main")


def test_default_selection(ui_page: Page):
    """On load, the current branch (branch_b) is auto-selected."""
    # branch_b button should have active styling
    branch_b_btn = ui_page.locator("button", has_text="branch_b").first
    expect(branch_b_btn).to_have_class(re.compile(r"bg-accent-bg"))

    # The diff header should show branch_b -> branch_a
    header = ui_page.locator("header h2")
    expect(header).to_contain_text("branch_b")
    expect(header).to_contain_text("branch_a")


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
    expect(page.get_by_text("No tracked branches found")).to_be_visible(timeout=10_000)

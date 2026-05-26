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


def test_stack_sidebar_toggle_persists(page: Page, ui_url: str):
    """Desktop stack sidebar can be hidden, restored, and persisted across reloads."""
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(ui_url)
    page.wait_for_selector("text=branch_a", timeout=15_000)
    page.locator("button", has_text="branch_b").first.click()
    page.wait_for_selector(".diff-content", timeout=10_000)

    sidebar = page.locator("#stack-sidebar")
    expect(sidebar.get_by_text("branch_a")).to_be_visible()

    before = page.evaluate(
        """
        () => ({
          sidebarWidth: document.querySelector('#stack-sidebar')?.getBoundingClientRect().width ?? 0,
          diffLeft: document.querySelector('.diff-content')?.getBoundingClientRect().left ?? 0,
        })
        """
    )
    assert before["sidebarWidth"] > 200

    page.get_by_role("button", name="Hide stack sidebar").click()
    expect(page.get_by_role("button", name="Show stack sidebar")).to_be_visible()
    page.wait_for_function(
        """
        (beforeDiffLeft) => {
          const sidebar = document.querySelector('#stack-sidebar');
          const diff = document.querySelector('.diff-content');
          if (!sidebar || !diff) return false;
          return sidebar.getBoundingClientRect().width < 5
            && diff.getBoundingClientRect().left < beforeDiffLeft - 100
            && localStorage.getItem('shortcake-stack-sidebar-collapsed') === 'true';
        }
        """,
        arg=before["diffLeft"],
        timeout=5_000,
    )

    page.reload()
    page.wait_for_selector(".diff-content", timeout=10_000)
    expect(page.get_by_role("button", name="Show stack sidebar")).to_be_visible()
    page.wait_for_function(
        """
        () => {
          const sidebar = document.querySelector('#stack-sidebar');
          return !!sidebar && sidebar.getBoundingClientRect().width < 5;
        }
        """,
        timeout=5_000,
    )

    page.get_by_role("button", name="Show stack sidebar").click()
    expect(page.get_by_role("button", name="Hide stack sidebar")).to_be_visible()
    page.wait_for_function(
        """
        () => {
          const sidebar = document.querySelector('#stack-sidebar');
          return !!sidebar && sidebar.getBoundingClientRect().width > 200
            && localStorage.getItem('shortcake-stack-sidebar-collapsed') === 'false';
        }
        """,
        timeout=5_000,
    )
    expect(sidebar.get_by_text("branch_a")).to_be_visible()


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


def test_default_selection(page: Page, ui_url: str):
    """On load, working changes is auto-selected by default."""
    page.goto(ui_url)
    page.wait_for_selector("text=branch_a", timeout=15_000)

    # Working Changes button should have active styling
    working_btn = page.locator("button", has_text="Working Changes").first
    expect(working_btn).to_have_class(re.compile(r"bg-accent-bg"))

    # The diff header should show "Uncommitted changes"
    header = page.locator("header h2")
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
    expect(page.get_by_text("No tracked branches found")).to_be_visible(timeout=10_000)

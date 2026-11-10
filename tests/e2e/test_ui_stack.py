import json
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
    listbox = ui_page.locator("#sc-diff-listbox")
    expect(listbox.get_by_text("Working Changes")).to_be_visible()


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


def test_diff_switcher_token_filter(page: Page, ui_url: str):
    """Multi-word queries AND-match across branch name and commit subject."""
    page.goto(ui_url)
    open_diff_switcher(page)

    search = page.get_by_role("combobox")
    search.fill("branch_a feature")
    listbox = page.locator("#sc-diff-listbox")
    expect(listbox.get_by_text("branch_a")).to_be_visible()
    expect(listbox.get_by_text("branch_b")).not_to_be_visible()


def test_diff_switcher_escape_clears_filter_first(page: Page, ui_url: str):
    """Esc with an active query clears it; a second Esc closes the popover."""
    page.goto(ui_url)
    open_diff_switcher(page)

    search = page.get_by_role("combobox")
    search.fill("branch_a")
    search.press("Escape")
    expect(page.locator("#sc-diff-listbox")).to_be_visible()
    expect(search).to_have_value("")
    expect(page.locator("#sc-diff-listbox").get_by_text("branch_b")).to_be_visible()

    search.press("Escape")
    expect(page.locator("#sc-diff-listbox")).not_to_be_visible()


def test_diff_switcher_trunk_row(page: Page, ui_url: str):
    """The trunk branch anchors the stack list as a non-selectable root row."""
    page.goto(ui_url)
    open_diff_switcher(page)

    listbox = page.locator("#sc-diff-listbox")
    expect(listbox.get_by_text("main", exact=True)).to_be_visible()
    expect(listbox.get_by_text("trunk", exact=True)).to_be_visible()


def test_diff_switcher_highlight_opens_on_selection(page: Page, ui_url: str):
    """Reopening the switcher starts the keyboard highlight on the viewed branch."""
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(ui_url)
    select_diff_option(page, "branch_a")
    page.wait_for_selector(".diff-content", timeout=10_000)

    # Highlight starts on branch_a, so one ArrowDown lands on branch_b.
    open_diff_switcher(page)
    search = page.get_by_role("combobox")
    search.press("ArrowDown")
    search.press("Enter")
    expect(page.locator("header")).to_contain_text("branch_b")


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


def test_stack_sidebar_lists_stack(ui_page: Page):
    """The left sidebar shows the trunk and every branch of the viewed stack."""
    sidebar = ui_page.get_by_role("navigation", name="Stack")
    expect(sidebar).to_be_visible()
    expect(sidebar.get_by_text("main", exact=True)).to_be_visible()
    expect(sidebar.get_by_role("button", name="branch_a")).to_be_visible()
    expect(sidebar.get_by_role("button", name="branch_b")).to_have_attribute(
        "aria-current", "true"
    )
    # Every row (not just the viewed one) shows its commit count and diff stats.
    expect(sidebar.get_by_role("button", name="branch_a")).to_contain_text(
        "1 commit · +2 \u22120"
    )


def test_stack_sidebar_selects_branch(ui_page: Page):
    """Clicking a branch in the sidebar loads its diff and updates the URL."""
    sidebar = ui_page.get_by_role("navigation", name="Stack")
    sidebar.get_by_role("button", name="branch_a").click()

    expect(ui_page.locator("header")).to_contain_text("branch_a → main")
    expect(sidebar.get_by_role("button", name="branch_a")).to_have_attribute(
        "aria-current", "true"
    )
    expect(sidebar.get_by_role("button", name="branch_b")).not_to_have_attribute(
        "aria-current", "true"
    )
    assert ui_page.url.endswith("#/branch/branch_a")


def test_stack_sidebar_selects_working_changes(ui_page: Page):
    """The sidebar's Working changes row switches to the uncommitted diff."""
    sidebar = ui_page.get_by_role("navigation", name="Stack")
    sidebar.get_by_role("button", name=re.compile("^Working changes")).click()
    expect(ui_page.locator("header")).to_contain_text("Uncommitted changes")


def test_stack_sidebar_folds_other_stacks(page: Page, ui_url: str):
    """Stacks other than the viewed one collapse under "Other stacks"."""

    def branch(name: str, parent: str, *, current: bool = False) -> dict:
        return {
            "name": name,
            "parent": parent,
            "depth": 0,
            "isCurrent": current,
            "commitCount": 1,
            "commit": "1" * 40,
            "commitShort": "1111111",
            "commitSubject": f"Add {name}",
            "commitTime": 1_700_000_000,
        }

    stack = {
        "currentBranch": "2026-01-02-auth",
        "branches": [
            branch("2026-01-02-auth", "main", current=True),
            branch("2026-01-02-login", "2026-01-02-auth"),
            branch("2026-01-01-billing", "main"),
        ],
    }
    page.route(
        "**/api/stack",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(stack)
        ),
    )
    # Keep the 3s poll from swapping the real repo's stack back in.
    page.route(
        "**/api/state",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({**stack, "workingDiffKey": "fixed"}),
        ),
    )
    page.goto(ui_url)

    sidebar = page.get_by_role("navigation", name="Stack")
    # Date prefixes are dropped from the sidebar labels.
    expect(sidebar.get_by_text("auth", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("login", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("billing", exact=True)).not_to_be_visible()

    toggle = sidebar.get_by_role("button", name=re.compile("^Other stacks"))
    expect(toggle).to_contain_text("1 branch")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(sidebar.get_by_text("billing", exact=True)).to_be_visible()


def test_stack_sidebar_hidden_on_small_screens(page: Page, ui_url: str):
    """Below the md breakpoint the sidebar hides; the switcher still works."""
    page.set_viewport_size({"width": 700, "height": 900})
    page.goto(ui_url)
    expect(page.get_by_role("button", name="Switch diff")).to_be_visible()
    expect(page.get_by_test_id("stack-sidebar")).to_be_hidden()

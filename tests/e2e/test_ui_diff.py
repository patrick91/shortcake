import json
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _new_file_patch(path: str, marker: str, line_count: int = 80) -> str:
    lines = "\n".join(f"+{marker} line {i}" for i in range(1, line_count + 1))
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..abc1234\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{line_count} @@\n"
        f"{lines}\n"
    )


def test_diff_loads_on_branch_click(ui_page: Page):
    """Clicking branch_a loads its diff; header shows 'branch_a -> main'."""
    ui_page.locator("button", has_text="branch_a").first.click()

    # Wait for diff to load
    ui_page.wait_for_selector(".diff-content", timeout=10_000)

    header = ui_page.locator("header h2")
    expect(header).to_contain_text("branch_a")
    expect(header).to_contain_text("main")


def test_diff_shows_file_tree(ui_page: Page):
    """File tree sidebar shows changed files (feature_b.py for branch_b)."""
    file_tree = ui_page.locator("aside")
    expect(file_tree.get_by_text("feature_b.py")).to_be_visible()


def test_diff_shows_additions(ui_page: Page):
    """Diff content shows added lines from the branch."""
    # branch_b adds feature_b.py containing "def farewell"
    diff_content = ui_page.locator(".diff-content")
    expect(diff_content).to_contain_text("farewell")


def test_file_click_scrolls(ui_page: Page):
    """Clicking a file in the file tree highlights it as active."""
    file_btn = ui_page.locator("aside button", has_text="feature_b.py")
    file_btn.click()

    # The file button should receive active styling
    expect(file_btn).to_have_class(re.compile(r"bg-accent-bg"))


def test_file_click_scrolls_to_clicked_file_section(page: Page, ui_url: str):
    """Clicking a file in a multi-file diff scrolls to that file's section."""
    mock_patch = "".join(
        [
            _new_file_patch("z_first.py", "FIRST"),
            _new_file_patch("a_middle.py", "MIDDLE"),
            _new_file_patch("m_last.py", "LAST"),
        ]
    )
    page.route(
        re.compile(r"/api/diff\?"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"branch": "branch_b", "parent": "branch_a", "patch": mock_patch}
            ),
        ),
    )
    page.goto(ui_url)
    page.wait_for_selector("text=branch_a", timeout=15_000)
    page.locator("button", has_text="branch_b").first.click()
    page.wait_for_selector(".diff-content", timeout=10_000)

    page.evaluate("document.querySelector('.diff-content').scrollTop = 0")
    page.locator("aside button", has_text="a_middle.py").click()

    page.wait_for_function(
        """
        () => {
          const scroller = document.querySelector('.diff-content');
          const target = scroller?.querySelector('[data-file-path="a_middle.py"]');
          if (!scroller || !target) return false;
          const scrollerRect = scroller.getBoundingClientRect();
          const targetRect = target.getBoundingClientRect();
          const offset = targetRect.top - scrollerRect.top;
          return scroller.scrollTop > 0 && Math.abs(offset) <= 8;
        }
        """,
        timeout=5_000,
    )
    position = page.evaluate(
        """
        () => {
          const scroller = document.querySelector('.diff-content');
          const target = scroller.querySelector('[data-file-path="a_middle.py"]');
          if (!target) return null;
          const scrollerRect = scroller.getBoundingClientRect();
          const targetRect = target.getBoundingClientRect();
          return {
            offset: targetRect.top - scrollerRect.top,
            scrollTop: scroller.scrollTop,
          };
        }
        """
    )
    assert position is not None
    assert position["scrollTop"] > 0
    assert abs(position["offset"]) <= 8


def test_unified_split_toggle(ui_page: Page):
    """Switching between unified and split diff layouts."""
    split_btn = ui_page.get_by_role("button", name="Split")
    unified_btn = ui_page.get_by_role("button", name="Unified")

    # Switch to split
    split_btn.click()
    expect(split_btn).to_have_class(re.compile(r"bg-surface-active"))

    # Switch back to unified
    unified_btn.click()
    expect(unified_btn).to_have_class(re.compile(r"bg-surface-active"))


def test_file_count_badge(ui_page: Page):
    """Header shows correct file count badge."""
    # branch_b has 1 changed file (feature_b.py)
    expect(ui_page.locator("header")).to_contain_text("1 file")


def test_working_changes_diff(page: Page, ui_url: str):
    """Working Changes shows uncommitted changes when present."""
    mock_patch = (
        "diff --git a/new_file.py b/new_file.py\n"
        "new file mode 100644\n"
        "index 0000000..abc1234\n"
        "--- /dev/null\n"
        "+++ b/new_file.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def hello():\n"
        '+    return "world"\n'
    )
    page.route(
        "**/api/diff/working",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"patch": mock_patch}),
        ),
    )
    page.goto(ui_url)
    page.wait_for_selector("text=branch_a", timeout=15_000)

    page.get_by_text("Working Changes").click()

    expect(page.locator("header h2")).to_contain_text("Uncommitted changes")
    page.wait_for_selector(".diff-content", timeout=10_000)
    expect(page.locator(".diff-content")).to_contain_text("hello")


def test_empty_working_changes(page: Page, ui_url: str):
    """When clean, Working Changes shows 'No uncommitted changes'."""
    page.route(
        "**/api/diff/working",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"patch": ""}',
        ),
    )
    page.goto(ui_url)
    page.wait_for_selector("text=branch_a", timeout=15_000)

    page.get_by_text("Working Changes").click()

    expect(page.get_by_text("No uncommitted changes")).to_be_visible(timeout=5_000)


def test_empty_diff(page: Page, ui_url: str):
    """Branch with no file differences shows empty state."""
    page.route(
        re.compile(r"/api/diff\?"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"branch": "branch_b", "parent": "branch_a", "patch": ""}',
        ),
    )
    page.goto(ui_url)
    page.wait_for_selector("text=branch_a", timeout=15_000)

    # Click branch_b to trigger the mocked empty diff response
    page.locator("button", has_text="branch_b").first.click()
    expect(page.get_by_text("No file differences")).to_be_visible(timeout=5_000)

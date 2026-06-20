import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import select_diff_option

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


def _modified_python_patch(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,4 +1,7 @@\n"
        " from __future__ import annotations\n"
        " \n"
        "-def render():\n"
        '-    return {"status": "old"}\n'
        "+def render(options):\n"
        "+    try:\n"
        '+        return {"status": options.status}\n'
        "+    except ValueError as exc:\n"
        '+        raise RuntimeError(f"invalid {exc}") from exc\n'
    )


def _wait_for_token_colors(page: Page, path: str) -> list[str]:
    page.wait_for_function(
        """
        (path) => {
          const section = [...document.querySelectorAll('[data-file-path]')]
            .find((el) => el.dataset.filePath === path);
          const root = section?.querySelector('diffs-container')?.shadowRoot;
          if (!root) return false;
          return new Set([...root.querySelectorAll('[data-line] span')]
            .map((span) => getComputedStyle(span).color)).size > 1;
        }
        """,
        arg=path,
        timeout=15_000,
    )
    return page.evaluate(
        """
        (path) => {
          const section = [...document.querySelectorAll('[data-file-path]')]
            .find((el) => el.dataset.filePath === path);
          const root = section?.querySelector('diffs-container')?.shadowRoot;
          return [...new Set([...root.querySelectorAll('[data-line] span')]
            .map((span) => getComputedStyle(span).color))];
        }
        """,
        arg=path,
    )


def test_diff_loads_on_branch_click(ui_page: Page):
    """Clicking branch_a loads its diff; header shows 'branch_a -> main'."""
    select_diff_option(ui_page, "branch_a")

    # Wait for diff to load
    ui_page.wait_for_selector(".diff-content", timeout=10_000)

    header = ui_page.locator("header")
    expect(header).to_contain_text("branch_a")
    expect(header).to_contain_text("main")


def test_diff_shows_file_tree(ui_page: Page):
    """File tree sidebar shows changed files (feature_b.py for branch_b)."""
    file_entry = ui_page.locator(
        "aside file-tree-container "
        '[data-type="item"][data-item-type="file"][data-item-path="feature_b.py"]'
    )
    expect(file_entry).to_be_visible()


def test_diff_shows_additions(ui_page: Page):
    """Diff content shows added lines from the branch."""
    # branch_b adds feature_b.py containing "def farewell"
    diff_content = ui_page.locator(".diff-content")
    expect(diff_content).to_contain_text("farewell")


def test_diff_syntax_highlights_new_file(ui_page: Page):
    """New-file diffs render syntax token colors, including fallback CSS."""
    ui_page.wait_for_function(
        """
        () => [...document.querySelectorAll('diffs-container')]
          .some((root) => root.shadowRoot?.querySelector('style[data-unsafe-css]'))
        """,
        timeout=10_000,
    )

    unsafe_css = ui_page.evaluate(
        """
        () => [...document.querySelectorAll('diffs-container')]
          .flatMap((root) => [
            ...(root.shadowRoot?.querySelectorAll('style[data-unsafe-css]') ?? [])
          ])
          .map((style) => style.textContent ?? '')
          .join('\\n')
        """
    )

    assert "--diffs-token-light" in unsafe_css
    assert "--diffs-token-dark" in unsafe_css

    ui_page.wait_for_function(
        """
        () => new Set(
          [...document.querySelectorAll('diffs-container')]
            .flatMap((root) => [
              ...(root.shadowRoot?.querySelectorAll('[data-line] span') ?? [])
            ])
            .map((span) => getComputedStyle(span).color)
        ).size > 1
        """,
        timeout=15_000,
    )
    token_colors = ui_page.evaluate(
        """
        () => [...new Set(
          [...document.querySelectorAll('diffs-container')]
            .flatMap((root) => [
              ...(root.shadowRoot?.querySelectorAll('[data-line] span') ?? [])
            ])
            .map((span) => getComputedStyle(span).color)
        )]
        """
    )
    assert len(token_colors) > 1


def test_diff_syntax_highlights_modified_file(page: Page, ui_url: str):
    """Modified-file diffs render syntax token colors."""
    path = "src/cross_inertia/_page.py"
    page.route(
        re.compile(r"/api/diff\?"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "branch": "branch_b",
                    "parent": "branch_a",
                    "patch": _modified_python_patch(path),
                }
            ),
        ),
    )
    page.goto(ui_url)
    select_diff_option(page, "branch_b")
    page.wait_for_selector(".diff-content", timeout=10_000)

    token_colors = _wait_for_token_colors(page, path)
    assert len(token_colors) > 1


def test_file_click_scrolls(ui_page: Page):
    """Clicking a file in the file tree highlights it as active."""
    file_btn = ui_page.locator(
        "aside file-tree-container "
        '[data-type="item"][data-item-type="file"][data-item-path="feature_b.py"]'
    )
    file_btn.click()

    expect(file_btn).to_have_attribute("data-item-selected", "true")


def test_file_click_scrolls_to_clicked_file_section(page: Page, ui_url: str):
    """Clicking a file in a multi-file diff scrolls to that file's section."""
    mock_patch = "".join(
        [
            _new_file_patch("a_first.py", "FIRST"),
            _new_file_patch("b_middle.py", "MIDDLE"),
            _new_file_patch("c_last.py", "LAST"),
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
    select_diff_option(page, "branch_b")
    page.wait_for_selector(".diff-content", timeout=10_000)

    page.evaluate("document.querySelector('.diff-content').scrollTop = 0")
    page.locator(
        "aside file-tree-container "
        '[data-type="item"][data-item-type="file"][data-item-path="b_middle.py"]'
    ).click()

    page.wait_for_function(
        """
        () => {
          const scroller = document.querySelector('.diff-content');
          const target = scroller?.querySelector('[data-file-path="b_middle.py"]');
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
          const target = scroller.querySelector('[data-file-path="b_middle.py"]');
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


def test_marking_viewed_preserves_diff_scroll_anchor(page: Page, ui_url: str):
    """Collapsing a viewed file keeps its compact header anchored."""
    mock_patch = "".join(
        [
            _new_file_patch("a_first.py", "FIRST"),
            _new_file_patch("b_middle.py", "MIDDLE"),
            _new_file_patch("c_last.py", "LAST"),
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
    select_diff_option(page, "branch_b")
    page.wait_for_selector(".diff-content", timeout=10_000)

    page.locator(
        "aside file-tree-container "
        '[data-type="item"][data-item-type="file"][data-item-path="b_middle.py"]'
    ).click()
    page.wait_for_function(
        """
        () => {
          const scroller = document.querySelector('.diff-content');
          const target = scroller?.querySelector('[data-file-path="b_middle.py"]');
          if (!scroller || !target) return false;
          const scrollerRect = scroller.getBoundingClientRect();
          const targetRect = target.getBoundingClientRect();
          return Math.abs(targetRect.top - scrollerRect.top) <= 8;
        }
        """,
        timeout=5_000,
    )

    page.evaluate("document.querySelector('.diff-content').scrollTop += 600")
    before = page.evaluate(
        """
        () => {
          const scroller = document.querySelector('.diff-content');
          const section = scroller?.querySelector('[data-file-path="b_middle.py"]');
          if (!scroller || !section) return null;
          const sectionTop = section.getBoundingClientRect().top;
          const scrollerTop = scroller.getBoundingClientRect().top;
          return {
            offset: sectionTop - scrollerTop,
            scrollTop: scroller.scrollTop,
          };
        }
        """
    )
    assert before is not None
    assert before["offset"] < -100

    page.locator('[data-file-path="b_middle.py"] diffs-container').get_by_role(
        "button", name="Viewed"
    ).click()

    page.wait_for_selector('[data-file-path="b_middle.py"] >> text=Viewed')
    after = page.evaluate(
        """
        () => {
          const scroller = document.querySelector('.diff-content');
          const section = scroller?.querySelector('[data-file-path="b_middle.py"]');
          if (!scroller || !section) return null;
          const sectionTop = section.getBoundingClientRect().top;
          const scrollerTop = scroller.getBoundingClientRect().top;
          return {
            offset: sectionTop - scrollerTop,
            scrollTop: scroller.scrollTop,
          };
        }
        """
    )
    assert after is not None
    assert abs(after["offset"]) <= 8
    assert after["scrollTop"] < before["scrollTop"]


def test_marking_viewed_persists_after_reload(page: Page, ui_url: str):
    """Viewed files are restored from persisted review state after reloading."""
    path = "persisted_viewed.py"
    mock_patch = _new_file_patch(path, "PERSISTED")

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
    select_diff_option(page, "branch_b")
    page.wait_for_selector(f'[data-file-path="{path}"] diffs-container', timeout=10_000)

    page.locator(f'[data-file-path="{path}"] diffs-container').get_by_role(
        "button", name="Viewed"
    ).click()
    expect(page.locator(f'[data-file-path="{path}"] diffs-container')).to_have_count(0)

    page.reload()
    page.wait_for_selector(f'[data-file-path="{path}"]', timeout=10_000)

    expect(page.locator(f'[data-file-path="{path}"] diffs-container')).to_have_count(0)
    expect(page.locator(f'[data-file-path="{path}"]')).to_contain_text("Viewed")


def test_marking_viewed_resets_when_file_patch_changes(page: Page, ui_url: str):
    """A stored viewed mark is ignored when the file's patch changes."""
    path = "changed_viewed.py"
    current_patch = {"value": _new_file_patch(path, "BEFORE")}

    page.route(
        re.compile(r"/api/diff\?"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "branch": "branch_b",
                    "parent": "branch_a",
                    "patch": current_patch["value"],
                }
            ),
        ),
    )
    page.goto(ui_url)
    select_diff_option(page, "branch_b")
    page.wait_for_selector(f'[data-file-path="{path}"] diffs-container', timeout=10_000)

    page.locator(f'[data-file-path="{path}"] diffs-container').get_by_role(
        "button", name="Viewed"
    ).click()
    expect(page.locator(f'[data-file-path="{path}"] diffs-container')).to_have_count(0)

    current_patch["value"] = _new_file_patch(path, "AFTER")
    page.reload()
    page.wait_for_selector(f'[data-file-path="{path}"] diffs-container', timeout=10_000)

    expect(page.locator(f'[data-file-path="{path}"] diffs-container')).to_have_count(1)
    expect(page.locator(f'[data-file-path="{path}"]')).to_contain_text("AFTER line 1")

    current_patch["value"] = _new_file_patch(path, "BEFORE")
    page.reload()
    page.wait_for_selector(f'[data-file-path="{path}"] diffs-container', timeout=10_000)

    expect(page.locator(f'[data-file-path="{path}"] diffs-container')).to_have_count(1)
    expect(page.locator(f'[data-file-path="{path}"]')).to_contain_text("BEFORE line 1")


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


def test_diff_layout_persists_after_reload(ui_page: Page):
    """The selected diff layout is restored after reloading the UI."""
    split_btn = ui_page.get_by_role("button", name="Split")
    unified_btn = ui_page.get_by_role("button", name="Unified")

    split_btn.click()
    expect(split_btn).to_have_class(re.compile(r"bg-surface-active"))

    ui_page.reload()
    ui_page.wait_for_selector(".diff-content", timeout=10_000)
    expect(split_btn).to_have_class(re.compile(r"bg-surface-active"))

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

    select_diff_option(page, "Working Changes")

    expect(page.locator("header")).to_contain_text("Uncommitted changes")
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

    select_diff_option(page, "Working Changes")

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

    # Click branch_b to trigger the mocked empty diff response
    select_diff_option(page, "branch_b")
    expect(page.get_by_text("No file differences")).to_be_visible(timeout=5_000)

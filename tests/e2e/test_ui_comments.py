import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import select_diff_option

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COMMENT_TEXTAREA = "textarea[placeholder='Add a comment...']"
LINE_GUTTER_SELECTOR = "[data-column-number]"


def _click_diff_line(page: Page):
    """Click the first visible line-number gutter and then the 'Comment'
    button on the selection toolbar to open the comment input.

    The UI flow is: click gutter → selection toolbar appears → click
    'Comment' button → ``CommentInput`` textarea appears.
    """
    gutter = page.locator(LINE_GUTTER_SELECTOR).first
    gutter.wait_for(state="visible", timeout=5_000)
    gutter.click()

    # The selection toolbar appears with a "Comment" button
    comment_btn = page.get_by_role("button", name="Comment")
    comment_btn.wait_for(state="visible", timeout=5_000)
    comment_btn.click()


def _add_comment(page: Page, text: str = "Test comment"):
    """Select a line and add a comment with the given text."""
    _click_diff_line(page)
    page.wait_for_selector(COMMENT_TEXTAREA, timeout=5_000)

    page.locator(COMMENT_TEXTAREA).fill(text)
    page.get_by_role("button", name="Add").click()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_line_selection_opens_input(ui_page: Page):
    """Clicking a line-number gutter opens the comment textarea."""
    _click_diff_line(ui_page)
    expect(ui_page.locator(COMMENT_TEXTAREA)).to_be_visible(timeout=5_000)


def test_add_comment(ui_page: Page):
    """Typing text and clicking 'Add' saves the comment inline."""
    _add_comment(ui_page, "My first comment")

    # The saved comment text should be visible in the diff area
    expect(ui_page.locator("text=My first comment")).to_be_visible()


def test_add_comment_keyboard(ui_page: Page):
    """Cmd+Enter (or Ctrl+Enter) submits the comment."""
    _click_diff_line(ui_page)
    textarea = ui_page.locator(COMMENT_TEXTAREA)
    textarea.wait_for(state="visible", timeout=5_000)
    textarea.fill("Keyboard comment")

    # Submit via keyboard shortcut
    modifier = (
        "Meta" if ui_page.evaluate("navigator.platform.includes('Mac')") else "Control"
    )
    textarea.press(f"{modifier}+Enter")

    expect(ui_page.locator("text=Keyboard comment")).to_be_visible()


def test_cancel_comment(ui_page: Page):
    """Pressing Cancel dismisses the comment input."""
    _click_diff_line(ui_page)
    ui_page.wait_for_selector(COMMENT_TEXTAREA, timeout=5_000)
    ui_page.locator(COMMENT_TEXTAREA).fill("Should be cancelled")

    ui_page.get_by_role("button", name="Cancel").click()

    expect(ui_page.locator(COMMENT_TEXTAREA)).not_to_be_visible()
    expect(ui_page.locator("text=Should be cancelled")).not_to_be_visible()


def test_cancel_comment_escape(ui_page: Page):
    """Pressing Escape dismisses the comment input."""
    _click_diff_line(ui_page)
    textarea = ui_page.locator(COMMENT_TEXTAREA)
    textarea.wait_for(state="visible", timeout=5_000)

    textarea.press("Escape")

    expect(ui_page.locator(COMMENT_TEXTAREA)).not_to_be_visible()


def test_edit_comment(ui_page: Page):
    """Hovering a saved comment and clicking edit allows modification."""
    _add_comment(ui_page, "Original text")

    # Hover the saved comment to reveal edit/delete buttons
    comment_el = ui_page.locator("text=Original text").first
    comment_el.hover()

    # Click the edit button (pencil icon)
    ui_page.locator("button[title='Edit']").first.click()

    # The textarea should re-appear pre-filled
    textarea = ui_page.locator(COMMENT_TEXTAREA)
    expect(textarea).to_be_visible(timeout=5_000)
    expect(textarea).to_have_value("Original text")

    # Modify and save
    textarea.fill("Updated text")
    ui_page.get_by_role("button", name="Add").click()

    expect(ui_page.locator("text=Updated text")).to_be_visible()
    expect(ui_page.locator("text=Original text")).not_to_be_visible()


def test_delete_comment(ui_page: Page):
    """Hovering a saved comment and clicking delete removes it."""
    _add_comment(ui_page, "Delete me")

    comment_el = ui_page.locator("text=Delete me").first
    comment_el.hover()

    ui_page.locator("button[title='Delete']").first.click()

    expect(ui_page.locator("text=Delete me")).not_to_be_visible()


def test_copy_comments_button(ui_page: Page):
    """With comments present, the 'Copy N comments' button appears."""
    _add_comment(ui_page, "Comment for copy test")

    expect(ui_page.locator("button", has_text="Copy 1 comment")).to_be_visible()


def test_copy_comments_format(ui_page: Page):
    """Clicking 'Copy' writes markdown-formatted comments to clipboard."""
    _add_comment(ui_page, "Clipboard comment")

    copy_btn = ui_page.locator("button", has_text="Copy 1 comment")
    copy_btn.click()

    # Verify button text changes to "Copied!"
    expect(ui_page.locator("button", has_text="Copied!")).to_be_visible()

    # Read clipboard content
    clipboard = ui_page.evaluate("() => navigator.clipboard.readText()")
    assert "Clipboard comment" in clipboard
    assert clipboard.startswith("- `")


def test_comments_clear_on_branch_switch(ui_page: Page):
    """Switching branches clears all comments."""
    _add_comment(ui_page, "Temporary comment")
    expect(ui_page.locator("text=Temporary comment")).to_be_visible()

    # Switch to branch_a
    select_diff_option(ui_page, "branch_a")
    ui_page.wait_for_selector(".diff-content", timeout=10_000)

    # Comments from previous branch should be gone
    expect(ui_page.locator("text=Temporary comment")).not_to_be_visible()

    # No copy button should be visible
    expect(ui_page.locator("button", has_text="Copy")).not_to_be_visible()

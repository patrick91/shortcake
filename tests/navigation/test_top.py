"""Tests for the top command."""

from pathlib import Path

import pytest

from shortcake import _git as git
from shortcake.commands.top import (
    DetachedHeadError,
    MultipleChildrenError,
    TopResult,
    _top,
)
from tests._git_helpers import Repo, get_ref, set_ref, switch_branch


def test_top_jumps_to_leaf(repo_with_stack: Repo) -> None:
    """Test jumping from branch_a to branch_c (top of stack)."""
    switch_branch(repo_with_stack, "branch_a")

    result = _top(repo_with_stack)

    assert isinstance(result, TopResult)
    assert result.from_branch == "branch_a"
    assert result.to_branch == "branch_c"
    assert result.already_at_top is False
    assert git.get_current_branch(repo_with_stack) == "branch_c"


def test_top_already_at_top(repo_with_stack: Repo) -> None:
    """Test when already at top of stack."""
    repo_with_stack.set_head("refs/heads/branch_c")

    result = _top(repo_with_stack)

    assert result.from_branch == "branch_c"
    assert result.to_branch == "branch_c"
    assert result.already_at_top is True


def test_top_from_main(repo_with_stack: Repo) -> None:
    """Test jumping from main to top of stack."""
    switch_branch(repo_with_stack, "main")

    result = _top(repo_with_stack)

    assert result.from_branch == "main"
    assert result.to_branch == "branch_c"


def test_top_multiple_children(repo_with_fork: Repo) -> None:
    """Test error when multiple children at some level."""
    switch_branch(repo_with_fork, "main")

    # Walking up from main will hit branch_a which has two children
    with pytest.raises(MultipleChildrenError) as exc_info:
        _top(repo_with_fork)

    assert exc_info.value.branch == "branch_a"
    assert "branch_b" in exc_info.value.children
    assert "branch_c" in exc_info.value.children


def test_top_detached_head(repo_with_stack: Repo) -> None:
    """Test error when in detached HEAD state."""
    main_sha = get_ref(repo_with_stack, "refs/heads/main")
    set_ref(repo_with_stack, "HEAD", main_sha)

    with pytest.raises(DetachedHeadError):
        _top(repo_with_stack)


def test_top_updates_working_directory(repo_with_stack: Repo) -> None:
    """Test that navigation updates working directory, not just HEAD."""
    # repo_with_stack has: main → branch_a (a.txt) → branch_b (b.txt) → branch_c (c.txt)
    tmp_path = Path(repo_with_stack.workdir)

    # Switch to main (only has README.md, no a.txt/b.txt/c.txt)
    switch_branch(repo_with_stack, "main")
    assert not (tmp_path / "c.txt").exists()

    # Navigate to top (branch_c)
    _top(repo_with_stack)

    # Verify branch changed AND working directory updated
    assert git.get_current_branch(repo_with_stack) == "branch_c"
    assert (tmp_path / "c.txt").exists()  # branch_c's file should now exist

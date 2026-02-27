"""Tests for the top command."""

from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake.commands.top import (
    DetachedHeadError,
    MultipleChildrenError,
    TopResult,
    _top,
)


def test_top_jumps_to_leaf(repo_with_stack: Repo) -> None:
    """Test jumping from branch_a to branch_c (top of stack)."""
    porcelain.switch(repo_with_stack, "branch_a")

    result = _top(repo_with_stack)

    assert isinstance(result, TopResult)
    assert result.from_branch == "branch_a"
    assert result.to_branch == "branch_c"
    assert result.already_at_top is False
    assert git.get_current_branch(repo_with_stack) == "branch_c"


def test_top_already_at_top(repo_with_stack: Repo) -> None:
    """Test when already at top of stack."""
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    result = _top(repo_with_stack)

    assert result.from_branch == "branch_c"
    assert result.to_branch == "branch_c"
    assert result.already_at_top is True


def test_top_from_main(repo_with_stack: Repo) -> None:
    """Test jumping from main to top of stack."""
    porcelain.switch(repo_with_stack, "main")

    result = _top(repo_with_stack)

    assert result.from_branch == "main"
    assert result.to_branch == "branch_c"


def test_top_multiple_children(repo_with_fork: Repo) -> None:
    """Test error when multiple children at some level."""
    porcelain.switch(repo_with_fork, "main")

    # Walking up from main will hit branch_a which has two children
    with pytest.raises(MultipleChildrenError) as exc_info:
        _top(repo_with_fork)

    assert exc_info.value.branch == "branch_a"
    assert "branch_b" in exc_info.value.children
    assert "branch_c" in exc_info.value.children


def test_top_detached_head(repo_with_stack: Repo) -> None:
    """Test error when in detached HEAD state."""
    main_sha = repo_with_stack.refs[b"refs/heads/main"]
    del repo_with_stack.refs[b"HEAD"]
    repo_with_stack.refs[b"HEAD"] = main_sha

    with pytest.raises(DetachedHeadError):
        _top(repo_with_stack)


def test_top_updates_working_directory(repo_with_stack: Repo) -> None:
    """Test that navigation updates working directory, not just HEAD."""
    # repo_with_stack has: main → branch_a (a.txt) → branch_b (b.txt) → branch_c (c.txt)
    tmp_path = Path(repo_with_stack.path)

    # Switch to main (only has README.md, no a.txt/b.txt/c.txt)
    porcelain.switch(repo_with_stack, "main")
    assert not (tmp_path / "c.txt").exists()

    # Navigate to top (branch_c)
    _top(repo_with_stack)

    # Verify branch changed AND working directory updated
    assert git.get_current_branch(repo_with_stack) == "branch_c"
    assert (tmp_path / "c.txt").exists()  # branch_c's file should now exist

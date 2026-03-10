"""Tests for the up command."""

from pathlib import Path

import pytest

from shortcake import _git as git
from shortcake.commands.up import (
    AlreadyAtTopError,
    DetachedHeadError,
    MultipleChildrenError,
    UpResult,
    _up,
)
from tests._git_helpers import Repo, switch_branch


def test_up_single_child(repo_with_stack: Repo) -> None:
    """Test moving up when there's a single child."""
    # Start on branch_a
    switch_branch(repo_with_stack, "branch_a")

    result = _up(repo_with_stack)

    assert isinstance(result, UpResult)
    assert result.from_branch == "branch_a"
    assert result.to_branch == "branch_b"
    assert git.get_current_branch(repo_with_stack) == "branch_b"


def test_up_at_top(repo_with_stack: Repo) -> None:
    """Test error when already at top of stack."""
    # branch_c is at top (no children)
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    with pytest.raises(AlreadyAtTopError):
        _up(repo_with_stack)


def test_up_multiple_children(repo_with_fork: Repo) -> None:
    """Test error when multiple children exist."""
    # branch_a has two children: branch_b and branch_c
    switch_branch(repo_with_fork, "branch_a")

    with pytest.raises(MultipleChildrenError) as exc_info:
        _up(repo_with_fork)

    assert "branch_b" in exc_info.value.children
    assert "branch_c" in exc_info.value.children


def test_up_multiple_children_with_selection(repo_with_fork: Repo) -> None:
    """Test moving up when specifying which child to use."""
    switch_branch(repo_with_fork, "branch_a")

    result = _up(repo_with_fork, child="branch_b")

    assert result.to_branch == "branch_b"
    assert git.get_current_branch(repo_with_fork) == "branch_b"


def test_up_detached_head(repo_with_stack: Repo) -> None:
    """Test error when in detached HEAD state."""
    # Detach HEAD
    main_sha = repo_with_stack.refs[b"refs/heads/main"]
    del repo_with_stack.refs[b"HEAD"]
    repo_with_stack.refs[b"HEAD"] = main_sha

    with pytest.raises(DetachedHeadError):
        _up(repo_with_stack)


def test_up_from_main(repo_with_stack: Repo) -> None:
    """Test moving up from main to its child."""
    switch_branch(repo_with_stack, "main")

    result = _up(repo_with_stack)

    assert result.from_branch == "main"
    assert result.to_branch == "branch_a"


def test_up_updates_working_directory(repo_with_stack: Repo) -> None:
    """Test that navigation updates working directory, not just HEAD."""
    # repo_with_stack has: main → branch_a (a.txt) → branch_b (b.txt) → branch_c (c.txt)
    tmp_path = Path(repo_with_stack.path)

    # Switch to branch_a (has a.txt but not b.txt)
    switch_branch(repo_with_stack, "branch_a")
    assert (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b.txt").exists()

    # Navigate up to branch_b
    _up(repo_with_stack)

    # Verify branch changed AND working directory updated
    assert git.get_current_branch(repo_with_stack) == "branch_b"
    assert (tmp_path / "b.txt").exists()  # branch_b's file should now exist

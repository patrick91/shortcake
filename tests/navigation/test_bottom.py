"""Tests for the bottom command."""

from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake.commands.bottom import (
    BottomResult,
    DetachedHeadError,
    NotTrackedError,
    _bottom,
)


def test_bottom_jumps_to_base(repo_with_stack: Repo) -> None:
    """Test jumping from branch_c to branch_a (bottom of stack)."""
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    result = _bottom(repo_with_stack)

    assert isinstance(result, BottomResult)
    assert result.from_branch == "branch_c"
    assert result.to_branch == "branch_a"
    assert result.already_at_bottom is False
    assert git.get_current_branch(repo_with_stack) == "branch_a"


def test_bottom_already_at_bottom(repo_with_stack: Repo) -> None:
    """Test when already at bottom of stack (parent is trunk)."""
    porcelain.switch(repo_with_stack, "branch_a")

    result = _bottom(repo_with_stack)

    assert result.from_branch == "branch_a"
    assert result.to_branch == "branch_a"
    assert result.already_at_bottom is True


def test_bottom_from_middle(repo_with_stack: Repo) -> None:
    """Test jumping from middle of stack to bottom."""
    porcelain.switch(repo_with_stack, "branch_b")

    result = _bottom(repo_with_stack)

    assert result.from_branch == "branch_b"
    assert result.to_branch == "branch_a"
    assert result.already_at_bottom is False


def test_bottom_not_tracked(tmp_path: Path) -> None:
    """Test error when branch is not tracked."""
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create untracked branch
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/untracked"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/untracked")
    file_f = tmp_path / "f.txt"
    file_f.write_text("f")
    porcelain.add(repo, paths=[str(file_f)])
    porcelain.commit(repo, message=b"Add feature")

    with pytest.raises(NotTrackedError):
        _bottom(repo)


def test_bottom_detached_head(repo_with_stack: Repo) -> None:
    """Test error when in detached HEAD state."""
    main_sha = repo_with_stack.refs[b"refs/heads/main"]
    del repo_with_stack.refs[b"HEAD"]
    repo_with_stack.refs[b"HEAD"] = main_sha

    with pytest.raises(DetachedHeadError):
        _bottom(repo_with_stack)


def test_bottom_single_tracked_branch(repo_with_tracked_feature: Repo) -> None:
    """Test bottom when there's only one tracked branch above trunk."""
    repo_with_tracked_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    result = _bottom(repo_with_tracked_feature)

    # feature's parent is main (trunk), so it's already at bottom
    assert result.from_branch == "feature"
    assert result.to_branch == "feature"
    assert result.already_at_bottom is True


def test_bottom_updates_working_directory(repo_with_stack: Repo) -> None:
    """Test that navigation updates working directory, not just HEAD."""
    # repo_with_stack has: main → branch_a (a.txt) → branch_b (b.txt) → branch_c (c.txt)
    # Fixture ends on branch_c, so c.txt exists in working directory
    tmp_path = Path(repo_with_stack.path)

    # Verify we're on branch_c with c.txt present
    assert git.get_current_branch(repo_with_stack) == "branch_c"
    assert (tmp_path / "c.txt").exists()

    # Navigate to bottom (branch_a)
    _bottom(repo_with_stack)

    # Verify branch changed AND working directory updated
    assert git.get_current_branch(repo_with_stack) == "branch_a"
    assert (tmp_path / "a.txt").exists()  # branch_a's file should exist
    assert not (tmp_path / "b.txt").exists()  # branch_b's file should be gone
    assert not (tmp_path / "c.txt").exists()  # branch_c's file should be gone

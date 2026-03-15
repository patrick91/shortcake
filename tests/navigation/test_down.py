"""Tests for the down command."""

from pathlib import Path

import pytest

from shortcake import _git as git
from shortcake.commands.down import (
    DetachedHeadError,
    DownResult,
    NotTrackedError,
    _down,
)
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_branch_head,
    get_ref,
    init_repo,
    set_ref,
    switch_branch,
)


def test_down_to_parent(repo_with_stack: Repo) -> None:
    """Test moving down from branch_b to branch_a."""
    switch_branch(repo_with_stack, "branch_b")

    result = _down(repo_with_stack)

    assert isinstance(result, DownResult)
    assert result.from_branch == "branch_b"
    assert result.to_branch == "branch_a"
    assert result.at_bottom is False
    assert git.get_current_branch(repo_with_stack) == "branch_a"


def test_down_to_trunk(repo_with_tracked_feature: Repo) -> None:
    """Test moving down to trunk (main)."""
    switch_branch(repo_with_tracked_feature, "feature")

    result = _down(repo_with_tracked_feature)

    assert result.from_branch == "feature"
    assert result.to_branch == "main"
    assert result.at_bottom is True
    assert git.get_current_branch(repo_with_tracked_feature) == "main"


def test_down_not_tracked(tmp_path: Path) -> None:
    """Test error when branch is not tracked."""
    # Create repo with untracked branch
    repo = init_repo(tmp_path)
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    # Create untracked feature branch
    create_branch(repo, "untracked", get_branch_head(repo, "main"), checkout=True)
    commit_files(repo, {tmp_path / "f.txt": "f"}, "Add feature")

    with pytest.raises(NotTrackedError):
        _down(repo)


def test_down_detached_head(repo_with_stack: Repo) -> None:
    """Test error when in detached HEAD state."""
    main_sha = get_ref(repo_with_stack, "refs/heads/main")
    set_ref(repo_with_stack, "HEAD", main_sha)

    with pytest.raises(DetachedHeadError):
        _down(repo_with_stack)


def test_down_updates_working_directory(repo_with_stack: Repo) -> None:
    """Test that navigation updates working directory, not just HEAD."""
    # repo_with_stack has: main → branch_a (a.txt) → branch_b (b.txt) → branch_c (c.txt)
    # Fixture ends on branch_c, so c.txt exists in working directory
    tmp_path = Path(repo_with_stack.workdir)

    # Verify we're on branch_c with c.txt present
    assert git.get_current_branch(repo_with_stack) == "branch_c"
    assert (tmp_path / "c.txt").exists()

    # Navigate down to branch_b
    _down(repo_with_stack)

    # Verify branch changed AND working directory updated
    assert git.get_current_branch(repo_with_stack) == "branch_b"
    assert (tmp_path / "b.txt").exists()  # branch_b's file should exist
    assert not (tmp_path / "c.txt").exists()  # branch_c's file should be gone

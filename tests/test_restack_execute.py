"""Tests for restack execution."""

import re
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState
from shortcake.commands.restack import (
    RestackError,
    _needs_restack,
    _restack,
)

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_restack_nothing_to_do(repo_with_stack: Repo) -> None:
    """Restack when everything up to date."""
    result = _restack(repo_with_stack)
    assert result.restacked_branches == []
    assert result.conflict_branch is None


def test_restack_dry_run(repo_with_stack_behind: Repo) -> None:
    """Dry run shows plan without executing."""
    # Store original SHAs
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    result = _restack(repo_with_stack_behind, dry_run=True)

    # Should show plan but not execute
    assert result.restacked_branches == []

    # Branches should be unchanged
    assert git.get_branch_head(repo_with_stack_behind, "branch_a") == original_a
    assert git.get_branch_head(repo_with_stack_behind, "branch_b") == original_b


def test_restack_linear_stack(repo_with_stack_behind: Repo, tmp_path: Path) -> None:
    """Restack propagates through linear stack."""
    # Store original SHAs for verification
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    result = _restack(repo_with_stack_behind)

    assert result.restacked_branches == ["branch_a", "branch_b"]
    assert result.conflict_branch is None

    # Branches should have moved
    new_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    new_b = git.get_branch_head(repo_with_stack_behind, "branch_b")
    assert new_a != original_a
    assert new_b != original_b

    # branch_a should now be on top of main
    assert not _needs_restack(repo_with_stack_behind, "branch_a", "main")

    # branch_b should now be on top of branch_a
    assert not _needs_restack(repo_with_stack_behind, "branch_b", "branch_a")


def test_restack_detached_head(temp_repo: Repo) -> None:
    """Error in detached HEAD state."""
    # Detach HEAD by removing the symbolic ref
    head_sha = temp_repo.refs[b"refs/heads/main"]
    # Remove symbolic ref and set HEAD directly to SHA
    temp_repo.refs.remove_if_equals(b"HEAD", temp_repo.refs.read_ref(b"HEAD"))
    temp_repo.refs.add_if_new(b"HEAD", head_sha)

    with pytest.raises(RestackError, match="detached HEAD"):
        _restack(temp_repo)


def test_restack_uncommitted_changes(
    repo_with_stack_behind: Repo, tmp_path: Path
) -> None:
    """Error with uncommitted changes."""
    # Create uncommitted change
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    porcelain.add(repo_with_stack_behind, paths=[str(test_file)])

    with pytest.raises(RestackError, match="uncommitted changes"):
        _restack(repo_with_stack_behind)


def test_restack_already_in_progress(repo_with_stack_behind: Repo) -> None:
    """Error when restack already in progress."""
    # Create fake state file
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[],
        current_index=0,
        original_refs={},
    )
    state.save(repo_with_stack_behind)

    with pytest.raises(RestackError, match="already in progress"):
        _restack(repo_with_stack_behind)

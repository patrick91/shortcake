"""Tests for restack continue and abort."""

import re

import pytest
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake.commands.abort import AbortError, _abort
from shortcake.commands.continue_ import ContinueError, _continue

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_continue_nothing_in_progress(temp_repo: Repo) -> None:
    """Error when no restack to continue."""
    with pytest.raises(ContinueError, match="No restack in progress"):
        _continue(temp_repo)


def test_abort_nothing_in_progress(temp_repo: Repo) -> None:
    """Error when no restack to abort."""
    with pytest.raises(AbortError, match="No restack in progress"):
        _abort(temp_repo)


def test_abort_restores_original(repo_with_stack_behind: Repo) -> None:
    """Abort restores all branches to original SHAs."""
    # Store original SHAs
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    # Create state as if restack started
    # dulwich SHAs are 40 ASCII hex bytes, decode to string for storage
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
            RestackStep(branch="branch_b", onto="branch_a", merge_base="def456"),
        ],
        current_index=0,
        original_refs={
            "branch_a": original_a.decode(),
            "branch_b": original_b.decode(),
        },
    )
    state.save(repo_with_stack_behind)

    result = _abort(repo_with_stack_behind)

    assert set(result.restored_branches) == {"branch_a", "branch_b"}

    # Branches should be restored
    assert git.get_branch_head(repo_with_stack_behind, "branch_a") == original_a
    assert git.get_branch_head(repo_with_stack_behind, "branch_b") == original_b

    # State should be deleted
    assert not RestackState.exists(repo_with_stack_behind)


def test_abort_with_invalid_sha(
    repo_with_stack_behind: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abort handles invalid SHA gracefully with warning."""
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    # Create state
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
            RestackStep(branch="branch_b", onto="branch_a", merge_base="def456"),
        ],
        current_index=0,
        original_refs={
            "branch_a": original_a.decode(),
            "branch_b": original_b.decode(),
        },
    )
    state.save(repo_with_stack_behind)

    # Mock update_branch to raise KeyError for branch_b (simulating deleted commit)
    original_update = git.update_branch

    def mock_update_branch(repo: Repo, branch: str, sha_hex: str) -> None:
        if branch == "branch_b":
            raise KeyError(sha_hex)
        original_update(repo, branch, sha_hex)

    monkeypatch.setattr(git, "update_branch", mock_update_branch)

    result = _abort(repo_with_stack_behind)

    # branch_a should be restored, branch_b should fail gracefully
    assert "branch_a" in result.restored_branches
    assert "branch_b" not in result.restored_branches

    # State should still be deleted
    assert not RestackState.exists(repo_with_stack_behind)

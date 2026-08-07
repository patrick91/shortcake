"""Tests for restack continue and abort."""

import re

import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake.commands.abort import AbortError, _abort
from shortcake.commands.continue_ import ContinueError, _continue
from tests._git_helpers import Repo

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


def test_abort_removes_branches_created_by_import(repo_with_stack: Repo) -> None:
    original_sha = git.get_branch_head(repo_with_stack, "branch_b")
    git.create_branch(repo_with_stack, "imported", original_sha)
    RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[],
        current_index=0,
        original_refs={},
        created_branches=["imported"],
    ).save(repo_with_stack)

    _abort(repo_with_stack)

    assert not git.branch_exists(repo_with_stack, "imported")


def test_continue_finishes_on_imported_pull_request_branch(
    repo_with_stack: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        completion_branch="branch_a",
        plan=[
            RestackStep(
                branch="branch_b",
                onto="branch_a",
                merge_base="abc123",
                new_parent_trailer="branch_a",
            )
        ],
        current_index=0,
        original_refs={},
    ).save(repo_with_stack)
    monkeypatch.setattr(git, "is_rebase_in_progress", lambda _repo: False)
    monkeypatch.setattr(
        "shortcake.commands.continue_._needs_restack",
        lambda _repo, _branch, _onto: False,
    )
    monkeypatch.setattr(
        "shortcake.commands.continue_._trailer_lost",
        lambda _repo, _branch, _onto: False,
    )
    monkeypatch.setattr(
        "shortcake.commands.reorder._update_branch_trailer",
        lambda _repo, _branch, _parent: None,
    )

    _continue(repo_with_stack)

    assert git.get_current_branch(repo_with_stack) == "branch_a"
    assert not RestackState.exists(repo_with_stack)


def test_continue_keeps_state_when_final_checkout_fails(
    repo_with_stack: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(
                branch="branch_b",
                onto="branch_a",
                merge_base="abc123",
            )
        ],
        current_index=0,
        original_refs={},
    ).save(repo_with_stack)
    monkeypatch.setattr(git, "is_rebase_in_progress", lambda _repo: False)
    monkeypatch.setattr(
        "shortcake.commands.continue_._needs_restack",
        lambda _repo, _branch, _onto: False,
    )
    monkeypatch.setattr(
        "shortcake.commands.continue_._trailer_lost",
        lambda _repo, _branch, _onto: False,
    )

    def fail_checkout(_repo: Repo, _branch: str, *, force: bool) -> None:
        assert force
        raise ValueError("checked out elsewhere")

    monkeypatch.setattr(git, "switch_branch", fail_checkout)

    with pytest.raises(ContinueError, match="checked out elsewhere"):
        _continue(repo_with_stack)

    assert RestackState.exists(repo_with_stack)


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

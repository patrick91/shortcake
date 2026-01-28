"""Tests for restack continue edge cases."""

import re
from pathlib import Path

import pytest
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake.cli import app

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_continue_remaining_branch_rebase_not_conflict(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue handles rebase error (not conflict) in remaining branches."""
    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Create state with multiple branches - start at index 0 which is already done
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(
                branch="branch_a",
                onto="main",
                merge_base=main_sha.decode(),
            ),
            RestackStep(
                branch="branch_b",
                onto="branch_a",
                merge_base=branch_a_sha.decode(),
            ),
        ],
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
            "branch_b": branch_b_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Mock _rebase_branch to fail without conflict
    def mock_rebase(repo, branch, onto, merge_base):
        return git.RebaseResult(success=False, error_output="fatal: error")

    monkeypatch.setattr("shortcake.commands.continue_._rebase_branch", mock_rebase)
    # Also mock is_rebase_in_progress to return False (not a conflict)
    monkeypatch.setattr(
        "shortcake.commands.continue_.git.is_rebase_in_progress", lambda repo: False
    )

    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    # Should show the error message
    assert "Failed to rebase" in result.output


def test_continue_remaining_branch_conflict(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue shows conflict message when remaining branch hits conflict."""
    from shortcake.commands import continue_ as continue_module

    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Create state with multiple branches - start at index 0 which is already done
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(
                branch="branch_a",
                onto="main",
                merge_base=main_sha.decode(),
            ),
            RestackStep(
                branch="branch_b",
                onto="branch_a",
                merge_base=branch_a_sha.decode(),
            ),
        ],
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
            "branch_b": branch_b_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Track if we got to the loop
    rebase_called = [False]

    # Mock _rebase_branch to fail with conflict on branch_b
    def mock_rebase(repo, branch, onto, merge_base):
        rebase_called[0] = True
        # branch_b will fail with conflict
        return git.RebaseResult(success=False, conflict=True, error_output="")

    # Patch directly on the module object
    monkeypatch.setattr(continue_module, "_rebase_branch", mock_rebase)

    # Mock _needs_restack to return False (branch is up to date)
    def mock_needs_restack(repo, branch, onto):
        return False

    monkeypatch.setattr(continue_module, "_needs_restack", mock_needs_restack)

    # Mock is_rebase_in_progress to return False initially (no active rebase)
    # then True after rebase fails with conflict
    is_rebase_calls = [0]

    def mock_is_rebase_in_progress(repo):
        is_rebase_calls[0] += 1
        # First call: checking initial state, no rebase in progress
        # Second call: after _rebase_branch fails with conflict
        return is_rebase_calls[0] > 1

    monkeypatch.setattr(
        continue_module.git, "is_rebase_in_progress", mock_is_rebase_in_progress
    )

    # Mock _get_conflict_files to return some files
    def mock_get_conflict_files(path):
        return ["file.txt"]

    monkeypatch.setattr(continue_module, "_get_conflict_files", mock_get_conflict_files)

    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert rebase_called[0], f"Mock not called. Output: {result.output}"
    # Should show conflict message with the file
    assert "Conflict" in result.output or "file.txt" in result.output

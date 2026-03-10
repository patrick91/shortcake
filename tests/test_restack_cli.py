"""Tests for restack CLI commands."""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake.cli import app
from tests._git_helpers import Repo

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_cli_restack_nothing_to_do(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack when nothing to do."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 0
    assert "Everything up to date" in result.output


def test_cli_restack_dry_run(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack --dry-run."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack", "--dry-run"])

    assert result.exit_code == 0
    assert "Would restack" in result.output


def test_cli_restack_success(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack success."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 0
    assert "Restacked" in result.output
    assert "successfully" in result.output


def test_cli_restack_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack in detached HEAD state."""
    monkeypatch.chdir(tmp_path)
    # Detach HEAD
    head_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs.remove_if_equals(b"HEAD", temp_repo.refs.read_ref(b"HEAD"))
    temp_repo.refs.add_if_new(b"HEAD", head_sha)

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_restack_untracked_branch(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack from an untracked branch shows informative message."""
    monkeypatch.chdir(tmp_path)

    # We're on main which has no Shortcake-Parent trailer
    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 0
    assert "not tracked" in result.output
    assert "Nothing to restack" in result.output


def test_cli_continue_nothing_in_progress(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI continue when no restack in progress."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert "No restack in progress" in result.output


def test_cli_abort_nothing_in_progress(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI abort when no restack in progress."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["abort"])

    assert result.exit_code == 1
    assert "No restack in progress" in result.output


def test_cli_abort_success(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI abort restores state."""
    monkeypatch.chdir(tmp_path)

    # Store original SHAs
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    # Create state as if restack started
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
        ],
        current_index=0,
        original_refs={
            "branch_a": original_a.decode(),
            "branch_b": original_b.decode(),
        },
    )
    state.save(repo_with_stack_behind)

    result = runner.invoke(app, ["abort"])

    assert result.exit_code == 0
    assert "aborted" in result.output.lower()

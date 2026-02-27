"""Tests for restack git helper functions."""

import re
from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_get_merge_base(repo_with_stack: Repo) -> None:
    """Test merge base calculation."""
    main_sha = git.get_branch_head(repo_with_stack, "main")
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    merge_base = git.get_merge_base(repo_with_stack, main_sha, branch_a_sha)
    assert merge_base == main_sha


def test_is_rebase_in_progress_false(temp_repo: Repo) -> None:
    """Test no rebase in progress."""
    assert not git.is_rebase_in_progress(temp_repo)


def test_has_uncommitted_changes_false(temp_repo: Repo) -> None:
    """Test no uncommitted changes."""
    assert not git.has_uncommitted_changes(temp_repo)


def test_has_uncommitted_changes_staged(temp_repo: Repo, tmp_path: Path) -> None:
    """Test staged changes detected."""
    test_file = tmp_path / "new.txt"
    test_file.write_text("content")
    porcelain.add(temp_repo, paths=[str(test_file)])

    assert git.has_uncommitted_changes(temp_repo)


def test_has_uncommitted_changes_unstaged(temp_repo: Repo, tmp_path: Path) -> None:
    """Test unstaged changes detected."""
    # Modify the README that's already tracked
    readme = tmp_path / "README.md"
    readme.write_text("modified content")

    assert git.has_uncommitted_changes(temp_repo)


def test_is_ancestor_true(repo_with_stack: Repo) -> None:
    """Test is_ancestor when true."""
    main_sha = git.get_branch_head(repo_with_stack, "main")
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    assert git.is_ancestor(repo_with_stack, main_sha, branch_a_sha)


def test_is_ancestor_false(repo_with_stack: Repo) -> None:
    """Test is_ancestor when false."""
    main_sha = git.get_branch_head(repo_with_stack, "main")
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    assert not git.is_ancestor(repo_with_stack, branch_a_sha, main_sha)


def test_is_ancestor_same_commit(repo_with_stack: Repo) -> None:
    """Test is_ancestor for same commit."""
    main_sha = git.get_branch_head(repo_with_stack, "main")

    assert git.is_ancestor(repo_with_stack, main_sha, main_sha)


def test_get_remote_ref_nonexistent(temp_repo: Repo) -> None:
    """Test getting nonexistent remote ref."""
    assert git.get_remote_ref(temp_repo, "origin/nonexistent") is None

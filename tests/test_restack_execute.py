"""Tests for restack execution."""

import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState
from shortcake.commands.restack import (
    RestackError,
    _needs_restack,
    _restack,
)
from tests._git_helpers import Repo, add_paths, get_ref, set_ref

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
    head_sha = get_ref(temp_repo, "refs/heads/main")
    # Remove symbolic ref and set HEAD directly to SHA
    set_ref(temp_repo, "HEAD", head_sha)

    with pytest.raises(RestackError, match="detached HEAD"):
        _restack(temp_repo)


def test_restack_uncommitted_changes(
    repo_with_stack_behind: Repo, tmp_path: Path
) -> None:
    """Error with uncommitted changes."""
    # Create uncommitted change
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    add_paths(repo_with_stack_behind, test_file)

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


def test_restack_after_parent_amend_preserves_content(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Regression test: restack after amending parent branch preserves child content.

    This tests the bug where amending a parent branch (e.g., via `sc modify`)
    caused the child branch to lose its commits during restack.

    The issue was that git merge-base returned an ancestor too far back in
    history, causing restack to try to cherry-pick both the old parent commit
    AND the child's commits.

    See: https://github.com/patrick91/shortcake/commit/7d8c7d7
    """
    # repo_with_stack has: main → branch_a → branch_b
    # branch_a has a.txt, branch_b has b.txt

    # Record original content
    a_commit = repo_with_stack.get(
        git.get_branch_head(repo_with_stack, "branch_a").decode()
    )
    original_a_tree_id = a_commit.tree_id
    b_commit = repo_with_stack.get(
        git.get_branch_head(repo_with_stack, "branch_b").decode()
    )
    original_b_tree_id = b_commit.tree_id

    # Verify initial state - branch_b should have both a.txt and b.txt
    branch_b_tree = repo_with_stack.get(str(original_b_tree_id))
    file_names = [item.name for item in branch_b_tree]
    assert "a.txt" in file_names
    assert "b.txt" in file_names

    # Switch to branch_a and amend it (simulating `sc modify`)
    git.switch_branch(repo_with_stack, "branch_a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content\nmodified content")
    add_paths(repo_with_stack, file_a)
    git.amend_commit(repo_with_stack, "feat: branch a (amended)")

    # Verify branch_a was actually modified
    new_a_commit = repo_with_stack.get(
        git.get_branch_head(repo_with_stack, "branch_a").decode()
    )
    assert new_a_commit.tree_id != original_a_tree_id

    # Switch to branch_b and run restack
    # Use git reset --hard to ensure clean working tree after pygit2 index operations
    subprocess.run(
        ["git", "checkout", "branch_b"],
        cwd=repo_with_stack.workdir,
        capture_output=True,
    )
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=repo_with_stack.workdir,
        capture_output=True,
    )
    result = _restack(repo_with_stack)

    # Should restack only branch_b (branch_a doesn't need rebasing)
    assert result.restacked_branches == ["branch_b"]
    assert result.conflict_branch is None

    # CRITICAL: Verify branch_b still has its own content (b.txt)
    new_b_head = git.get_branch_head(repo_with_stack, "branch_b")
    new_b_commit = repo_with_stack.get(new_b_head.decode())
    new_b_tree = repo_with_stack.get(str(new_b_commit.tree_id))
    new_file_names = [item.name for item in new_b_tree]
    assert "b.txt" in new_file_names, "branch_b should still have b.txt after restack"
    assert "a.txt" in new_file_names, "branch_b should have a.txt from parent"

    # Verify b.txt content is preserved
    b_txt_oid = None
    for item in new_b_tree:
        if item.name == "b.txt":
            b_txt_oid = item.id
            break
    assert b_txt_oid is not None
    assert repo_with_stack.get(str(b_txt_oid)).data == b"branch b content"

    # Verify a.txt has the amended content
    a_txt_oid = None
    for item in new_b_tree:
        if item.name == "a.txt":
            a_txt_oid = item.id
            break
    assert a_txt_oid is not None
    assert b"modified content" in repo_with_stack.get(str(a_txt_oid)).data

    # Verify only 1 commit on branch_b since branch_a (not 2)
    branch_a_head = git.get_branch_head(repo_with_stack, "branch_a")
    commits_on_b = git.get_commits_between(repo_with_stack, new_b_head, branch_a_head)
    assert len(commits_on_b) == 1, (
        f"branch_b should have exactly 1 commit since branch_a, got {len(commits_on_b)}"
    )

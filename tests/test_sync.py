"""Tests for sync command."""

from pathlib import Path
from unittest.mock import patch

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.restack import RestackResult
from shortcake.commands.sync import (
    SyncError,
    SyncResult,
    _get_merged_branches,
    _get_tracked_branches,
    _is_merged,
    _reparent_branch,
    _sync,
    _topological_sort_for_deletion,
)

runner = CliRunner()


# Unit tests for helper functions


def test_get_tracked_branches(repo_with_stack: Repo) -> None:
    """Test getting tracked branches."""
    tracked = _get_tracked_branches(repo_with_stack)
    assert "branch_a" in tracked
    assert "branch_b" in tracked
    assert "main" not in tracked


def test_get_tracked_branches_no_tracked(temp_repo: Repo) -> None:
    """Test with no tracked branches."""
    tracked = _get_tracked_branches(temp_repo)
    assert tracked == []


def test_is_merged_true(repo_with_merged_branch: Repo) -> None:
    """Test detecting merged branch."""
    assert _is_merged(repo_with_merged_branch, "feature", "main")


def test_is_merged_false(repo_with_tracked_feature: Repo) -> None:
    """Test non-merged branch."""
    assert not _is_merged(repo_with_tracked_feature, "feature", "main")


def test_get_merged_branches(repo_with_merged_branch: Repo) -> None:
    """Test getting merged branches."""
    tracked = _get_tracked_branches(repo_with_merged_branch)
    merged = _get_merged_branches(repo_with_merged_branch, tracked, "main")
    assert "feature" in merged


def test_get_merged_branches_with_children(repo_with_merged_and_children: Repo) -> None:
    """Test getting merged branches when one has children."""
    tracked = _get_tracked_branches(repo_with_merged_and_children)
    merged = _get_merged_branches(repo_with_merged_and_children, tracked, "main")
    assert "branch_a" in merged
    assert "branch_b" not in merged


def test_topological_sort_for_deletion(repo_with_stack: Repo) -> None:
    """Test topological sort puts leaves first."""
    # branch_b is child of branch_a, so should come first
    sorted_branches = _topological_sort_for_deletion(
        repo_with_stack, ["branch_a", "branch_b"]
    )
    assert sorted_branches.index("branch_b") < sorted_branches.index("branch_a")


def test_topological_sort_single_branch(repo_with_tracked_feature: Repo) -> None:
    """Test topological sort with single branch."""
    sorted_branches = _topological_sort_for_deletion(
        repo_with_tracked_feature, ["feature"]
    )
    assert sorted_branches == ["feature"]


def test_reparent_branch(repo_with_merged_and_children: Repo) -> None:
    """Test reparenting a branch to new parent."""
    # branch_b has parent branch_a, reparent to main
    _reparent_branch(repo_with_merged_and_children, "branch_b", "main")

    # Verify trailer was updated
    all_branches = set(git.get_all_local_branches(repo_with_merged_and_children))
    new_parent = git.get_branch_parent(
        repo_with_merged_and_children, "branch_b", all_branches
    )
    assert new_parent == "main"


# Tests for _sync function


def test_sync_no_changes(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test sync when nothing to do."""
    result = _sync(repo_with_stack)
    assert result.deleted_branches == []
    assert result.trunk_updated is False


def test_sync_deletes_merged_branch(
    repo_with_merged_branch: Repo, tmp_path: Path
) -> None:
    """Test sync deletes merged branch with force."""
    # Switch to main first since feature will be deleted
    git.switch_branch(repo_with_merged_branch, "main")

    result = _sync(repo_with_merged_branch, force=True)

    assert "feature" in result.deleted_branches
    assert not git.branch_exists(repo_with_merged_branch, "feature")


def test_sync_dry_run(repo_with_merged_branch: Repo, tmp_path: Path) -> None:
    """Test sync dry run doesn't delete."""
    result = _sync(repo_with_merged_branch, dry_run=True)

    assert result.deleted_branches == []
    # Branch should still exist
    assert git.branch_exists(repo_with_merged_branch, "feature")


def test_sync_reparents_children(
    repo_with_merged_and_children: Repo, tmp_path: Path
) -> None:
    """Test sync reparents children when deleting merged branch."""
    result = _sync(repo_with_merged_and_children, force=True)

    assert "branch_a" in result.deleted_branches
    assert "branch_b" in result.reparented_branches
    assert result.reparented_branches["branch_b"] == "main"

    # branch_b should now have main as parent
    all_branches = set(git.get_all_local_branches(repo_with_merged_and_children))
    new_parent = git.get_branch_parent(
        repo_with_merged_and_children, "branch_b", all_branches
    )
    assert new_parent == "main"


def test_sync_prompt_fn_decline(repo_with_merged_branch: Repo, tmp_path: Path) -> None:
    """Test sync respects user declining deletion."""
    git.switch_branch(repo_with_merged_branch, "main")

    def decline_fn(branch: str, trunk: str) -> bool:
        return False

    result = _sync(repo_with_merged_branch, prompt_fn=decline_fn)

    assert result.deleted_branches == []
    assert git.branch_exists(repo_with_merged_branch, "feature")


def test_sync_prompt_fn_accept(repo_with_merged_branch: Repo, tmp_path: Path) -> None:
    """Test sync respects user accepting deletion."""
    git.switch_branch(repo_with_merged_branch, "main")

    def accept_fn(branch: str, trunk: str) -> bool:
        return True

    result = _sync(repo_with_merged_branch, prompt_fn=accept_fn)

    assert "feature" in result.deleted_branches


def test_sync_error_uncommitted_changes(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test sync fails with uncommitted changes."""
    # Create uncommitted changes
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    porcelain.add(repo_with_stack, paths=[str(test_file)])

    with pytest.raises(SyncError, match="uncommitted changes"):
        _sync(repo_with_stack)


def test_sync_deletes_current_branch_switches_to_trunk(
    repo_with_merged_branch: Repo, tmp_path: Path
) -> None:
    """Test sync switches to trunk when current branch is deleted."""
    # We're on feature which is merged
    assert git.get_current_branch(repo_with_merged_branch) == "feature"

    result = _sync(repo_with_merged_branch, force=True)

    assert "feature" in result.deleted_branches
    assert git.get_current_branch(repo_with_merged_branch) == "main"


def test_sync_chain_deletion(temp_repo: Repo, tmp_path: Path) -> None:
    """Test sync deletes chain of merged branches in correct order."""
    # Create chain: main → branch_a → branch_b, both merged
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: b").encode())
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # Merge both into main (fast-forward to branch_b)
    temp_repo.refs[b"refs/heads/main"] = branch_b_sha

    # Add commit to main so it's ahead
    porcelain.switch(temp_repo, "main")
    main_file = tmp_path / "main_after_merge.txt"
    main_file.write_text("main after merge")
    porcelain.add(temp_repo, paths=[str(main_file)])
    porcelain.commit(temp_repo, message=b"chore: post-merge commit")

    result = _sync(temp_repo, force=True)

    # Both should be deleted
    assert "branch_a" in result.deleted_branches
    assert "branch_b" in result.deleted_branches
    # branch_b should be deleted before branch_a (leaf first)
    assert result.deleted_branches.index("branch_b") < result.deleted_branches.index(
        "branch_a"
    )


# CLI tests


def test_cli_sync_nothing_to_do(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync when nothing to do."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert "Everything up to date" in result.output


def test_cli_sync_dry_run(
    repo_with_merged_branch: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync --dry-run."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["sync", "--dry-run"])

    assert result.exit_code == 0
    assert "Would delete" in result.output


def test_cli_sync_force_deletes(
    repo_with_merged_branch: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync --force deletes merged branches."""
    monkeypatch.chdir(tmp_path)
    git.switch_branch(repo_with_merged_branch, "main")

    result = runner.invoke(app, ["sync", "--force"])

    assert result.exit_code == 0
    assert "Deleted branch feature" in result.output


def test_cli_sync_uncommitted_changes(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync fails with uncommitted changes."""
    monkeypatch.chdir(tmp_path)
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    porcelain.add(repo_with_stack, paths=[str(test_file)])

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1
    assert "uncommitted changes" in result.output


def test_cli_sync_user_declines(
    repo_with_merged_branch: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync when user declines deletion."""
    monkeypatch.chdir(tmp_path)
    git.switch_branch(repo_with_merged_branch, "main")

    result = runner.invoke(app, ["sync"], input="n\n")

    assert result.exit_code == 0
    # Branch should still exist
    assert git.branch_exists(repo_with_merged_branch, "feature")


def test_cli_sync_user_accepts(
    repo_with_merged_branch: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync when user accepts deletion."""
    monkeypatch.chdir(tmp_path)
    git.switch_branch(repo_with_merged_branch, "main")

    result = runner.invoke(app, ["sync"], input="y\n")

    assert result.exit_code == 0
    assert "Deleted branch feature" in result.output


# Additional tests for coverage


def test_sync_error_rebase_in_progress(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test sync fails when rebase is in progress."""
    # Create CHERRY_PICK_HEAD to simulate rebase in progress
    cherry_pick_path = tmp_path / ".git" / "CHERRY_PICK_HEAD"
    cherry_pick_path.write_text("abc123")

    with pytest.raises(SyncError, match="rebase in progress"):
        _sync(repo_with_stack)


def test_sync_error_no_default_branch(tmp_path: Path) -> None:
    """Test sync fails when no default branch can be determined."""
    # Create repo with non-standard branch name
    repo = Repo.init(tmp_path, default_branch=b"develop")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    with pytest.raises(SyncError, match="Cannot determine default branch"):
        _sync(repo)


def test_reparent_branch_untracked(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _reparent_branch does nothing for untracked branch."""
    # Create an untracked feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add commit without trailer
    file_a = tmp_path / "feature.txt"
    file_a.write_text("feature")
    porcelain.add(temp_repo, paths=[str(file_a)])
    porcelain.commit(temp_repo, message=b"feat: untracked feature")

    # This should do nothing since branch is untracked
    _reparent_branch(temp_repo, "feature", "main")

    # Branch should still exist and be unchanged
    assert git.branch_exists(temp_repo, "feature")


def test_reparent_branch_no_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _reparent_branch when branch has no commits relative to parent."""
    # Create tracked feature branch at same commit as main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha

    # Create a commit with trailer but no file changes
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: empty feature")

    # Create a file so we have a commit
    file_a = tmp_path / "feature.txt"
    file_a.write_text("content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    porcelain.commit(temp_repo, message=message.encode())

    # Now fast-forward main to feature so they're at the same commit
    feature_sha = temp_repo.refs[b"refs/heads/feature"]
    temp_repo.refs[b"refs/heads/main"] = feature_sha

    # Reparent should handle this gracefully (no commits between them now)
    # This tests the "if not commits: return" branch
    _reparent_branch(temp_repo, "feature", "main")


def test_sync_with_restack_needed(
    repo_with_merged_and_children: Repo, tmp_path: Path
) -> None:
    """Test sync triggers restack when current branch needs it."""
    # After deleting branch_a, branch_b is reparented to main and may need restack
    result = _sync(repo_with_merged_and_children, force=True)

    assert "branch_a" in result.deleted_branches
    assert "branch_b" in result.reparented_branches
    # branch_b should be restacked if needed
    assert result.restack_result is not None


def test_cli_sync_with_restack(
    repo_with_merged_and_children: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync shows restack output."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["sync", "--force"])

    assert result.exit_code == 0
    assert "Deleted branch branch_a" in result.output
    assert "Reparented branch_b to main" in result.output


def test_sync_fetch_failure(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test sync handles fetch failure gracefully."""
    monkeypatch.chdir(tmp_path)

    # Mock fetch_and_fast_forward_trunk to return failure
    with patch.object(
        git, "fetch_and_fast_forward_trunk", return_value=(False, None)
    ):
        result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert "Warning: Could not fast-forward" in result.output


def test_sync_fetch_success_with_update(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test sync shows update message when trunk is fast-forwarded."""
    monkeypatch.chdir(tmp_path)

    # Mock fetch_and_fast_forward_trunk to return success with new SHA
    with patch.object(
        git, "fetch_and_fast_forward_trunk", return_value=(True, "abc1234")
    ):
        result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert "fast-forwarded to abc1234" in result.output


def test_sync_restack_output(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test sync shows restack output when branches are restacked."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    # Should show restack messages
    assert "Restacked" in result.output or "Rebasing" in result.output


def test_cli_sync_conflict_exit(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync exits with code 1 when restack has conflict."""
    monkeypatch.chdir(tmp_path)

    # Create a mock result with conflict
    mock_result = SyncResult(
        trunk_updated=False,
        restack_result=RestackResult(
            restacked_branches=[], conflict_branch="branch_a"
        ),
    )

    with patch(
        "shortcake.commands.sync._sync", return_value=mock_result
    ):
        result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1


def test_reparent_branch_same_commit(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _reparent_branch returns early when no commits to replay."""
    # Create two branches pointing to same commit with feature having a trailer
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add commit to feature
    file_a = tmp_path / "feature.txt"
    file_a.write_text("content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers.apply_to("feat: feature").encode())

    # Create a "child" branch that points to the same commit as its "parent"
    feature_sha = temp_repo.refs[b"refs/heads/feature"]
    temp_repo.refs[b"refs/heads/child"] = feature_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child")

    # Add trailer to child pointing to feature
    child_file = tmp_path / "child.txt"
    child_file.write_text("child content")
    porcelain.add(temp_repo, paths=[str(child_file)])
    child_trailers = Trailers(parent_branch="feature")
    porcelain.commit(
        temp_repo, message=child_trailers.apply_to("feat: child").encode()
    )

    # Fast-forward feature to child's commit so they're the same
    child_sha = temp_repo.refs[b"refs/heads/child"]
    temp_repo.refs[b"refs/heads/feature"] = child_sha

    # Now try to reparent child to main - there are no commits between
    # child and feature (they're the same), so this should return early
    _reparent_branch(temp_repo, "child", "main")

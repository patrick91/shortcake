"""Tests for sync command."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._git._stack import (
    get_merged_branches,
    get_tracked_branches,
    is_merged,
    is_squash_merged,
)
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.restack import RestackResult
from shortcake.commands.sync import (
    SyncError,
    SyncResult,
    _delete_and_reparent,
    _detect_github_stale_branches,
    _GitHubBranchStatus,
    _reparent_branch,
    _resolve_deleted_parent,
    _sync,
    _topological_sort_for_deletion,
)
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    get_ref,
    init_repo,
    remove_paths,
    reset_hard,
    run_git,
    set_ref,
    switch_branch,
)

runner = CliRunner()


# Unit tests for helper functions


def test_get_tracked_branches(repo_with_stack: Repo) -> None:
    """Test getting tracked branches."""
    tracked = get_tracked_branches(repo_with_stack)
    assert "branch_a" in tracked
    assert "branch_b" in tracked
    assert "main" not in tracked


def test_get_tracked_branches_no_tracked(temp_repo: Repo) -> None:
    """Test with no tracked branches."""
    tracked = get_tracked_branches(temp_repo)
    assert tracked == []


def test_is_merged_true(repo_with_merged_branch: Repo) -> None:
    """Test detecting merged branch."""
    assert is_merged(repo_with_merged_branch, "feature", "main")


def test_is_merged_false(repo_with_tracked_feature: Repo) -> None:
    """Test non-merged branch."""
    assert not is_merged(repo_with_tracked_feature, "feature", "main")


def test_is_squash_merged_true(temp_repo: Repo, tmp_path: Path) -> None:
    """Test detecting squash-merged branch.

    Simulates a squash merge by manually copying the branch's tree changes
    to main, without making the branch an ancestor of main.
    """
    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Add a commit on feature
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    # Simulate squash merge: add same file to main directly
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    # Create same file with same content on main
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    commit(temp_repo, b"squash: add feature")

    # Branch is NOT an ancestor of main (not regular merged)
    assert not is_merged(temp_repo, "feature", "main")
    # But tree changes ARE in main (squash merged)
    assert is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_false(repo_with_tracked_feature: Repo) -> None:
    """Test non-squash-merged branch."""
    assert not is_squash_merged(repo_with_tracked_feature, "feature", "main")


def test_is_squash_merged_branch_no_changes(temp_repo: Repo, tmp_path: Path) -> None:
    """Test is_squash_merged when branch tree equals merge base (no changes)."""
    # Create feature branch at same commit as main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)

    # Branch has no changes - tree equals merge base
    assert is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_with_deletion(temp_repo: Repo, tmp_path: Path) -> None:
    """Test is_squash_merged detects deleted files that are also deleted on trunk."""
    # Create a file on main first
    delete_me = tmp_path / "delete_me.txt"
    delete_me.write_text("will be deleted")
    add_paths(temp_repo, delete_me)
    commit(temp_repo, b"Add file to delete")

    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")
    reset_hard(temp_repo)

    # Delete the file on feature branch
    delete_me.unlink()
    remove_paths(temp_repo, delete_me)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: delete file")
    commit(temp_repo, message)

    # Simulate squash merge: delete same file on main
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    delete_me.unlink()
    remove_paths(temp_repo, delete_me)
    commit(temp_repo, b"squash: delete file")

    # Should detect as squash-merged
    assert is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_with_extra_trunk_changes(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test squash-merge detection when trunk has additional changes.

    This tests the case where branch changes are a subset of trunk changes.
    """
    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Add a commit on feature
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    # Simulate squash merge with extra changes:
    # Add same file to main PLUS an additional file
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    # Create same file with same content on main
    feature_file.write_text("feature content")
    extra_file = tmp_path / "extra.txt"
    extra_file.write_text("extra content")
    add_paths(temp_repo, feature_file, extra_file)
    commit(temp_repo, b"squash: add feature plus extra")

    # Branch is NOT an ancestor of main (not regular merged)
    assert not is_merged(temp_repo, "feature", "main")
    # But tree changes ARE in main (squash merged)
    # Trees are different (main has extra.txt) but feature changes are present
    assert is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_with_extra_trunk_deletions(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test squash-merge detection when trunk has additional deletions.

    Regression test: when trunk has file deletions that the branch doesn't have,
    the code must handle TreeChange objects where change.new is None.
    Also tests that branch deletions are properly tracked.
    """
    # Create two files on main - one will be deleted by branch, one by trunk only
    branch_deletes = tmp_path / "branch_deletes.txt"
    branch_deletes.write_text("branch will delete this")
    trunk_deletes = tmp_path / "trunk_deletes.txt"
    trunk_deletes.write_text("only trunk will delete this")
    add_paths(temp_repo, branch_deletes, trunk_deletes)
    commit(temp_repo, b"Add files to delete later")

    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # On feature: delete one file AND add a new file
    branch_deletes.unlink()
    remove_paths(temp_repo, branch_deletes)
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: delete file and add feature")
    commit(temp_repo, message)

    # Simulate squash merge with extra deletion:
    # Apply same changes as branch (delete + add) AND delete another file
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    # Delete same file branch deleted
    branch_deletes.unlink()
    remove_paths(temp_repo, branch_deletes)
    # Add same file branch added
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    # Delete extra file that branch didn't touch
    trunk_deletes.unlink()
    remove_paths(temp_repo, trunk_deletes)
    commit(temp_repo, b"squash: apply branch changes plus extra delete")

    # Branch is NOT an ancestor of main (not regular merged)
    assert not is_merged(temp_repo, "feature", "main")
    # But tree changes ARE in main (squash merged)
    # Trunk has extra deletion, but all of feature's changes are present
    assert is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_trunk_modified_same_files_further(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test squash-merge detection when trunk further modifies the same files.

    Regression test: after a squash merge, trunk may have additional commits
    that modify the exact same files the branch changed. The file SHAs will
    differ, but the branch should still be detected as merged.
    """
    # Create a file on main that will be modified by both branch and trunk
    shared_file = tmp_path / "shared.txt"
    shared_file.write_text("original content")
    add_paths(temp_repo, shared_file)
    commit(temp_repo, b"Add shared file")

    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Modify the shared file on feature
    shared_file.write_text("modified by feature")
    add_paths(temp_repo, shared_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: modify shared file")
    commit(temp_repo, message)

    # Simulate squash merge into main, then additional changes to same file
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    # Apply branch's changes (simulating squash merge)
    shared_file.write_text("modified by feature")
    add_paths(temp_repo, shared_file)
    commit(temp_repo, b"squash: modify shared file")
    # Then make additional changes to the SAME file
    shared_file.write_text("modified by feature, then modified again on trunk")
    add_paths(temp_repo, shared_file)
    commit(temp_repo, b"chore: further modifications")

    # Branch is NOT an ancestor of main
    assert not is_merged(temp_repo, "feature", "main")
    # Should still be detected as squash-merged despite different file SHAs
    assert is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_false_positive_independent_changes(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test that independent changes to same files are NOT detected as squash-merged.

    Regression test: if trunk independently modifies the same files as a branch
    (without actually merging the branch), is_squash_merged should return False.
    """
    # Create a shared file on main
    shared_file = tmp_path / "shared.txt"
    shared_file.write_text("original content")
    add_paths(temp_repo, shared_file)
    commit(temp_repo, b"Add shared file")

    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Modify shared file on feature
    shared_file.write_text("modified by feature")
    add_paths(temp_repo, shared_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: modify shared file")
    commit(temp_repo, message)

    # Independently modify same file on main with DIFFERENT content
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    shared_file.write_text("independently modified on main")
    add_paths(temp_repo, shared_file)
    commit(temp_repo, b"chore: independent change to shared file")

    # Branch is NOT an ancestor of main
    assert not is_merged(temp_repo, "feature", "main")
    # Should NOT be detected as squash-merged (different content, not a merge)
    assert not is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_deletion_not_applied(temp_repo: Repo, tmp_path: Path) -> None:
    """Test is_squash_merged returns False when branch deletes a file but trunk doesn't.

    Covers the case where tree_lookup_path finds the file still exists on a
    trunk commit, meaning the deletion was not applied.
    """
    # Create a file on main
    to_delete = tmp_path / "to_delete.txt"
    to_delete.write_text("will be deleted by branch")
    add_paths(temp_repo, to_delete)
    commit(temp_repo, b"Add file to delete")

    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Delete the file on feature branch
    to_delete.unlink()
    remove_paths(temp_repo, to_delete)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: delete file")
    commit(temp_repo, message)

    # On main, modify the file instead of deleting it
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    to_delete.write_text("modified on trunk, not deleted")
    add_paths(temp_repo, to_delete)
    commit(temp_repo, b"chore: modify file on trunk")

    # Should NOT be detected as squash-merged (branch deleted file, trunk didn't)
    assert not is_squash_merged(temp_repo, "feature", "main")


def test_is_squash_merged_no_common_ancestor(tmp_path: Path) -> None:
    """Test is_squash_merged returns False when branches have no common ancestor."""
    # Create a repo with two unrelated branches
    repo = init_repo(tmp_path, default_branch="main")

    # First commit on main
    file1 = tmp_path / "main.txt"
    file1.write_text("main content")
    add_paths(repo, file1)
    commit(repo, b"Initial commit on main")

    # Create an orphan branch with unrelated history.
    run_git(repo, "checkout", "--orphan", "orphan")
    if file1.exists():
        file1.unlink()
    orphan_file = tmp_path / "orphan.txt"
    orphan_file.write_text("orphan content")
    run_git(repo, "add", "-A")
    commit(repo, b"Orphan commit")

    # Branches have no common ancestor
    assert not is_squash_merged(repo, "orphan", "main")


def test_get_merged_branches_detects_squash_merge(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test get_merged_branches detects squash-merged branches."""
    # Create feature branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Add a commit on feature with trailer
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    # Simulate squash merge: add same file to main
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    commit(temp_repo, b"squash: add feature")

    # get_merged_branches should detect it
    tracked = get_tracked_branches(temp_repo)
    merged = get_merged_branches(temp_repo, tracked, "main")
    assert "feature" in merged


def test_get_merged_branches(repo_with_merged_branch: Repo) -> None:
    """Test getting merged branches."""
    tracked = get_tracked_branches(repo_with_merged_branch)
    merged = get_merged_branches(repo_with_merged_branch, tracked, "main")
    assert "feature" in merged


def test_get_merged_branches_with_children(repo_with_merged_and_children: Repo) -> None:
    """Test getting merged branches when one has children."""
    tracked = get_tracked_branches(repo_with_merged_and_children)
    merged = get_merged_branches(repo_with_merged_and_children, tracked, "main")
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


def test_reparent_branch_when_parent_diverged(temp_repo: Repo, tmp_path: Path) -> None:
    """Test reparenting when the parent branch was rebased/diverged.

    Regression test: if branch_a was rebased (its head changed), and branch_b
    still has commits based on the OLD branch_a, _reparent_branch should still
    correctly update branch_b's trailer.
    """
    # Create branch_a from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")

    # Commit on branch_a
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    commit(temp_repo, trailers_a.apply_to("feat: branch a"))
    old_branch_a_sha = get_ref(temp_repo, "refs/heads/branch_a")

    # Create branch_b from branch_a
    set_ref(temp_repo, "refs/heads/branch_b", old_branch_a_sha)
    temp_repo.set_head("refs/heads/branch_b")

    # Commit on branch_b
    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    commit(temp_repo, trailers_b.apply_to("feat: branch b"))

    # Now simulate branch_a being rebased (new commit with different SHA)
    temp_repo.set_head("refs/heads/branch_a")
    reset_hard(temp_repo, treeish=main_sha)
    file_a.write_text("branch a content rebased")
    add_paths(temp_repo, file_a)
    commit(temp_repo, trailers_a.apply_to("feat: branch a rebased").encode())
    # branch_a now has a different head than what branch_b was based on

    temp_repo.set_head("refs/heads/branch_b")
    reset_hard(temp_repo)

    # Reparent branch_b to main (as if branch_a was merged and deleted)
    _reparent_branch(temp_repo, "branch_b", "main")

    # Verify trailer was updated to main (not still branch_a)
    all_branches = set(git.get_all_local_branches(temp_repo))
    new_parent = git.get_branch_parent(temp_repo, "branch_b", all_branches)
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


def test_sync_error_uncommitted_changes(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test sync fails with uncommitted changes."""
    # Create uncommitted changes
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    add_paths(repo_with_stack, test_file)

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
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    commit(temp_repo, trailers_a.apply_to("feat: a"))
    branch_a_sha = get_ref(temp_repo, "refs/heads/branch_a")

    set_ref(temp_repo, "refs/heads/branch_b", branch_a_sha)
    temp_repo.set_head("refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    commit(temp_repo, trailers_b.apply_to("feat: b"))
    branch_b_sha = get_ref(temp_repo, "refs/heads/branch_b")

    # Merge both into main (fast-forward to branch_b)
    set_ref(temp_repo, "refs/heads/main", branch_b_sha)

    # Add commit to main so it's ahead
    switch_branch(temp_repo, "main")
    main_file = tmp_path / "main_after_merge.txt"
    main_file.write_text("main after merge")
    add_paths(temp_repo, main_file)
    commit(temp_repo, b"chore: post-merge commit")

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
    """Test CLI sync --yes deletes merged branches."""
    monkeypatch.chdir(tmp_path)
    git.switch_branch(repo_with_merged_branch, "main")

    result = runner.invoke(app, ["sync", "--yes"])

    assert result.exit_code == 0
    assert "Deleted branch feature" in result.output


def test_cli_sync_uncommitted_changes(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync fails with uncommitted changes."""
    monkeypatch.chdir(tmp_path)
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    add_paths(repo_with_stack, test_file)

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


def test_sync_error_rebase_in_progress(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test sync fails when rebase is in progress."""
    # Create CHERRY_PICK_HEAD to simulate rebase in progress
    cherry_pick_path = tmp_path / ".git" / "CHERRY_PICK_HEAD"
    cherry_pick_path.write_text("abc123")

    with pytest.raises(SyncError, match="rebase in progress"):
        _sync(repo_with_stack)


def test_sync_error_no_default_branch(tmp_path: Path) -> None:
    """Test sync fails when no default branch can be determined."""
    # Create repo with non-standard branch name
    repo = init_repo(tmp_path, default_branch="develop")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    with pytest.raises(SyncError, match="Cannot determine default branch"):
        _sync(repo)


def test_reparent_branch_untracked(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _reparent_branch does nothing for untracked branch."""
    # Create an untracked feature branch
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Add commit without trailer
    file_a = tmp_path / "feature.txt"
    file_a.write_text("feature")
    add_paths(temp_repo, file_a)
    commit(temp_repo, b"feat: untracked feature")

    # This should do nothing since branch is untracked
    _reparent_branch(temp_repo, "feature", "main")

    # Branch should still exist and be unchanged
    assert git.branch_exists(temp_repo, "feature")


def test_reparent_branch_orphan_commit(tmp_path: Path) -> None:
    """Test _reparent_branch returns early for orphan commit."""
    repo = init_repo(tmp_path, default_branch="main")

    # Create initial commit on main
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    # Create an orphan branch with a Shortcake-Parent trailer.
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: orphan feature")
    run_git(repo, "checkout", "--orphan", "feature")
    if readme.exists():
        readme.unlink()
    orphan_file = tmp_path / "orphan.txt"
    orphan_file.write_text("orphan content")
    run_git(repo, "add", "-A")
    commit(repo, message)
    feature_sha = get_ref(repo, "refs/heads/feature")

    # Reparent should return early (orphan commit, merge_base is None)
    _reparent_branch(repo, "feature", "main")

    # Branch should still exist and be unchanged
    assert git.branch_exists(repo, "feature")
    assert get_ref(repo, "refs/heads/feature") == feature_sha


def test_reparent_branch_no_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _reparent_branch when branch has no commits relative to parent."""
    # Create tracked feature branch at same commit as main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)

    # Create a commit with trailer but no file changes
    temp_repo.set_head("refs/heads/feature")
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: empty feature")

    # Create a file so we have a commit
    file_a = tmp_path / "feature.txt"
    file_a.write_text("content")
    add_paths(temp_repo, file_a)
    commit(temp_repo, message)

    # Now fast-forward main to feature so they're at the same commit
    feature_sha = get_ref(temp_repo, "refs/heads/feature")
    set_ref(temp_repo, "refs/heads/main", feature_sha)

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

    result = runner.invoke(app, ["sync", "--yes"])

    assert result.exit_code == 0
    assert "Deleted branch branch_a" in result.output
    assert "Reparented branch_b to main" in result.output


def test_sync_fetch_failure(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test sync handles fetch failure gracefully."""
    monkeypatch.chdir(tmp_path)

    # Mock fetch_and_fast_forward_trunk to return failure
    with patch.object(git, "fetch_and_fast_forward_trunk", return_value=(False, None)):
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
        restack_result=RestackResult(restacked_branches=[], conflict_branch="branch_a"),
    )

    with patch("shortcake.commands.sync._sync", return_value=mock_result):
        result = runner.invoke(app, ["sync"])

    assert result.exit_code == 1


def test_reparent_branch_same_commit(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _reparent_branch returns early when no commits to replay."""
    # Create two branches pointing to same commit with feature having a trailer
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Add commit to feature
    file_a = tmp_path / "feature.txt"
    file_a.write_text("content")
    add_paths(temp_repo, file_a)
    trailers = Trailers(parent_branch="main")
    commit(temp_repo, trailers.apply_to("feat: feature"))

    # Create a "child" branch that points to the same commit as its "parent"
    feature_sha = get_ref(temp_repo, "refs/heads/feature")
    set_ref(temp_repo, "refs/heads/child", feature_sha)
    temp_repo.set_head("refs/heads/child")

    # Add trailer to child pointing to feature
    child_file = tmp_path / "child.txt"
    child_file.write_text("child content")
    add_paths(temp_repo, child_file)
    child_trailers = Trailers(parent_branch="feature")
    commit(temp_repo, child_trailers.apply_to("feat: child"))

    # Fast-forward feature to child's commit so they're the same
    child_sha = get_ref(temp_repo, "refs/heads/child")
    set_ref(temp_repo, "refs/heads/feature", child_sha)

    # Now try to reparent child to main - there are no commits between
    # child and feature (they're the same), so this should return early
    _reparent_branch(temp_repo, "child", "main")


def test_reparent_branch_multiple_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _reparent_branch replays all commits when branch has multiple."""
    # Create parent branch
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/parent", main_sha)
    temp_repo.set_head("refs/heads/parent")

    file_p = tmp_path / "parent.txt"
    file_p.write_text("parent content")
    add_paths(temp_repo, file_p)
    parent_trailers = Trailers(parent_branch="main")
    commit(temp_repo, parent_trailers.apply_to("feat: parent").encode())
    parent_sha = get_ref(temp_repo, "refs/heads/parent")

    # Create child branch with TWO commits on top of parent
    set_ref(temp_repo, "refs/heads/child", parent_sha)
    temp_repo.set_head("refs/heads/child")

    file_c1 = tmp_path / "child1.txt"
    file_c1.write_text("child commit 1")
    add_paths(temp_repo, file_c1)
    child_trailers = Trailers(parent_branch="parent")
    commit(temp_repo, child_trailers.apply_to("feat: child commit 1").encode())

    file_c2 = tmp_path / "child2.txt"
    file_c2.write_text("child commit 2")
    add_paths(temp_repo, file_c2)
    commit(temp_repo, b"feat: child commit 2")

    # Reparent child to main (as if parent was deleted)
    _reparent_branch(temp_repo, "child", "main")

    # Verify trailer was updated
    all_branches = set(git.get_all_local_branches(temp_repo))
    new_parent = git.get_branch_parent(temp_repo, "child", all_branches)
    assert new_parent == "main"

    # Verify child still has both commits (check files exist in tree)
    child_head = get_ref(temp_repo, "refs/heads/child")
    child_commit = temp_repo.get(child_head.decode())
    child_tree = temp_repo.get(str(child_commit.tree_id))
    tree_entries = {entry.name for entry in child_tree}
    assert "child1.txt" in tree_entries
    assert "child2.txt" in tree_entries


def test_reparent_branch_does_not_revert_master_changes(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test _reparent_branch properly rebases content onto the new parent.

    Regression test: when a child branch is reparented from a deleted parent
    to the grandparent (e.g., main), the child's tree must be rebased onto
    the new parent. Previously, _reparent_branch only rewrote commit metadata
    (parent pointer + trailer) but preserved the old tree snapshot. This caused
    the reparented branch to silently revert changes that were independently
    added to main after the original stack was created.
    """
    main_sha = get_ref(temp_repo, "refs/heads/main")

    # Create parent branch with a file
    set_ref(temp_repo, "refs/heads/parent", main_sha)
    temp_repo.set_head("refs/heads/parent")
    reset_hard(temp_repo)

    parent_file = tmp_path / "parent.txt"
    parent_file.write_text("parent content")
    add_paths(temp_repo, parent_file)
    parent_trailers = Trailers(parent_branch="main")
    commit(temp_repo, parent_trailers.apply_to("feat: parent").encode())
    parent_sha = get_ref(temp_repo, "refs/heads/parent")

    # Create child branch with its own file
    set_ref(temp_repo, "refs/heads/child", parent_sha)
    temp_repo.set_head("refs/heads/child")
    reset_hard(temp_repo)

    child_file = tmp_path / "child.txt"
    child_file.write_text("child content")
    add_paths(temp_repo, child_file)
    child_trailers = Trailers(parent_branch="parent")
    commit(temp_repo, child_trailers.apply_to("feat: child").encode())

    # Now add a new file to main (simulating another PR being merged)
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)

    main_new_file = tmp_path / "main_new.txt"
    main_new_file.write_text("independently added to main")
    add_paths(temp_repo, main_new_file)
    commit(temp_repo, b"chore: add main_new.txt")

    # Also fast-forward parent into main (simulating parent being merged)
    run_git(temp_repo, "merge", "parent", "--no-edit")

    # Switch to child for the reparent
    temp_repo.set_head("refs/heads/child")
    reset_hard(temp_repo)

    # Reparent child from parent to main
    _reparent_branch(temp_repo, "child", "main")

    # Verify trailer was updated
    all_branches = set(git.get_all_local_branches(temp_repo))
    new_parent = git.get_branch_parent(temp_repo, "child", all_branches)
    assert new_parent == "main"

    # CRITICAL: verify that main_new.txt is in the child's tree.
    # Before the fix, _reparent_branch preserved the old tree (which didn't
    # have main_new.txt), making the reparented branch silently revert it.
    child_head = get_ref(temp_repo, "refs/heads/child")
    child_commit = temp_repo.get(child_head.decode())
    child_tree = temp_repo.get(str(child_commit.tree_id))
    tree_entries = {entry.name for entry in child_tree}
    assert "child.txt" in tree_entries, "Child's own file should be preserved"
    assert "parent.txt" in tree_entries, (
        "Parent's file should be preserved (merged into main)"
    )
    assert "main_new.txt" in tree_entries, (
        "File independently added to main MUST be in the reparented branch's tree. "
        "If missing, the reparented branch would silently revert this file."
    )


# Tests for _detect_github_stale_branches


def test_detect_github_stale_branches_no_token(temp_repo: Repo) -> None:
    """Test returns empty when no GitHub token available."""
    with patch("shortcake.commands.sync.get_github_token", return_value=None):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == []
    assert result.closed == []


def test_detect_github_stale_branches_no_repo_info(temp_repo: Repo) -> None:
    """Test returns empty when repo info not available."""
    with (
        patch("shortcake.commands.sync.get_github_token", return_value="tok"),
        patch("shortcake.commands.sync.get_repo_info", return_value=None),
    ):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == []
    assert result.closed == []


def test_detect_github_stale_branches_finds_merged(temp_repo: Repo) -> None:
    """Test detects branches with merged PRs on GitHub."""
    mock_gh = _make_mock_github_client(
        open_prs={},
        closed_prs={"feature": (123, True)},
    )
    with _patch_github_for_sync(mock_gh):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == ["feature"]
    assert result.closed == []


def test_detect_github_stale_branches_finds_closed(temp_repo: Repo) -> None:
    """Test detects branches with closed (not merged) PRs on GitHub."""
    mock_gh = _make_mock_github_client(
        open_prs={},
        closed_prs={"feature": (456, False)},
    )
    with _patch_github_for_sync(mock_gh):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == []
    assert result.closed == ["feature"]


def test_detect_github_stale_branches_skips_excluded(temp_repo: Repo) -> None:
    """Test skips branches in the exclude list."""
    mock_gh = _make_mock_github_client(
        open_prs={},
        closed_prs={"feature": (123, True)},
    )
    with _patch_github_for_sync(mock_gh):
        result = _detect_github_stale_branches(temp_repo, ["feature"], ["feature"])

    assert result.merged == []
    assert result.closed == []


def test_detect_github_stale_branches_skips_open_prs(temp_repo: Repo) -> None:
    """Test skips branches with open PRs."""
    mock_gh = _make_mock_github_client(
        open_prs={"feature": 789},
        closed_prs={},
    )
    with _patch_github_for_sync(mock_gh):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == []
    assert result.closed == []


def test_detect_github_stale_branches_skips_no_pr(temp_repo: Repo) -> None:
    """Test skips branches with no PR at all."""
    mock_gh = _make_mock_github_client(open_prs={}, closed_prs={})
    with _patch_github_for_sync(mock_gh):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == []
    assert result.closed == []


def test_detect_github_stale_branches_handles_exception(
    temp_repo: Repo,
) -> None:
    """Test handles GitHub client exceptions gracefully."""
    with (
        patch("shortcake.commands.sync.get_github_token", return_value="tok"),
        patch(
            "shortcake.commands.sync.get_repo_info",
            return_value=("owner", "repo"),
        ),
        patch(
            "shortcake.commands.sync.GitHubClient",
            side_effect=Exception("network error"),
        ),
    ):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == []
    assert result.closed == []


def test_detect_github_stale_branches_handles_per_branch_exception(
    temp_repo: Repo,
) -> None:
    """Test handles exceptions on individual branch checks."""
    from unittest.mock import MagicMock

    client = MagicMock()
    client.get_pr_for_branch.side_effect = Exception("API error")
    client.__enter__ = lambda self: client
    client.__exit__ = lambda self, *a: None

    with (
        patch("shortcake.commands.sync.get_github_token", return_value="tok"),
        patch(
            "shortcake.commands.sync.get_repo_info",
            return_value=("owner", "repo"),
        ),
        patch(
            "shortcake.commands.sync.GitHubClient",
            return_value=client,
        ),
    ):
        result = _detect_github_stale_branches(temp_repo, ["feature"], [])

    assert result.merged == []
    assert result.closed == []


# Tests for sync with GitHub-detected branches


def test_sync_deletes_github_merged_branch(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test sync deletes branches detected as merged via GitHub API."""
    git.switch_branch(repo_with_stack, "main")

    github_status = _GitHubBranchStatus(merged=["branch_a"], closed=[])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = _sync(repo_with_stack, force=True)

    assert "branch_a" in result.deleted_branches


def test_sync_deletes_github_closed_branch(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test sync deletes branches detected as closed via GitHub API."""
    git.switch_branch(repo_with_stack, "main")

    github_status = _GitHubBranchStatus(merged=[], closed=["branch_a"])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = _sync(repo_with_stack, force=True)

    assert "branch_a" in result.closed_branches


def test_sync_dry_run_github_merged(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test sync dry run with GitHub-detected merged branches."""
    github_status = _GitHubBranchStatus(merged=["branch_a"], closed=[])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = _sync(repo_with_stack, dry_run=True)

    assert result.deleted_branches == []
    assert git.branch_exists(repo_with_stack, "branch_a")


def test_sync_dry_run_github_closed(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test sync dry run with GitHub-detected closed branches."""
    github_status = _GitHubBranchStatus(merged=[], closed=["branch_a"])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = _sync(repo_with_stack, dry_run=True)

    assert result.closed_branches == []
    assert git.branch_exists(repo_with_stack, "branch_a")


def test_sync_prompt_fn_github_merged(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test sync respects prompt_fn for GitHub-detected merged branches."""
    git.switch_branch(repo_with_stack, "main")

    github_status = _GitHubBranchStatus(merged=["branch_a"], closed=[])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = _sync(
            repo_with_stack,
            prompt_fn=lambda branch, trunk: True,
        )

    assert "branch_a" in result.deleted_branches


def test_sync_prompt_fn_github_closed(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test sync respects prompt_fn for GitHub-detected closed branches."""
    git.switch_branch(repo_with_stack, "main")

    github_status = _GitHubBranchStatus(merged=[], closed=["branch_a"])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = _sync(
            repo_with_stack,
            prompt_fn=lambda branch, reason: True,
        )

    assert "branch_a" in result.closed_branches


def test_cli_sync_github_merged_user_accepts(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync prompts user for GitHub-detected merged branches."""
    monkeypatch.chdir(tmp_path)
    git.switch_branch(repo_with_stack, "main")

    github_status = _GitHubBranchStatus(merged=["branch_a"], closed=[])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = runner.invoke(app, ["sync"], input="y\n")

    assert result.exit_code == 0
    assert "Deleted branch branch_a" in result.output


def test_cli_sync_github_closed_user_accepts(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI sync prompts user for GitHub-detected closed branches."""
    monkeypatch.chdir(tmp_path)
    git.switch_branch(repo_with_stack, "main")

    github_status = _GitHubBranchStatus(merged=[], closed=["branch_a"])
    with patch(
        "shortcake.commands.sync._detect_github_stale_branches",
        return_value=github_status,
    ):
        result = runner.invoke(app, ["sync"], input="y\n")

    assert result.exit_code == 0
    assert "Deleted branch branch_a" in result.output


# Helpers for GitHub mocking


def _make_mock_github_client(
    open_prs: dict[str, int],
    closed_prs: dict[str, tuple[int, bool]],
):
    """Create a mock GitHubClient for testing.

    Args:
        open_prs: Map of branch name to PR number for open PRs.
        closed_prs: Map of branch name to (number, is_merged) for closed PRs.
    """
    from unittest.mock import MagicMock

    from shortcake._github import PRInfo

    client = MagicMock()

    def get_pr_for_branch(branch):
        if branch in open_prs:
            return PRInfo(
                number=open_prs[branch],
                url=f"https://github.com/owner/repo/pull/{open_prs[branch]}",
                base="main",
                title="",
                body="",
                state="open",
                is_draft=False,
            )
        return None

    def get_closed_pr_info(branch):
        if branch in closed_prs:
            return closed_prs[branch]
        return None, False

    client.get_pr_for_branch = get_pr_for_branch
    client.get_closed_pr_info = get_closed_pr_info
    client.__enter__ = lambda self: client
    client.__exit__ = lambda self, *a: None
    return client


def _patch_github_for_sync(mock_client):
    """Context manager to patch GitHub dependencies for sync tests."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with (
            patch("shortcake.commands.sync.get_github_token", return_value="tok"),
            patch(
                "shortcake.commands.sync.get_repo_info",
                return_value=("owner", "repo"),
            ),
            patch(
                "shortcake.commands.sync.GitHubClient",
                return_value=mock_client,
            ),
        ):
            yield

    return _ctx()


# Tests for orphaned parent reparenting


def test_sync_reparents_branch_with_deleted_parent(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test sync reparents a branch whose parent was deleted locally."""
    # Create a branch with trailer pointing to non-existent parent
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="deleted-parent")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    git.switch_branch(temp_repo, "main")

    # Mock _resolve_deleted_parent to return "main"
    with patch(
        "shortcake.commands.sync._resolve_deleted_parent",
        return_value="main",
    ):
        result = _sync(temp_repo, force=True)

    assert result.reparented_branches == {"feature": "main"}
    # Verify the trailer was updated
    all_branches = set(git.get_all_local_branches(temp_repo))
    new_parent = git.get_branch_parent(temp_repo, "feature", all_branches)
    assert new_parent == "main"


def test_sync_reparents_branch_dry_run(temp_repo: Repo, tmp_path: Path) -> None:
    """Test sync dry run shows reparent without executing."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="deleted-parent")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    git.switch_branch(temp_repo, "main")

    with patch(
        "shortcake.commands.sync._resolve_deleted_parent",
        return_value="main",
    ):
        result = _sync(temp_repo, dry_run=True)

    # Dry run should NOT reparent
    assert result.reparented_branches == {}
    # Trailer should still point to deleted-parent
    all_branches = set(git.get_all_local_branches(temp_repo))
    parent = git.get_branch_parent(temp_repo, "feature", all_branches)
    assert parent == "deleted-parent"


def test_sync_skips_reparent_when_resolve_returns_none(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test sync skips reparent when parent can't be resolved."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="deleted-parent")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    git.switch_branch(temp_repo, "main")

    with patch(
        "shortcake.commands.sync._resolve_deleted_parent",
        return_value=None,
    ):
        result = _sync(temp_repo, force=True)

    assert result.reparented_branches == {}


def test_resolve_deleted_parent_returns_merged_base(
    temp_repo: Repo,
) -> None:
    """Test _resolve_deleted_parent returns merged PR base."""
    from unittest.mock import MagicMock

    client = MagicMock()
    client.get_merged_pr_base.return_value = "main"
    client.__enter__ = lambda self: client
    client.__exit__ = lambda self, *a: None

    with _patch_github_for_sync(client):
        result = _resolve_deleted_parent(temp_repo, "deleted-branch")

    assert result == "main"


def test_resolve_deleted_parent_returns_none_no_token(
    temp_repo: Repo,
) -> None:
    """Test _resolve_deleted_parent returns None when no token."""
    with patch("shortcake.commands.sync.get_github_token", return_value=None):
        result = _resolve_deleted_parent(temp_repo, "deleted-branch")

    assert result is None


def test_resolve_deleted_parent_handles_api_error(
    temp_repo: Repo,
) -> None:
    """Test _resolve_deleted_parent handles API errors gracefully."""
    with (
        patch("shortcake.commands.sync.get_github_token", return_value="tok"),
        patch(
            "shortcake.commands.sync.get_repo_info",
            return_value=("owner", "repo"),
        ),
        patch(
            "shortcake.commands.sync.GitHubClient",
            side_effect=Exception("network error"),
        ),
    ):
        result = _resolve_deleted_parent(temp_repo, "deleted-branch")

    assert result is None


# Tests for trunk-not-deleted and grandparent-fallback bugs


def test_sync_never_deletes_trunk(temp_repo: Repo, tmp_path: Path) -> None:
    """Test that sync never offers to delete the trunk branch.

    After ff-merging a tracked branch into main, the merged commit's
    Shortcake-Parent trailer can make main appear "tracked", and
    is_merged(main, main) is trivially true. Sync must filter trunk
    from the merged list.
    """

    def _switch(repo, branch):
        repo.set_head(f"refs/heads/{branch}")
        reset_hard(repo)

    # Create a tracked feature branch
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)
    feature_sha = get_ref(temp_repo, "refs/heads/feature")

    # Fast-forward main to feature (simulates merge)
    _switch(temp_repo, "main")
    set_ref(temp_repo, "refs/heads/main", feature_sha)

    # Add a post-merge commit on main
    _switch(temp_repo, "main")
    post = tmp_path / "post.txt"
    post.write_text("post merge")
    add_paths(temp_repo, post)
    commit(temp_repo, b"chore: post merge")

    # Now main's HEAD has the feature commit with Shortcake-Parent: main
    # in its history. get_tracked_branches may include main.
    # Sync must NOT try to delete main even though is_merged(main, main) is true.
    result = _sync(temp_repo, force=True)

    # main must still exist
    assert git.branch_exists(temp_repo, "main")
    assert "main" not in result.deleted_branches
    # feature should be detected as merged and deleted
    assert "feature" in result.deleted_branches


def test_delete_and_reparent_grandparent_already_deleted(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test _delete_and_reparent falls back to trunk when grandparent was deleted.

    Scenario: main → A → B → C. Both A and B are merged. When deleting B,
    its parent A was already deleted. The grandparent should fall back to trunk.
    """

    def _switch(repo, branch):
        repo.set_head(f"refs/heads/{branch}")
        reset_hard(repo)

    # Create branch_a from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    commit(temp_repo, trailers_a.apply_to("feat: a"))
    a_sha = get_ref(temp_repo, "refs/heads/branch_a")

    # Create branch_b from branch_a
    set_ref(temp_repo, "refs/heads/branch_b", a_sha)
    temp_repo.set_head("refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    commit(temp_repo, trailers_b.apply_to("feat: b"))
    b_sha = get_ref(temp_repo, "refs/heads/branch_b")

    # Create branch_c from branch_b (unmerged, should be reparented)
    set_ref(temp_repo, "refs/heads/branch_c", b_sha)
    temp_repo.set_head("refs/heads/branch_c")

    file_c = tmp_path / "c.txt"
    file_c.write_text("branch c")
    add_paths(temp_repo, file_c)
    trailers_c = Trailers(parent_branch="branch_b")
    commit(temp_repo, trailers_c.apply_to("feat: c"))

    _switch(temp_repo, "main")

    # Delete branch_a first (simulating earlier sync loop iteration)
    git.delete_branch(temp_repo, "branch_a")

    # Now delete branch_b — its parent (branch_a) is already gone
    result = SyncResult(trunk_updated=False)
    skip = {"branch_a", "branch_b"}
    _delete_and_reparent(temp_repo, "branch_b", "main", "main", skip, result)

    # branch_c should be reparented to main (fallback), not branch_a
    assert result.reparented_branches.get("branch_c") == "main"
    # Verify the actual trailer was updated
    all_branches = set(git.get_all_local_branches(temp_repo))
    parent = git.get_branch_parent(temp_repo, "branch_c", all_branches)
    assert parent == "main"

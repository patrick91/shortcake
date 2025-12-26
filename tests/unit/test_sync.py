"""Tests for the sync command."""

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from tests.helpers.git_helpers import add_notes, get_notes

runner = CliRunner()


def test_sync_help():
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "Sync branches after a parent branch has been merged" in result.stdout


def test_sync_no_branches(isolated_git_repo: Path, isolated_config: Path):
    """Test sync when there are no tracked branches."""
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "All branches are up to date - nothing to sync" in result.stdout


def test_sync_all_up_to_date(isolated_git_repo: Path, isolated_config: Path):
    """Test sync when all branches are up to date (nothing merged)."""
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature-1", checkout=True)
    test_file = isolated_git_repo / "feature1.txt"
    test_file.write_text("feature 1")
    git.add_files("feature1.txt")
    git.commit("Add feature 1")

    # Add shortcake tracking
    notes_data = {"parent": "main"}
    add_notes(isolated_git_repo, json.dumps(notes_data), "feature-1")

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "All branches are up to date" in result.stdout


def test_sync_dry_run_shows_plan(isolated_git_repo: Path, isolated_config: Path):
    """Test sync --dry-run shows what would happen."""
    git = GitRepo()

    # Create parent branch
    git.create_branch("feature-parent", checkout=True)
    test_file1 = isolated_git_repo / "parent.txt"
    test_file1.write_text("parent feature")
    git.add_files("parent.txt")
    git.commit("Add parent feature")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-parent")

    # Create child branch
    git.create_branch("feature-child", checkout=True)
    test_file2 = isolated_git_repo / "child.txt"
    test_file2.write_text("child feature")
    git.add_files("child.txt")
    git.commit("Add child feature")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-parent"}), "feature-child")

    # Simulate merging parent into main by making parent an ancestor of main
    # In real scenario, GitHub merge would update main
    # For testing, we'll merge feature-parent into main
    git.checkout_branch("main")
    git.repo.git.merge("feature-parent", "--no-ff", "-m", "Merge feature-parent")

    # Now feature-parent is merged into main
    result = runner.invoke(app, ["sync", "--dry-run"])
    assert result.exit_code == 0
    assert "Detected merged branches" in result.stdout
    assert "feature-parent" in result.stdout


def test_sync_rebases_child_after_parent_merged(isolated_git_repo: Path, isolated_config: Path):
    """Test that sync rebases child branch after parent is merged."""
    git = GitRepo()

    # Create parent branch with a commit
    git.create_branch("feature-parent", checkout=True)
    test_file1 = isolated_git_repo / "parent.txt"
    test_file1.write_text("parent feature")
    git.add_files("parent.txt")
    git.commit("Add parent feature")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-parent")

    # Create child branch with its own commit
    git.create_branch("feature-child", checkout=True)
    test_file2 = isolated_git_repo / "child.txt"
    test_file2.write_text("child feature")
    git.add_files("child.txt")
    git.commit("Add child feature")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-parent"}), "feature-child")

    # Merge parent into main (simulating GitHub merge)
    git.checkout_branch("main")
    git.repo.git.merge("feature-parent", "--no-ff", "-m", "Merge feature-parent")

    # Run sync
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "Rebasing branches" in result.stdout
    assert "feature-child" in result.stdout
    assert "Sync complete" in result.stdout

    # Verify child's parent was updated to main
    notes = get_notes(isolated_git_repo, "feature-child")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "main"

    # Verify parent branch was deleted
    assert not git.branch_exists("feature-parent")


def test_sync_handles_deep_stack(isolated_git_repo: Path, isolated_config: Path):
    """Test sync with a 3-level deep stack where the root is merged."""
    git = GitRepo()

    # Create level 1: feature-1 off main
    git.create_branch("feature-1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files("f1.txt")
    git.commit("Add f1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-1")

    # Create level 2: feature-2 off feature-1
    git.create_branch("feature-2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2")
    git.add_files("f2.txt")
    git.commit("Add f2")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-1"}), "feature-2")

    # Create level 3: feature-3 off feature-2
    git.create_branch("feature-3", checkout=True)
    (isolated_git_repo / "f3.txt").write_text("f3")
    git.add_files("f3.txt")
    git.commit("Add f3")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-2"}), "feature-3")

    # Merge feature-1 into main
    git.checkout_branch("main")
    git.repo.git.merge("feature-1", "--no-ff", "-m", "Merge feature-1")

    # Run sync
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0

    # feature-1 should be deleted
    assert not git.branch_exists("feature-1")

    # feature-2's parent should now be main
    notes = get_notes(isolated_git_repo, "feature-2")
    assert notes is not None
    assert json.loads(notes).get("parent") == "main"

    # feature-3's parent should still be feature-2
    notes = get_notes(isolated_git_repo, "feature-3")
    assert notes is not None
    assert json.loads(notes).get("parent") == "feature-2"


def test_sync_abort_no_rebase_in_progress(isolated_git_repo: Path, isolated_config: Path):
    """Test sync --abort when no rebase is in progress."""
    result = runner.invoke(app, ["sync", "--abort"])
    assert result.exit_code == 1
    assert "No rebase in progress" in result.output


def test_sync_continue_no_rebase_in_progress(isolated_git_repo: Path, isolated_config: Path):
    """Test sync --continue when no rebase is in progress."""
    result = runner.invoke(app, ["sync", "--continue"])
    assert result.exit_code == 1
    assert "No rebase in progress" in result.output


def test_sync_preserves_unrelated_branches(isolated_git_repo: Path, isolated_config: Path):
    """Test that sync doesn't affect branches not in the merged stack."""
    git = GitRepo()

    # Create and track branch-a
    git.create_branch("branch-a", checkout=True)
    (isolated_git_repo / "a.txt").write_text("a")
    git.add_files("a.txt")
    git.commit("Add a")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "branch-a")

    # Create and track branch-b (independent)
    git.checkout_branch("main")
    git.create_branch("branch-b", checkout=True)
    (isolated_git_repo / "b.txt").write_text("b")
    git.add_files("b.txt")
    git.commit("Add b")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "branch-b")

    # Merge branch-a into main
    git.checkout_branch("main")
    git.repo.git.merge("branch-a", "--no-ff", "-m", "Merge branch-a")

    # Run sync
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0

    # branch-a should be deleted (merged)
    assert not git.branch_exists("branch-a")

    # branch-b should be untouched
    assert git.branch_exists("branch-b")
    notes = get_notes(isolated_git_repo, "branch-b")
    assert notes is not None
    assert json.loads(notes).get("parent") == "main"


def test_sync_detects_squash_merge(isolated_git_repo: Path, isolated_config: Path):
    """Test that sync detects branches merged via squash merge."""
    git = GitRepo()

    # Create parent branch with changes
    git.create_branch("feature-parent", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Add feature")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-parent")

    # Create child branch
    git.create_branch("feature-child", checkout=True)
    (isolated_git_repo / "child.txt").write_text("child content")
    git.add_files("child.txt")
    git.commit("Add child")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-parent"}), "feature-child")

    # Simulate squash merge: create a NEW commit on main with the same file content
    git.checkout_branch("main")
    (isolated_git_repo / "feature.txt").write_text("feature content")  # Same content!
    git.add_files("feature.txt")
    git.commit("Squashed: Add feature")  # Different commit, same content

    # Run sync - should detect feature-parent as merged via squash
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0

    # feature-parent should be detected as merged and deleted
    assert not git.branch_exists("feature-parent")

    # feature-child should have its parent updated to main
    notes = get_notes(isolated_git_repo, "feature-child")
    assert notes is not None
    assert json.loads(notes).get("parent") == "main"


def test_sync_squash_merge_with_stack(isolated_git_repo: Path, isolated_config: Path):
    """Test sync with squash merge on a 3-level stack."""
    git = GitRepo()

    # Level 1: feature-1 off main
    git.create_branch("feature-1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1 content")
    git.add_files("f1.txt")
    git.commit("Add f1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-1")

    # Level 2: feature-2 off feature-1
    git.create_branch("feature-2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2 content")
    git.add_files("f2.txt")
    git.commit("Add f2")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-1"}), "feature-2")

    # Level 3: feature-3 off feature-2
    git.create_branch("feature-3", checkout=True)
    (isolated_git_repo / "f3.txt").write_text("f3 content")
    git.add_files("f3.txt")
    git.commit("Add f3")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-2"}), "feature-3")

    # Squash merge feature-1 into main
    git.checkout_branch("main")
    (isolated_git_repo / "f1.txt").write_text("f1 content")
    git.add_files("f1.txt")
    git.commit("Squashed: Add f1")

    # Run sync
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0

    # feature-1 should be deleted (squash merged)
    assert not git.branch_exists("feature-1")

    # feature-2's parent should now be main
    notes = get_notes(isolated_git_repo, "feature-2")
    assert notes is not None
    assert json.loads(notes).get("parent") == "main"

    # feature-3's parent should still be feature-2
    notes = get_notes(isolated_git_repo, "feature-3")
    assert notes is not None
    assert json.loads(notes).get("parent") == "feature-2"


def test_sync_multiple_consecutive_branches_merged(isolated_git_repo: Path, isolated_config: Path):
    """Test sync when multiple consecutive branches in a stack are merged.

    Scenario: main → A → B → C, both A and B are merged.
    Expected: C should be rebased onto main (not onto A which is also merged).
    """
    git = GitRepo()

    # Create A off main
    git.create_branch("branch-a", checkout=True)
    (isolated_git_repo / "a.txt").write_text("a content")
    git.add_files("a.txt")
    git.commit("Add a")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "branch-a")

    # Create B off A
    git.create_branch("branch-b", checkout=True)
    (isolated_git_repo / "b.txt").write_text("b content")
    git.add_files("b.txt")
    git.commit("Add b")
    add_notes(isolated_git_repo, json.dumps({"parent": "branch-a"}), "branch-b")

    # Create C off B
    git.create_branch("branch-c", checkout=True)
    (isolated_git_repo / "c.txt").write_text("c content")
    git.add_files("c.txt")
    git.commit("Add c")
    add_notes(isolated_git_repo, json.dumps({"parent": "branch-b"}), "branch-c")

    # Merge both A and B into main (simulating merging the first two PRs)
    git.checkout_branch("main")
    git.repo.git.merge("branch-a", "--no-ff", "-m", "Merge branch-a")
    git.repo.git.merge("branch-b", "--no-ff", "-m", "Merge branch-b")

    # Run sync
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0

    # Both A and B should be deleted (merged)
    assert not git.branch_exists("branch-a")
    assert not git.branch_exists("branch-b")

    # C should still exist and its parent should be main (not branch-a!)
    assert git.branch_exists("branch-c")
    notes = get_notes(isolated_git_repo, "branch-c")
    assert notes is not None
    assert json.loads(notes).get("parent") == "main"


def test_sync_handles_stale_metadata(isolated_git_repo: Path, isolated_config: Path):
    """Test sync gracefully handles metadata for branches that no longer exist locally."""
    git = GitRepo()

    # Create and track a branch
    git.create_branch("feature-1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files("f1.txt")
    git.commit("Add f1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-1")

    # Delete the branch manually (simulating external deletion)
    git.checkout_branch("main")
    git.delete_branch("feature-1", force=True)

    # Metadata still exists but branch is gone - sync should not crash
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    # Should report all up to date since the only tracked branch doesn't exist locally
    assert "All branches are up to date - nothing to sync" in result.stdout


def test_sync_fast_forwards_main_branch(
    isolated_git_repo: Path, isolated_config: Path, remote_repo: Path
):
    """Test that sync fast-forwards the main branch when it's behind origin."""
    import tempfile

    git = GitRepo()

    # Set up remote and push main
    git.add_remote("origin", str(remote_repo))
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=isolated_git_repo, check=True)

    local_sha_before = git.get_commit_sha("main")

    # Simulate a commit added to main on the remote via another clone
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "clone"
        subprocess.run(["git", "clone", str(remote_repo), str(clone_path)], check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=clone_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=clone_path, check=True)
        subprocess.run(["git", "checkout", "main"], cwd=clone_path, check=True)
        (clone_path / "remote-main-update.txt").write_text("remote main update")
        subprocess.run(["git", "add", "remote-main-update.txt"], cwd=clone_path, check=True)
        subprocess.run(["git", "commit", "-m", "Remote main update"], cwd=clone_path, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=clone_path, check=True)

    # Run sync - should fast-forward the main branch
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "Fast-forwarded main to origin/main" in result.output
    assert "Sync complete" in result.output

    # Verify local main now matches remote
    subprocess.run(["git", "fetch", "origin"], cwd=isolated_git_repo, check=True)
    remote_sha = git.get_commit_sha("origin/main")
    local_sha_after = git.get_commit_sha("main")
    assert local_sha_after == remote_sha
    assert local_sha_after != local_sha_before


def test_sync_fast_forwards_and_updates_metadata(
    isolated_git_repo: Path, isolated_config: Path, remote_repo: Path
):
    """Test that sync fast-forwards branches and updates their metadata."""
    import tempfile

    git = GitRepo()

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Create a tracked branch and push it
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Add feature")

    main_sha = git.get_commit_sha("main")
    add_notes(
        isolated_git_repo,
        json.dumps({"parent": "main", "parent_revision": main_sha}),
        "feature",
    )
    subprocess.run(["git", "push", "-u", "origin", "feature"], cwd=isolated_git_repo, check=True)

    local_sha_before = git.get_commit_sha("feature")

    # Simulate a commit added to the remote branch via another clone
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "clone"
        subprocess.run(["git", "clone", str(remote_repo), str(clone_path)], check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=clone_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=clone_path, check=True)
        subprocess.run(["git", "checkout", "feature"], cwd=clone_path, check=True)
        (clone_path / "remote-update.txt").write_text("remote update")
        subprocess.run(["git", "add", "remote-update.txt"], cwd=clone_path, check=True)
        subprocess.run(["git", "commit", "-m", "Remote update"], cwd=clone_path, check=True)
        subprocess.run(["git", "push", "origin", "feature"], cwd=clone_path, check=True)

    # Run sync - should fast-forward the branch
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "Fast-forwarded 1 branch(es)" in result.output

    # Verify local branch now matches remote
    subprocess.run(["git", "fetch", "origin"], cwd=isolated_git_repo, check=True)
    remote_sha = git.get_commit_sha("origin/feature")
    local_sha_after = git.get_commit_sha("feature")
    assert local_sha_after == remote_sha
    assert local_sha_after != local_sha_before

    # Verify metadata was updated
    notes = get_notes(isolated_git_repo, "feature")
    assert notes is not None
    metadata = json.loads(notes)
    assert metadata.get("parent_revision") == main_sha  # Should still point to main


def test_sync_deletes_branch_checked_out_in_worktree(
    isolated_git_repo: Path, isolated_config: Path, tmp_path: Path
):
    git = GitRepo(isolated_git_repo)

    # Create a feature branch
    git.create_branch("feature-parent", checkout=True)
    (isolated_git_repo / "parent.txt").write_text("parent")
    git.add_files("parent.txt")
    git.commit("Add parent")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-parent")

    # Switch back to main so we can create a worktree for feature-parent
    git.checkout_branch("main")

    # Create a worktree with the feature branch checked out
    worktree_path = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "feature-parent"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Verify branch is in worktree
    assert git.get_worktree_for_branch("feature-parent") == worktree_path

    # Simulate merge: copy content to main
    git.checkout_branch("main")
    (isolated_git_repo / "parent.txt").write_text("parent")
    git.add_files("parent.txt")
    git.commit("Merge feature-parent")

    # Run sync - should switch worktree to main (or detach at main) and delete branch
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "Switched worktree at" in result.output
    assert "Deleted merged branch: feature-parent" in result.output

    # Verify branch was deleted
    assert not git.branch_exists("feature-parent")

    # Verify worktree is now detached (since main was already checked out)
    rev_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=True,
    )
    # HEAD should be detached (returns "HEAD") or on main
    assert rev_result.stdout.strip() in ["HEAD", "main"]

    # Clean up
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

"""Tests for the restack command."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from tests.helpers.git_helpers import add_notes, get_notes

runner = CliRunner()


def test_restack_help():
    result = runner.invoke(app, ["restack", "--help"])
    assert result.exit_code == 0
    assert "Restack branches" in result.stdout


def test_restack_not_in_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_restack_from_main_branch(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 1
    assert "Cannot restack from main/master branch" in result.output


def test_restack_untracked_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()
    git.create_branch("feature", checkout=True)

    # Create a commit on the feature branch
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    git.add_files("test.txt")
    git.commit("Add test file")

    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 1
    assert "not managed by shortcake" in result.output


def test_restack_dry_run(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    git.add_files("test.txt")
    git.commit("Add test file")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), git.get_current_branch())

    result = runner.invoke(app, ["restack", "--dry-run"])
    assert result.exit_code == 0
    assert "Would check" in result.output
    assert "feature" in result.output


def test_restack_single_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    git.add_files("test.txt")
    git.commit("Add test file")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    # Branch is already up-to-date (just created on main), so no rebase needed
    assert "up to date" in result.output or "Restack complete" in result.output

    # Verify metadata is preserved
    notes = get_notes(isolated_git_repo, "feature")
    assert notes is not None
    assert "parent" in notes


def test_restack_preserves_metadata(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch with extra metadata
    git.create_branch("feature", checkout=True)
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    git.add_files("test.txt")
    git.commit("Add test file")

    original_notes = {"parent": "main", "pr_number": 42, "pr_url": "https://example.com/pr/42"}
    add_notes(isolated_git_repo, json.dumps(original_notes), "feature")

    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0

    # Verify all metadata is preserved
    notes = get_notes(isolated_git_repo, "feature")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data["parent"] == "main"
    assert notes_data["pr_number"] == 42
    assert notes_data["pr_url"] == "https://example.com/pr/42"


def test_restack_stacked_branches(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create first branch
    git.create_branch("feature-1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files("f1.txt")
    git.commit("Add feature 1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature-1")

    # Create second branch stacked on first
    git.create_branch("feature-2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2")
    git.add_files("f2.txt")
    git.commit("Add feature 2")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-1"}), "feature-2")

    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "feature-1" in result.output
    assert "feature-2" in result.output
    # Branches are already up-to-date (just created), so no rebase needed
    assert "up to date" in result.output or "Restack complete" in result.output

    # Verify metadata is preserved for both
    notes1 = get_notes(isolated_git_repo, "feature-1")
    notes2 = get_notes(isolated_git_repo, "feature-2")
    assert notes1 is not None
    assert notes2 is not None


def test_restack_includes_descendants(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create first branch
    git.create_branch("feature-1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files("f1.txt")
    git.commit("Add feature 1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), git.get_current_branch())

    # Create second branch stacked on first
    git.create_branch("feature-2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2")
    git.add_files("f2.txt")
    git.commit("Add feature 2")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-1"}), "feature-2")

    # Create third branch stacked on second
    git.create_branch("feature-3", checkout=True)
    (isolated_git_repo / "f3.txt").write_text("f3")
    git.add_files("f3.txt")
    git.commit("Add feature 3")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature-2"}), "feature-3")

    # Go back to feature-1 and restack - should include feature-2 and feature-3
    git.checkout_branch("feature-1")

    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    # feature-1 plus descendants feature-2 and feature-3
    assert "feature-1" in result.output
    assert "feature-2" in result.output
    assert "feature-3" in result.output


def test_restack_abort_no_rebase_in_progress(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    git.add_files("test.txt")
    git.commit("Add test file")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), git.get_current_branch())

    result = runner.invoke(app, ["restack", "--abort"])
    assert result.exit_code == 1
    assert "No rebase in progress" in result.output


def test_restack_continue_no_rebase_in_progress(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    git.add_files("test.txt")
    git.commit("Add test file")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), git.get_current_branch())

    result = runner.invoke(app, ["restack", "--continue"])
    assert result.exit_code == 1
    assert "No rebase in progress" in result.output


def test_restack_after_main_updated(
    isolated_git_repo: Path, isolated_config: Path, remote_repo: Path
):
    """Test restack when main has been updated on remote."""
    git = GitRepo()

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Add feature")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), git.get_current_branch())

    # Simulate main being updated on remote (add a commit to main and push)
    git.checkout_branch("main")
    (isolated_git_repo / "main-update.txt").write_text("main update")
    git.add_files("main-update.txt")
    git.commit("Update main")
    # Push the update to origin so origin/main is updated
    subprocess.run(["git", "push", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Go back to feature
    git.checkout_branch("feature")

    # Restack should rebase feature onto updated origin/main
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "Restack complete" in result.output

    # Verify the branch now has the main update in its history
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert "Update main" in log.stdout
    assert "Add feature" in log.stdout


def test_restack_fast_forwards_branch_behind_remote(
    isolated_git_repo: Path, isolated_config: Path, remote_repo: Path
):
    """Test that restack fast-forwards local branches that are behind their remote counterpart."""
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
        git.get_current_branch(),
    )
    subprocess.run(["git", "push", "-u", "origin", "feature"], cwd=isolated_git_repo, check=True)

    local_sha_before = git.get_commit_sha("feature")

    # Simulate a commit added to the remote branch (e.g., via GitHub UI "Update branch")
    # We do this by adding a commit directly to origin/feature in the bare repo
    # Create a temporary clone to add the commit
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "clone"
        subprocess.run(["git", "clone", str(remote_repo), str(clone_path)], check=True)
        subprocess.run(["git", "checkout", "feature"], cwd=clone_path, check=True)
        (clone_path / "remote-update.txt").write_text("remote update")
        subprocess.run(["git", "add", "remote-update.txt"], cwd=clone_path, check=True)
        subprocess.run(["git", "commit", "-m", "Remote update"], cwd=clone_path, check=True)
        subprocess.run(["git", "push", "origin", "feature"], cwd=clone_path, check=True)

    # Verify local is now behind remote
    subprocess.run(["git", "fetch", "origin"], cwd=isolated_git_repo, check=True)
    remote_sha = git.get_commit_sha("origin/feature")
    assert local_sha_before != remote_sha

    # Run restack - should fast-forward the branch
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "Fast-forwarded feature to match remote" in result.output

    # Verify local branch now matches remote
    local_sha_after = git.get_commit_sha("feature")
    assert local_sha_after == remote_sha

    # Verify the remote commit is in history
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert "Remote update" in log.stdout


def test_restack_detects_commits_merged_via_separate_pr(
    isolated_git_repo: Path, isolated_config: Path, remote_repo: Path
):
    """Test restack detects when commits on branch were merged via a separate PR.

    This tests the scenario where:
    1. Branch A has commits X and Y (created from main at commit M)
    2. Commit X is merged into main via a separate PR (fast-forward)
    3. Branch A's stored parent_revision (M) differs from current main (X)
    4. Restack should detect this and rebase, removing the now-redundant X
    """
    git = GitRepo()

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Record main SHA before any branches
    original_main_sha = git.get_commit_sha("main")

    # Create first feature branch with a commit
    git.create_branch("feature-python38", checkout=True)
    (isolated_git_repo / "python38.txt").write_text("drop python 3.8")
    git.add_files("python38.txt")
    git.commit("Drop support for Python 3.8")
    python38_sha = git.get_current_commit()
    add_notes(
        isolated_git_repo,
        json.dumps({"parent": "main", "parent_revision": original_main_sha}),
        "feature-python38",
    )

    # Create second feature branch stacked on top with another commit
    git.create_branch("feature-pydantic", checkout=True)
    (isolated_git_repo / "pydantic.txt").write_text("drop pydantic v1")
    git.add_files("pydantic.txt")
    git.commit("Drop support for Pydantic v1")
    # Note: This branch tracks main as parent (not feature-python38)
    # The stored parent_revision is the original main SHA (before python38 was added)
    add_notes(
        isolated_git_repo,
        json.dumps({"parent": "main", "parent_revision": original_main_sha}),
        "feature-pydantic",
    )

    # Simulate merging feature-python38 into main (fast-forward merge)
    git.checkout_branch("main")
    git.merge_ff_only("feature-python38")
    subprocess.run(["git", "push", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Verify main is now at python38 commit
    assert git.get_commit_sha("main") == python38_sha

    # Go back to feature-pydantic
    git.checkout_branch("feature-pydantic")

    # The branch history shows 2 commits since original main
    log_from_original = subprocess.run(
        ["git", "log", "--oneline", f"{original_main_sha}..feature-pydantic"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commits_from_original = [line for line in log_from_original.stdout.strip().split("\n") if line]
    assert (
        len(commits_from_original) == 2
    ), f"Expected 2 commits from original, got: {commits_from_original}"

    # But git log main..branch shows only 1 (since main moved to include python38)
    log_from_main = subprocess.run(
        ["git", "log", "--oneline", "main..feature-pydantic"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commits_from_main = [line for line in log_from_main.stdout.strip().split("\n") if line]
    assert len(commits_from_main) == 1, f"Expected 1 commit from main, got: {commits_from_main}"

    # Restack should detect the mismatch between stored parent_revision and current main
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "Rebasing feature-pydantic" in result.output

    # After restack: branch is based on current main, still 1 commit ahead
    log_after = subprocess.run(
        ["git", "log", "--oneline", "main..feature-pydantic"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commits_after = [line for line in log_after.stdout.strip().split("\n") if line]
    assert len(commits_after) == 1, f"Expected 1 commit after rebase, got: {commits_after}"
    assert "Pydantic" in commits_after[0]


def test_restack_detects_squash_merged_commits(
    isolated_git_repo: Path, isolated_config: Path, remote_repo: Path
):
    """Test restack detects when commits on branch were squash-merged into main.

    This tests the scenario where:
    1. Branch A has commits X and Y
    2. Commit X is squash-merged into main (different SHA but same changes)
    3. Branch A should detect X is redundant and rebase to remove it
    """
    git = GitRepo()

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Record main SHA
    original_main_sha = git.get_commit_sha("main")

    # Create branch with two commits
    git.create_branch("feature", checkout=True)

    # First commit
    (isolated_git_repo / "first.txt").write_text("first change")
    git.add_files("first.txt")
    git.commit("First change")

    # Second commit
    (isolated_git_repo / "second.txt").write_text("second change")
    git.add_files("second.txt")
    git.commit("Second change")

    add_notes(
        isolated_git_repo,
        json.dumps({"parent": "main", "parent_revision": original_main_sha}),
        "feature",
    )

    # Simulate squash-merging the first commit into main
    # We do this by cherry-picking with a different message
    git.checkout_branch("main")
    first_commit = subprocess.run(
        ["git", "log", "--format=%H", "-1", "feature~1"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "cherry-pick", first_commit],
        cwd=isolated_git_repo,
        check=True,
    )
    # Amend with different message to simulate squash
    subprocess.run(
        ["git", "commit", "--amend", "-m", "First change (squashed)"],
        cwd=isolated_git_repo,
        check=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Go back to feature
    git.checkout_branch("feature")

    # Restack should detect the redundant commit (by patch equivalence)
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "Rebasing feature" in result.output

    # After restack: first commit should be skipped, only second remains
    log_after = subprocess.run(
        ["git", "log", "--oneline", "main..feature"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commits_after = [line for line in log_after.stdout.strip().split("\n") if line]
    assert len(commits_after) == 1, f"Expected 1 commit after rebase, got: {commits_after}"
    assert "Second change" in commits_after[0]


def test_restack_detects_redundant_commits_without_parent_revision(
    isolated_git_repo: Path, isolated_config: Path, remote_repo: Path
):
    """Test restack detects redundant commits even without stored parent_revision.

    This tests the legacy/fallback case where:
    1. Branch has no stored parent_revision (legacy branch)
    2. Commits on the branch have been cherry-picked/squash-merged into main
    3. The cherry detection should catch this and trigger a restack
    """
    git = GitRepo()

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Create branch with two commits
    git.create_branch("feature", checkout=True)

    # First commit
    (isolated_git_repo / "first.txt").write_text("first change")
    git.add_files("first.txt")
    git.commit("First change")

    # Second commit
    (isolated_git_repo / "second.txt").write_text("second change")
    git.add_files("second.txt")
    git.commit("Second change")

    # Set metadata WITHOUT parent_revision (simulating legacy branch)
    add_notes(
        isolated_git_repo,
        json.dumps({"parent": "main"}),  # No parent_revision!
        "feature",
    )

    # Simulate cherry-picking the first commit into main
    git.checkout_branch("main")
    first_commit = subprocess.run(
        ["git", "log", "--format=%H", "-1", "feature~1"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "cherry-pick", first_commit],
        cwd=isolated_git_repo,
        check=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=isolated_git_repo, check=True)

    # Go back to feature
    git.checkout_branch("feature")

    # Restack should detect the redundant commit via cherry detection
    result = runner.invoke(app, ["restack", "--debug"])
    assert result.exit_code == 0, f"Restack failed: {result.output}"
    # Should detect and rebase (the cherry detection catches this)
    assert (
        "Rebasing feature" in result.output
    ), f"Expected 'Rebasing feature' in output:\n{result.output}"

    # After restack: first commit should be skipped, only second remains
    log_after = subprocess.run(
        ["git", "log", "--oneline", "main..feature"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commits_after = [line for line in log_after.stdout.strip().split("\n") if line]
    assert len(commits_after) == 1, f"Expected 1 commit after rebase, got: {commits_after}"
    assert "Second change" in commits_after[0]

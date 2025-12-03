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

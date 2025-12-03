"""Tests for the split command."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from tests.helpers.git_helpers import add_notes, get_notes

runner = CliRunner()


def test_split_help():
    result = runner.invoke(app, ["split", "--help"])
    assert result.exit_code == 0
    assert "Split a branch into multiple stacked branches" in result.stdout


def test_split_requires_by_hunk_flag(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "test.txt").write_text("content")
    git.add_files("test.txt")
    git.commit("Add feature")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    result = runner.invoke(app, ["split"])
    assert result.exit_code == 1
    assert "Please specify --by-hunk" in result.output


def test_split_not_in_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_split_from_main_branch(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 1
    assert "Cannot split main/master branch" in result.output


def test_split_untracked_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()
    git.create_branch("feature", checkout=True)

    (isolated_git_repo / "test.txt").write_text("content")
    git.add_files("test.txt")
    git.commit("Add feature")

    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 1
    assert "not managed by shortcake" in result.output


def test_split_by_hunk_starts_split(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch with multiple file changes
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content1")
    (isolated_git_repo / "file2.txt").write_text("content2")
    git.add_files(["file1.txt", "file2.txt"])
    git.commit("Add files")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0
    assert "Split started" in result.output
    assert "git add -p" in result.output

    # State file should exist
    state_file = isolated_git_repo / ".git" / "shortcake-split-state.json"
    assert state_file.exists()

    # Changes should be unstaged
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    # Files should show as untracked or modified (not staged)
    assert "file1.txt" in status.stdout
    assert "file2.txt" in status.stdout


def test_split_abort(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content1")
    git.add_files("file1.txt")
    git.commit("Add files")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    original_commit = git.get_current_commit()

    # Start split
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0

    # Abort
    result = runner.invoke(app, ["split", "--abort"])
    assert result.exit_code == 0
    assert "aborted" in result.output.lower()

    # Should be back to original state
    assert git.get_current_commit() == original_commit
    assert git.get_current_branch() == "feature"

    # State file should be cleaned up
    state_file = isolated_git_repo / ".git" / "shortcake-split-state.json"
    assert not state_file.exists()


def test_split_abort_no_split_in_progress(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["split", "--abort"])
    assert result.exit_code == 1
    assert "No split in progress" in result.output


def test_split_continue_no_split_in_progress(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["split", "--continue"])
    assert result.exit_code == 1
    assert "No split in progress" in result.output


def test_split_continue_no_staged_changes(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content1")
    git.add_files("file1.txt")
    git.commit("Add files")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    # Start split
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0

    # Try to continue without staging
    result = runner.invoke(app, ["split", "--continue"])
    assert result.exit_code == 1
    assert "No staged changes" in result.output


def test_split_already_in_progress(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content1")
    git.add_files("file1.txt")
    git.commit("Add files")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    # Start split
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0

    # Try to start another split
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 1
    assert "split is already in progress" in result.output


def test_split_continue_creates_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch with multiple files
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content1")
    (isolated_git_repo / "file2.txt").write_text("content2")
    git.add_files(["file1.txt", "file2.txt"])
    git.commit("Add files")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    # Start split
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0

    # Stage first file
    subprocess.run(["git", "add", "file1.txt"], cwd=isolated_git_repo, check=True)

    # Continue - answer "no" to reuse original, then provide branch name and message
    # Input: n (don't reuse), branch name, commit message
    result = runner.invoke(app, ["split", "--continue"], input="n\nadd-file1\nAdd file1\n")
    assert result.exit_code == 0, result.output
    assert "Created branch" in result.output

    # Should have created a branch with shortcake notes
    branches = git.get_branches()
    assert "add-file1" in branches
    notes = get_notes(isolated_git_repo, "add-file1")
    assert notes is not None
    assert "parent" in notes


def test_split_full_workflow(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch with multiple files
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "api.py").write_text("api code")
    (isolated_git_repo / "utils.py").write_text("utils code")
    git.add_files(["api.py", "utils.py"])
    git.commit("Add api and utils")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    # Start split
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0

    # Stage and commit first file - don't reuse original, use a new branch name
    subprocess.run(["git", "add", "api.py"], cwd=isolated_git_repo, check=True)
    # Input: n (don't reuse), branch name, commit message
    result = runner.invoke(app, ["split", "--continue"], input="n\nadd-api\nAdd API\n")
    assert result.exit_code == 0, result.output
    assert "Created branch" in result.output

    # Stage and commit second file - reuse original branch name to preserve PR
    subprocess.run(["git", "add", "utils.py"], cwd=isolated_git_repo, check=True)
    # Input: y (reuse original "feature"), commit message (default is original)
    result = runner.invoke(app, ["split", "--continue"], input="y\nAdd utils\n")
    assert result.exit_code == 0, result.output

    # Should have created two branches:
    # - add-api (first split branch)
    # - feature (original branch name, preserved for PR)
    branches = git.get_branches()
    assert "add-api" in branches
    assert "feature" in branches

    # State file should be cleaned up
    state_file = isolated_git_repo / ".git" / "shortcake-split-state.json"
    assert not state_file.exists()


def test_split_default_branch_name_is_original(isolated_git_repo: Path, isolated_config: Path):
    """Test that the default branch name is the original branch name (for first split)."""
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("my-feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content1")
    (isolated_git_repo / "file2.txt").write_text("content2")
    git.add_files(["file1.txt", "file2.txt"])
    git.commit("Add files")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "my-feature")

    # Start split
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0

    # Stage first file and reuse original branch name (should be "my-feature")
    subprocess.run(["git", "add", "file1.txt"], cwd=isolated_git_repo, check=True)
    # Input: y (reuse original), commit message (use default)
    result = runner.invoke(app, ["split", "--continue"], input="y\n\n")
    assert result.exit_code == 0, result.output
    assert "Created branch: my-feature" in result.output

    # Stage second file - original already used, so no prompt to reuse
    subprocess.run(["git", "add", "file2.txt"], cwd=isolated_git_repo, check=True)
    # Input: branch name (empty = generate from message), commit message
    result = runner.invoke(app, ["split", "--continue"], input="\nAdd file2\n")
    assert result.exit_code == 0, result.output
    # Should have created a branch with generated name (not "my-feature" again)
    assert "add-file2" in result.output or "Created branch" in result.output

    branches = git.get_branches()
    assert "my-feature" in branches
    # There should be another branch (generated name)
    assert len([b for b in branches if b not in ["main", "my-feature"]]) >= 1


def test_split_multiple_commits(isolated_git_repo: Path, isolated_config: Path):
    """Test that split unstages all commits in the branch, not just the latest."""
    git = GitRepo()

    # Create a tracked branch with multiple commits
    git.create_branch("feature", checkout=True)

    # First commit
    (isolated_git_repo / "file1.py").write_text("file1 content")
    git.add_files("file1.py")
    git.commit("Add file1")

    # Second commit
    (isolated_git_repo / "file2.py").write_text("file2 content")
    git.add_files("file2.py")
    git.commit("Add file2")

    # Third commit
    (isolated_git_repo / "file3.py").write_text("file3 content")
    git.add_files("file3.py")
    git.commit("Add file3")

    main_sha = git.get_commit_sha("main")
    add_notes(
        isolated_git_repo,
        json.dumps({"parent": "main", "parent_revision": main_sha}),
        "feature",
    )

    # Start split - should show 3 commits
    result = runner.invoke(app, ["split", "--by-hunk"])
    assert result.exit_code == 0
    assert "3 commit(s)" in result.output

    # All three files should be unstaged
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=isolated_git_repo, capture_output=True, text=True
    )
    # ?? means untracked (after reset, files are untracked)
    assert "file1.py" in status.stdout
    assert "file2.py" in status.stdout
    assert "file3.py" in status.stdout

    # Abort to clean up
    result = runner.invoke(app, ["split", "--abort"])
    assert result.exit_code == 0

    # All commits should be restored
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=isolated_git_repo, capture_output=True, text=True
    )
    assert "Add file1" in log.stdout
    assert "Add file2" in log.stdout
    assert "Add file3" in log.stdout

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

    # Continue with a message
    result = runner.invoke(app, ["split", "--continue"], input="Add file1\n")
    assert result.exit_code == 0
    assert "Created branch" in result.output

    # Should have created a branch with shortcake notes
    branches = git.get_branches()
    new_branch = [b for b in branches if b.startswith("add-file1")][0]
    notes = get_notes(isolated_git_repo, new_branch)
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

    # Stage and commit first file
    subprocess.run(["git", "add", "api.py"], cwd=isolated_git_repo, check=True)
    result = runner.invoke(app, ["split", "--continue"], input="Add API\n")
    assert result.exit_code == 0
    assert "Created branch" in result.output

    # Stage and commit second file
    subprocess.run(["git", "add", "utils.py"], cwd=isolated_git_repo, check=True)
    result = runner.invoke(app, ["split", "--continue"], input="Add utils\n")
    assert result.exit_code == 0

    # Should have created two branches:
    # - add-api (first split branch)
    # - feature (original branch name, preserved for PR)
    branches = git.get_branches()
    api_branch = [b for b in branches if "api" in b.lower()]
    # The last branch is renamed to the original "feature" to preserve PRs
    assert "feature" in branches

    assert len(api_branch) >= 1

    # State file should be cleaned up
    state_file = isolated_git_repo / ".git" / "shortcake-split-state.json"
    assert not state_file.exists()

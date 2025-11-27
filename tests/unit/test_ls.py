"""Tests for the ls command."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from tests.helpers.git_helpers import get_notes

runner = CliRunner()

type GitEditorScript = Callable[[str], None]


def stage_all(repo_path: Path):
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)


@pytest.fixture
def in_repo(isolated_git_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure we're in the isolated git repo directory for the test."""
    # Force change to the directory and ensure it persists
    original_dir = Path.cwd()
    os.chdir(isolated_git_repo)
    monkeypatch.chdir(isolated_git_repo)

    # Verify we're in the right place
    assert Path.cwd() == isolated_git_repo

    yield isolated_git_repo

    # Cleanup
    try:
        os.chdir(original_dir)
    except Exception:
        pass


def test_ls_help():
    result = runner.invoke(app, ["ls", "--help"])
    assert result.exit_code == 0
    assert "List all shortcake-managed branches" in result.stdout


def test_ls_with_no_branches(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "No shortcake-managed branches found" in result.stdout


def test_ls_with_single_branch(
    in_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    # Create a branch using shortcake create
    test_file = in_repo / "test1.txt"
    test_file.write_text("test1")
    stage_all(in_repo)

    git_editor_script("Test feature")
    os.chdir(in_repo)  # Ensure we're in the repo before invoking
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Now list branches
    os.chdir(in_repo)  # Ensure we're in the repo before invoking
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "test-feature" in result.output
    assert "(current)" in result.output


def test_ls_with_stacked_branches(
    in_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    # Create first branch
    test_file1 = in_repo / "test1.txt"
    test_file1.write_text("test1")
    stage_all(in_repo)

    git_editor_script("First feature")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Create second branch (stacked on first)
    test_file2 = in_repo / "test2.txt"
    test_file2.write_text("test2")
    stage_all(in_repo)

    git_editor_script("Second feature")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # List should show tree structure
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "first-feature" in result.output
    assert "second-feature" in result.output


def test_ls_shows_tree_structure(
    in_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    # Create a stack of 3 branches
    for i in range(1, 4):
        test_file = in_repo / f"test{i}.txt"
        test_file.write_text(f"test{i}")
        stage_all(in_repo)

        git_editor_script(f"Feature {i}")
        result = runner.invoke(app, ["create"])
        assert result.exit_code == 0

    # List should show tree with proper nesting
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    output = result.output

    # Should contain tree characters
    assert "└──" in output or "├──" in output


def test_create_adds_metadata(
    in_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    # Create a branch
    test_file = in_repo / "test.txt"
    test_file.write_text("test")
    stage_all(in_repo)

    git_editor_script("Test feature")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Check that metadata was added
    notes = get_notes(in_repo, "test-feature")
    assert notes is not None
    assert "parent" in notes


def test_ls_only_shows_shortcake_branches(
    in_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    git = GitRepo()

    # Create a regular git branch (not using shortcake)
    git.create_branch("regular-branch", checkout=False)

    # Create a shortcake branch
    test_file = in_repo / "test.txt"
    test_file.write_text("test")
    stage_all(in_repo)

    git_editor_script("Shortcake feature")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # List should only show shortcake branch
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "shortcake-feature" in result.output
    assert "regular-branch" not in result.output


def test_ls_with_multiple_root_branches(
    in_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    git = GitRepo()

    # Get initial branch name
    initial_branch = git.get_current_branch()

    # Create first branch
    test_file1 = in_repo / "test1.txt"
    test_file1.write_text("test1")
    stage_all(in_repo)

    git_editor_script("Feature A")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Go back to initial branch
    git.checkout_branch(initial_branch)

    # Create second branch (also from initial)
    test_file2 = in_repo / "test2.txt"
    test_file2.write_text("test2")
    stage_all(in_repo)

    git_editor_script("Feature B")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # List should show both branches
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert "feature-a" in result.output
    assert "feature-b" in result.output

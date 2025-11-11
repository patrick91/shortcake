"""Tests for the edit and modify commands."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app

from .conftest import GitEditorScript

runner = CliRunner()


def stage_all(repo_path: Path) -> None:
    """Helper to stage all changes in the repository."""
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_help(command: str):
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "amending the commit" in result.stdout.lower()
    assert "Stage your changes first" in result.stdout


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_basic_success(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("initial content")
    stage_all(isolated_git_repo)

    commit_message = "Initial commit"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Get the branch name before edit
    branch_before = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    branch_name = branch_before.stdout.strip()

    initial_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    initial_hash = initial_commit.stdout.strip()

    test_file.write_text("updated content")
    stage_all(isolated_git_repo)

    result = runner.invoke(app, [command])

    assert result.exit_code == 0
    assert "Successfully amended the commit" in result.stdout

    # Verify we stayed on the same branch
    branch_after = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert branch_after.stdout.strip() == branch_name

    amended_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    amended_hash = amended_commit.stdout.strip()
    assert amended_hash != initial_hash

    assert test_file.read_text() == "updated content"


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_error_no_changes(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("content")
    stage_all(isolated_git_repo)

    commit_message = "Initial commit"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    result = runner.invoke(app, [command])

    assert result.exit_code == 1
    assert "Error: No staged changes to amend" in result.stderr


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_requires_manual_staging(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("initial")
    stage_all(isolated_git_repo)

    commit_message = "Initial commit"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    test_file.write_text("updated")

    result = runner.invoke(app, [command])

    assert result.exit_code == 1
    assert "Error: No staged changes to amend" in result.stderr

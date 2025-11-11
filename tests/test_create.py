import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app

from .conftest import GitEditorScript

runner = CliRunner()


def test_create_help():
    result = runner.invoke(app, ["create", "--help"])

    assert result.exit_code == 0

    assert "Create a stack with a new branch and commit" in result.stdout
    assert "keep" in result.stdout.lower()
    assert "emoji" in result.stdout.lower()


def test_create_basic_success(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test basic create command with emoji removed (default config)."""
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    commit_message = "🚀 Add new feature"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    assert "Created and switched to branch: add-new-feature" in result.stdout
    assert f"Created commit: {commit_message}" in result.stdout

    branch_result = subprocess.run(
        ["git", "branch", "--list", "add-new-feature"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert "* add-new-feature" in branch_result.stdout

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert current_branch.stdout.strip() == "add-new-feature"

    commit_msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert commit_msg.stdout.strip() == commit_message


def test_create_with_keep_emoji_true(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    runner.invoke(app, ["config", "set", "keep_emoji", "true"])

    test_file = isolated_git_repo / "feature.txt"
    test_file.write_text("new feature")

    commit_message = "🚀 Add rocket feature"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    assert "Created and switched to branch: 🚀-add-rocket-feature" in result.stdout

    branch_result = subprocess.run(
        ["git", "branch", "--list", "🚀-add-rocket-feature"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert "🚀-add-rocket-feature" in branch_result.stdout

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert current_branch.stdout.strip() == "🚀-add-rocket-feature"


def test_create_with_long_message(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test create command with long commit message (should truncate to 50 chars)."""
    test_file = isolated_git_repo / "long.txt"
    test_file.write_text("long feature")

    commit_message = "Add a very long feature name that exceeds fifty characters in length"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0

    expected_branch = "add-a-very-long-feature-name-that-exceeds-fifty-ch"
    assert len(expected_branch) == 50
    assert f"Created and switched to branch: {expected_branch}" in result.stdout

    branch_result = subprocess.run(
        ["git", "branch", "--list", expected_branch],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert f"* {expected_branch}" in branch_result.stdout

    commit_msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert commit_msg.stdout.strip() == commit_message


def test_create_with_special_characters(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test create command with special characters in commit message."""
    test_file = isolated_git_repo / "special.txt"
    test_file.write_text("special")

    commit_message = "Fix: bug in @user's code (issue #123)!"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0

    expected_branch = "fix-bug-in-users-code-issue-123"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout

    branch_result = subprocess.run(
        ["git", "branch", "--list", expected_branch],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert f"* {expected_branch}" in branch_result.stdout


def test_create_with_multiple_spaces(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test create command with multiple spaces in commit message."""
    test_file = isolated_git_repo / "spaces.txt"
    test_file.write_text("spaces")

    commit_message = "Add    feature   with    spaces"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0

    expected_branch = "add-feature-with-spaces"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_error_empty_commit_message(
    isolated_git_repo: Path, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
):
    """Test create command with empty commit message."""
    test_file = isolated_git_repo / "empty.txt"
    test_file.write_text("empty")

    # set GIT_EDITOR to false which exits with error
    monkeypatch.setenv("GIT_EDITOR", "false")

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    assert "Error:" in result.stderr


def test_create_error_only_emoji_message(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test create command with only emoji in commit message (keep_emoji=False)."""
    test_file = isolated_git_repo / "emoji.txt"
    test_file.write_text("emoji only")

    commit_message = "🚀🔥⭐"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    assert "Could not generate a valid branch name" in result.stderr

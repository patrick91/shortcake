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


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_no_commits_to_amend(
    command: str,
    tmp_path: Path,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_path = tmp_path / "empty_repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    test_file = repo_path / "test.txt"
    test_file.write_text("content")
    stage_all(repo_path)

    result = runner.invoke(app, [command])

    assert result.exit_code == 1
    assert result.exception is not None
    assert isinstance(result.exception, subprocess.CalledProcessError)


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_preserves_commit_message(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("initial content")
    stage_all(isolated_git_repo)

    commit_message = "My important commit message"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    test_file.write_text("updated content")
    stage_all(isolated_git_repo)

    result = runner.invoke(app, [command])
    assert result.exit_code == 0

    message_result = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert message_result.stdout.strip() == commit_message


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_does_not_create_new_commit(
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

    commit_count_before = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    count_before = int(commit_count_before.stdout.strip())

    test_file.write_text("updated content")
    stage_all(isolated_git_repo)

    result = runner.invoke(app, [command])
    assert result.exit_code == 0

    commit_count_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    count_after = int(commit_count_after.stdout.strip())

    assert count_before == count_after


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_with_multiple_files(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    file1 = isolated_git_repo / "file1.txt"
    file2 = isolated_git_repo / "file2.txt"
    file1.write_text("content 1")
    file2.write_text("content 2")
    stage_all(isolated_git_repo)

    commit_message = "Initial commit with multiple files"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    file1.write_text("updated content 1")
    file2.write_text("updated content 2")
    file3 = isolated_git_repo / "file3.txt"
    file3.write_text("new file")
    stage_all(isolated_git_repo)

    result = runner.invoke(app, [command])
    assert result.exit_code == 0

    assert file1.read_text() == "updated content 1"
    assert file2.read_text() == "updated content 2"
    assert file3.read_text() == "new file"


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_multiple_consecutive_amends(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    initial_count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    starting_commit_count = int(initial_count.stdout.strip())

    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("version 1")
    stage_all(isolated_git_repo)

    commit_message = "Initial commit"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    initial_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    initial_hash = initial_commit.stdout.strip()

    test_file.write_text("version 2")
    stage_all(isolated_git_repo)
    result = runner.invoke(app, [command])
    assert result.exit_code == 0

    second_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    second_hash = second_commit.stdout.strip()

    test_file.write_text("version 3")
    stage_all(isolated_git_repo)
    result = runner.invoke(app, [command])
    assert result.exit_code == 0

    third_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    third_hash = third_commit.stdout.strip()

    assert initial_hash != second_hash != third_hash
    assert test_file.read_text() == "version 3"

    commit_count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert int(commit_count.stdout.strip()) == starting_commit_count + 1


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_with_mixed_staged_unstaged_changes(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    staged_file = isolated_git_repo / "staged.txt"
    unstaged_file = isolated_git_repo / "unstaged.txt"

    staged_file.write_text("initial staged")
    unstaged_file.write_text("initial unstaged")
    stage_all(isolated_git_repo)

    commit_message = "Initial commit"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    staged_file.write_text("updated staged")
    unstaged_file.write_text("updated unstaged")

    subprocess.run(
        ["git", "add", "staged.txt"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    result = runner.invoke(app, [command])
    assert result.exit_code == 0

    staged_in_commit = subprocess.run(
        ["git", "show", "HEAD:staged.txt"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert staged_in_commit.stdout == "updated staged"

    unstaged_in_commit = subprocess.run(
        ["git", "show", "HEAD:unstaged.txt"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert unstaged_in_commit.stdout == "initial unstaged"

    assert unstaged_file.read_text() == "updated unstaged"


@pytest.mark.parametrize("command", ["edit", "modify"])
def test_command_amending_initial_commit(
    command: str,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("initial content")
    stage_all(isolated_git_repo)

    commit_message = "Very first commit"
    git_editor_script(commit_message)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    commit_count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    count_before = int(commit_count.stdout.strip())

    test_file.write_text("updated content")
    stage_all(isolated_git_repo)

    result = runner.invoke(app, [command])
    assert result.exit_code == 0

    commit_count_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert int(commit_count_after.stdout.strip()) == count_before

    message_result = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert message_result.stdout.strip() == commit_message

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from tests.helpers.assertions import strip_ansi

type GitEditorScript = Callable[[str], None]

runner = CliRunner()


def stage_all(repo_path: Path) -> None:
    """Helper to stage all changes in the repository."""
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)


def test_create_help():
    result = runner.invoke(app, ["create", "--help"])

    assert result.exit_code == 0

    output = strip_ansi(result.stdout)
    assert "Create a stack with a new branch and commit" in output
    assert "keep" in output.lower()
    assert "emoji" in output.lower()
    assert "--no-verify" in output
    assert "-n" in output


def test_create_basic_success(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    commit_message = "🚀 Add new feature"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
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

    stage_all(isolated_git_repo)
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
    test_file = isolated_git_repo / "long.txt"
    test_file.write_text("long feature")

    commit_message = "Add a very long feature name that exceeds fifty characters in length"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
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
    test_file = isolated_git_repo / "special.txt"
    test_file.write_text("special")

    commit_message = "Fix: bug in @user's code (issue #123)!"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
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
    test_file = isolated_git_repo / "spaces.txt"
    test_file.write_text("spaces")

    commit_message = "Add    feature   with    spaces"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0

    expected_branch = "add-feature-with-spaces"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_error_empty_commit_message(
    isolated_git_repo: Path, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
):
    test_file = isolated_git_repo / "empty.txt"
    test_file.write_text("empty")

    # set GIT_EDITOR to false which exits with error
    monkeypatch.setenv("GIT_EDITOR", "false")

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    # Git commit will fail because the editor returns non-zero
    assert "Commit aborted or failed" in result.stderr


def test_create_error_only_emoji_message(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "emoji.txt"
    test_file.write_text("emoji only")

    commit_message = "🚀🔥⭐"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    assert (
        result.stderr.strip()
        == "Error: Could not generate a valid branch name from the commit message"
    )


def test_create_error_no_changes(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    commit_message = "Add nothing"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    # Git commit fails with no changes
    assert "Commit aborted or failed" in result.stderr


def test_create_error_branch_already_exists(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("content")
    stage_all(isolated_git_repo)

    subprocess.run(
        ["git", "branch", "add-feature"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    commit_message = "Add feature"
    git_editor_script(commit_message)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    assert "a branch named 'add-feature' already exists" in result.stderr


def test_create_error_not_in_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
):
    non_git_dir = tmp_path / "not_git"
    non_git_dir.mkdir()
    monkeypatch.chdir(non_git_dir)

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    assert (
        result.stderr.strip()
        == "Error: fatal: not a git repository (or any of the parent directories): .git"
    )


def test_create_with_leading_trailing_whitespace(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "whitespace.txt"
    test_file.write_text("whitespace")

    commit_message = "   Add feature with whitespace   "
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "add-feature-with-whitespace"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_with_consecutive_hyphens(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "hyphens.txt"
    test_file.write_text("hyphens")

    commit_message = "Add --- multiple --- hyphens"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "add-multiple-hyphens"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_with_very_short_message(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "short.txt"
    test_file.write_text("short")

    commit_message = "Go"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "go"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_with_only_hyphens(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "hyphens_only.txt"
    test_file.write_text("hyphens only")

    commit_message = "--- --- ---"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    assert (
        result.stderr.strip()
        == "Error: Could not generate a valid branch name from the commit message"
    )


def test_create_with_emoji_at_start(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "emoji_start.txt"
    test_file.write_text("emoji start")

    commit_message = "🚀 Launch feature"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "launch-feature"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_with_emoji_in_middle(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "emoji_middle.txt"
    test_file.write_text("emoji middle")

    commit_message = "Add 🚀 feature"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "add-feature"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_with_emoji_at_end(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "emoji_end.txt"
    test_file.write_text("emoji end")

    commit_message = "Add feature 🚀"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "add-feature"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_with_unicode_characters(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "unicode.txt"
    test_file.write_text("unicode")

    commit_message = "添加新功能"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "添加新功能"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert current_branch.stdout.strip() == expected_branch


def test_create_with_exactly_50_chars(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "fifty.txt"
    test_file.write_text("fifty")

    commit_message = "Add a very long feature name that is exactly right"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    branch_name = current_branch.stdout.strip()
    assert len(branch_name) <= 50


def test_create_with_multiline_commit_message(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "multiline.txt"
    test_file.write_text("multiline")

    commit_message = "Add feature\n\nThis is the body of the commit message"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "add-feature"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout

    commit_msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert commit_msg.stdout.strip() == "Add feature"


def test_create_config_persist_across_invocations(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    runner.invoke(app, ["config", "set", "keep_emoji", "true"])

    # Get the default branch name
    default_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    test_file = isolated_git_repo / "config_test1.txt"
    test_file.write_text("test1")
    stage_all(isolated_git_repo)

    commit_message = "🚀 First feature"
    git_editor_script(commit_message)

    result1 = runner.invoke(app, ["create"])
    assert result1.exit_code == 0
    assert "🚀-first-feature" in result1.stdout

    subprocess.run(
        ["git", "checkout", default_branch],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    test_file2 = isolated_git_repo / "config_test2.txt"
    test_file2.write_text("test2")
    stage_all(isolated_git_repo)

    commit_message2 = "⭐ Second feature"
    git_editor_script(commit_message2)

    result2 = runner.invoke(app, ["create"])
    assert result2.exit_code == 0
    assert "⭐-second-feature" in result2.stdout


def test_create_preserves_original_branch_on_error(
    isolated_git_repo: Path, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
):
    original_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    original_branch_name = original_branch.stdout.strip()

    monkeypatch.setenv("GIT_EDITOR", "false")

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert current_branch.stdout.strip() == original_branch_name


def test_create_with_numbers_only(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "numbers.txt"
    test_file.write_text("numbers")

    commit_message = "Fix issue 12345"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "fix-issue-12345"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_with_uppercase_letters(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "uppercase.txt"
    test_file.write_text("uppercase")

    commit_message = "ADD NEW FEATURE"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0
    expected_branch = "add-new-feature"
    assert f"Created and switched to branch: {expected_branch}" in result.stdout


def test_create_stacked_branches(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    # Create first branch with first commit
    test_file1 = isolated_git_repo / "feature1.txt"
    test_file1.write_text("first feature")
    stage_all(isolated_git_repo)

    commit_message1 = "Add first feature"
    git_editor_script(commit_message1)

    result1 = runner.invoke(app, ["create"])
    assert result1.exit_code == 0
    assert "Created and switched to branch: add-first-feature" in result1.stdout

    # Get the first commit hash
    first_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    first_commit_hash = first_commit.stdout.strip()

    # Create second branch with second commit (stacked on first)
    test_file2 = isolated_git_repo / "feature2.txt"
    test_file2.write_text("second feature")
    stage_all(isolated_git_repo)

    commit_message2 = "Add second feature"
    git_editor_script(commit_message2)

    result2 = runner.invoke(app, ["create"])
    assert result2.exit_code == 0
    assert "Created and switched to branch: add-second-feature" in result2.stdout

    # Verify we're on the second branch
    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert current_branch.stdout.strip() == "add-second-feature"

    # Verify the second branch contains both commits
    log_output = subprocess.run(
        ["git", "log", "--oneline", "--all", "--decorate"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )

    # Verify both commits exist in the log
    assert "Add first feature" in log_output.stdout
    assert "Add second feature" in log_output.stdout

    # Verify the first commit is in the history of the second branch
    commits_in_second_branch = subprocess.run(
        ["git", "log", "--format=%H", "add-second-feature"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert first_commit_hash in commits_in_second_branch.stdout

    # Verify the first branch only has one commit (not the second)
    commits_in_first_branch = subprocess.run(
        ["git", "log", "--format=%s", "add-first-feature"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    assert "Add first feature" in commits_in_first_branch.stdout
    assert "Add second feature" not in commits_in_first_branch.stdout

    # Verify both files exist in the second branch
    assert (isolated_git_repo / "feature1.txt").exists()
    assert (isolated_git_repo / "feature2.txt").exists()


def test_create_with_no_verify(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    commit_message = "Add feature with no verify"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create", "--no-verify"])

    assert result.exit_code == 0
    assert "Created and switched to branch: add-feature-with-no-verify" in result.stdout


def test_create_with_no_verify_short_flag(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    commit_message = "Add feature with short flag"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create", "-n"])

    assert result.exit_code == 0
    assert "Created and switched to branch: add-feature-with-short-flag" in result.stdout


def test_create_with_claude_flag_in_help():
    result = runner.invoke(app, ["create", "--help"])

    assert result.exit_code == 0
    output = strip_ansi(result.stdout)
    assert "--claude" in output
    assert "-c" in output
    assert "Claude" in output


def test_create_with_claude_no_staged_changes(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["create", "--claude"])

    assert result.exit_code == 1
    # Error messages go to stderr via rich console
    assert "No staged changes" in result.output


def test_create_with_claude_cli_not_found(
    isolated_git_repo: Path, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Mock the _is_claude_cli_available function directly
    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: False)

    result = runner.invoke(app, ["create", "--claude"])

    assert result.exit_code == 1
    # Error messages go to stderr via rich console
    assert "Claude CLI not found" in result.output


def test_create_with_claude_success(
    isolated_git_repo: Path,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_editor_script: GitEditorScript,
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Mock _is_claude_cli_available and _get_claude_command
    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: True)
    monkeypatch.setattr(create, "_get_claude_command", lambda: ["claude"])

    # Mock subprocess.run for the claude call
    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "claude":
            # Return a mock result
            class MockResult:
                returncode = 0
                stdout = "Add test file for feature"
                stderr = ""

            return MockResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Set up editor to accept the pre-filled message
    git_editor_script("Add test file for feature")

    result = runner.invoke(app, ["create", "--claude"])

    assert result.exit_code == 0
    assert "Generating commit message with Claude" in result.stdout
    assert "Generated: Add test file for feature" in result.stdout
    assert "Created and switched to branch: add-test-file-for-feature" in result.stdout


def test_create_with_claude_and_gitmoji(
    isolated_git_repo: Path,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_editor_script: GitEditorScript,
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Mock _is_claude_cli_available and _get_claude_command
    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: True)
    monkeypatch.setattr(create, "_get_claude_command", lambda: ["claude"])

    # Mock subprocess.run for the claude call
    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "claude":
            # Check that gitmoji instruction is in the prompt
            prompt = cmd[3] if len(cmd) > 3 else ""
            if "gitmoji" in prompt.lower():

                class MockResult:
                    returncode = 0
                    stdout = "✨ Add new feature"
                    stderr = ""

                return MockResult()

            class MockResult:
                returncode = 0
                stdout = "Add new feature"
                stderr = ""

            return MockResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Set up editor to accept the pre-filled message
    git_editor_script("✨ Add new feature")

    result = runner.invoke(app, ["create", "--claude", "--gitmoji"])

    assert result.exit_code == 0
    assert "Generating commit message with Claude" in result.stdout


def test_create_with_claude_generation_fails(
    isolated_git_repo: Path, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
):
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Mock _is_claude_cli_available and _get_claude_command
    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: True)
    monkeypatch.setattr(create, "_get_claude_command", lambda: ["claude"])

    # Mock subprocess.run for the claude call
    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "claude":

            class MockResult:
                returncode = 1
                stdout = ""
                stderr = "Error"

            return MockResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(app, ["create", "--claude"])

    assert result.exit_code == 1
    # Error messages go to stderr via rich console
    assert "Failed to generate commit message with Claude" in result.output


def test_create_insert_updates_children(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test that --insert updates children of the current branch to point to the new branch."""
    import json

    from shortcake.git import GitRepo
    from tests.helpers.git_helpers import get_notes

    git = GitRepo()

    # Create first branch (parent)
    (isolated_git_repo / "parent.txt").write_text("parent")
    git_editor_script("Add parent feature")
    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0
    parent_branch = "add-parent-feature"

    # Create child branch
    (isolated_git_repo / "child.txt").write_text("child")
    git_editor_script("Add child feature")
    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0
    child_branch = "add-child-feature"

    # Go back to parent branch
    git.checkout_branch(parent_branch)

    # Now insert a new branch between parent and child
    (isolated_git_repo / "middle.txt").write_text("middle")
    git_editor_script("Add middle feature")
    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create", "--insert"])
    assert result.exit_code == 0
    middle_branch = "add-middle-feature"

    assert f"Created and switched to branch: {middle_branch}" in result.stdout
    assert f"Updating 1 child branch(es) to point to '{middle_branch}'" in result.stdout
    assert f"{child_branch}: parent → {middle_branch}" in result.stdout

    # Verify the child branch now points to the middle branch
    notes = get_notes(isolated_git_repo, child_branch)
    assert notes is not None
    metadata = json.loads(notes)
    assert metadata.get("parent") == middle_branch


def test_create_insert_no_children(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test that --insert works even when there are no children (just creates normally)."""
    # Create first branch
    (isolated_git_repo / "feature.txt").write_text("feature")
    git_editor_script("Add feature")
    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Insert another branch (no children to update)
    (isolated_git_repo / "another.txt").write_text("another")
    git_editor_script("Add another feature")
    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create", "--insert"])
    assert result.exit_code == 0

    # Should succeed without any child update messages
    assert "Created and switched to branch: add-another-feature" in result.stdout
    assert "child branch" not in result.stdout


def test_create_with_claude_runs_precommit_hooks(
    isolated_git_repo: Path,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_editor_script: GitEditorScript,
):
    """Test that pre-commit hooks run before generating the commit message with Claude."""
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Create a pre-commit hook that creates a marker file
    hooks_dir = isolated_git_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_marker = isolated_git_repo / "hook_ran.marker"
    hook_script = hooks_dir / "pre-commit"
    hook_script.write_text(f'#!/bin/sh\ntouch "{hook_marker}"\nexit 0\n')
    hook_script.chmod(0o755)

    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: True)
    monkeypatch.setattr(create, "_get_claude_command", lambda: ["claude"])

    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "claude":
            # Verify hook ran before Claude was called
            assert hook_marker.exists(), "Pre-commit hook should run before Claude"

            class MockResult:
                returncode = 0
                stdout = "Add test file"
                stderr = ""

            return MockResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)
    git_editor_script("Add test file")

    result = runner.invoke(app, ["create", "--claude"])

    assert result.exit_code == 0
    assert "Running pre-commit hooks" in result.stdout
    assert "Generating commit message with Claude" in result.stdout


def test_create_with_claude_precommit_hook_fails(
    isolated_git_repo: Path,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that pre-commit hook failures are properly reported."""
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Create a pre-commit hook that fails
    hooks_dir = isolated_git_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_script = hooks_dir / "pre-commit"
    hook_script.write_text('#!/bin/sh\necho "Linting failed: style errors found"\nexit 1\n')
    hook_script.chmod(0o755)

    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: True)

    result = runner.invoke(app, ["create", "--claude"])

    assert result.exit_code == 1
    assert "Pre-commit hooks failed" in result.output


def test_create_with_claude_no_verify_skips_precommit(
    isolated_git_repo: Path,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_editor_script: GitEditorScript,
):
    """Test that --no-verify skips pre-commit hooks when using --claude."""
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Create a pre-commit hook that creates a marker file
    hooks_dir = isolated_git_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_marker = isolated_git_repo / "hook_ran.marker"
    hook_script = hooks_dir / "pre-commit"
    hook_script.write_text(f'#!/bin/sh\ntouch "{hook_marker}"\nexit 0\n')
    hook_script.chmod(0o755)

    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: True)
    monkeypatch.setattr(create, "_get_claude_command", lambda: ["claude"])

    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "claude":

            class MockResult:
                returncode = 0
                stdout = "Add test file"
                stderr = ""

            return MockResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)
    git_editor_script("Add test file")

    result = runner.invoke(app, ["create", "--claude", "--no-verify"])

    assert result.exit_code == 0
    assert not hook_marker.exists(), "Pre-commit hooks should not run with --no-verify"
    assert "Running pre-commit hooks" not in result.stdout


def test_create_with_claude_restages_modified_files(
    isolated_git_repo: Path,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_editor_script: GitEditorScript,
):
    """Test that files modified by pre-commit hooks are re-staged."""
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")
    stage_all(isolated_git_repo)

    # Create a pre-commit hook that modifies the file
    hooks_dir = isolated_git_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_script = hooks_dir / "pre-commit"
    hook_script.write_text(f'#!/bin/sh\necho "formatted test content" > "{test_file}"\nexit 0\n')
    hook_script.chmod(0o755)

    from shortcake.commands import create

    monkeypatch.setattr(create, "_is_claude_cli_available", lambda: True)
    monkeypatch.setattr(create, "_get_claude_command", lambda: ["claude"])

    original_run = subprocess.run
    files_added = []

    def mock_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "claude":

            class MockResult:
                returncode = 0
                stdout = "Add formatted test file"
                stderr = ""

            return MockResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Track calls to add_files
    from shortcake.git import GitRepo

    original_add_files = GitRepo.add_files

    def mock_add_files(self, paths):
        if isinstance(paths, list):
            files_added.extend(paths)
        else:
            files_added.append(paths)
        return original_add_files(self, paths)

    monkeypatch.setattr(GitRepo, "add_files", mock_add_files)

    git_editor_script("Add formatted test file")

    result = runner.invoke(app, ["create", "--claude"])

    assert result.exit_code == 0
    # Verify files were re-staged after hooks ran
    assert "test.txt" in files_added

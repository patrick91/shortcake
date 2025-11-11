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
    """Test create command with long commit message (should truncate to 50 chars)."""
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
    """Test create command with special characters in commit message."""
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
    """Test create command with multiple spaces in commit message."""
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
    """Test create command with empty commit message."""
    test_file = isolated_git_repo / "empty.txt"
    test_file.write_text("empty")

    # set GIT_EDITOR to false which exits with error
    monkeypatch.setenv("GIT_EDITOR", "false")

    stage_all(isolated_git_repo)
    result = runner.invoke(app, ["create"])

    assert result.exit_code == 1
    # Git commit will fail because the editor returns non-zero
    assert result.stderr.strip() == "Error: Command failed"


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
    # Git commit fails with no changes, stderr is empty so we get generic error
    assert result.stderr.strip() == "Error: Command failed"


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
    assert result.stderr.strip() == "Error: fatal: a branch named 'add-feature' already exists"


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

    test_file = isolated_git_repo / "config_test1.txt"
    test_file.write_text("test1")
    stage_all(isolated_git_repo)

    commit_message = "🚀 First feature"
    git_editor_script(commit_message)

    result1 = runner.invoke(app, ["create"])
    assert result1.exit_code == 0
    assert "🚀-first-feature" in result1.stdout

    subprocess.run(
        ["git", "checkout", "main"],
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

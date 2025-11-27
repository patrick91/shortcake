"""Integration tests for branch stack workflows."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from inline_snapshot import snapshot
from typer.testing import CliRunner

from shortcake.cli import app
from tests.helpers.assertions import assert_branch_exists, assert_current_branch
from tests.helpers.git_helpers import get_notes

type GitEditorScript = Callable[[str], None]


@pytest.fixture
def runner() -> CliRunner:
    """Provide a CLI test runner."""
    return CliRunner()


def stage_all(isolated_git_repo: Path) -> None:
    """Helper to stage all changes."""
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, check=True, capture_output=True)


@pytest.mark.integration
def test_create_and_list_branch_stack(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
) -> None:
    """Test creating a stack of branches and listing them.

    This integration test verifies:
    - Creating multiple stacked branches
    - Git notes are properly stored
    - The ls command shows the correct tree structure
    """
    # Create first branch
    test_file = isolated_git_repo / "auth.txt"
    test_file.write_text("authentication code")
    stage_all(isolated_git_repo)

    git_editor_script("Add user authentication")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"
    assert result.stdout == snapshot(
        """\
Created and switched to branch: add-user-authentication
Created commit: Add user authentication
"""
    )
    assert_branch_exists(isolated_git_repo, "add-user-authentication")
    assert_current_branch(isolated_git_repo, "add-user-authentication")

    # Verify git notes were created
    notes = get_notes(isolated_git_repo, "HEAD", "shortcake")
    assert notes is not None
    assert '"parent": "main"' in notes

    # Create second branch (stacked on first)
    test_file2 = isolated_git_repo / "login.txt"
    test_file2.write_text("login form")
    stage_all(isolated_git_repo)

    git_editor_script("Add login form")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"
    assert_branch_exists(isolated_git_repo, "add-login-form")
    assert_current_branch(isolated_git_repo, "add-login-form")

    # Verify second branch has correct parent
    notes = get_notes(isolated_git_repo, "HEAD", "shortcake")
    assert notes is not None
    assert '"parent": "add-user-authentication"' in notes

    # Create third branch
    test_file3 = isolated_git_repo / "validation.txt"
    test_file3.write_text("password validation")
    stage_all(isolated_git_repo)

    git_editor_script("Add password validation")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"
    assert_branch_exists(isolated_git_repo, "add-password-validation")

    # List all branches and verify tree structure
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0

    # Use snapshot to verify the exact tree structure output
    assert result.stdout == snapshot(
        """\
◉ add-password-validation
│
◯ add-login-form
│
◯ add-user-authentication
│
◯ main
"""
    )


@pytest.mark.integration
def test_adopt_existing_branch_and_stack(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
) -> None:
    """Test adopting an existing branch and creating a stack on top.

    This integration test verifies:
    - Adopting an existing git branch into shortcake
    - Creating stacked branches on top of adopted branch
    - Branch relationships are correctly tracked
    """
    # Create a regular git branch (outside shortcake)
    from shortcake.git import GitRepo

    git = GitRepo(isolated_git_repo)
    git.create_branch("feature-base", checkout=True)

    # Make a commit
    test_file = isolated_git_repo / "feature.txt"
    test_file.write_text("new feature")
    git.add_files("feature.txt")
    git.commit("Add feature base")

    # Adopt the branch with main as parent
    result = runner.invoke(app, ["adopt", "feature-base", "--parent", "main"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"
    assert result.stdout == snapshot("Adopted branch 'feature-base' with parent 'main'\n")

    # Verify it was adopted (has git notes)
    notes = get_notes(isolated_git_repo, "feature-base", "shortcake")
    assert notes is not None
    assert '"parent": "main"' in notes

    # Create a branch on top of the adopted branch
    test_file2 = isolated_git_repo / "extension.txt"
    test_file2.write_text("extended feature")
    stage_all(isolated_git_repo)

    git_editor_script("Extend feature")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"
    assert_branch_exists(isolated_git_repo, "extend-feature")

    # Verify the new branch is stacked on the adopted branch
    notes = get_notes(isolated_git_repo, "HEAD", "shortcake")
    assert notes is not None
    assert '"parent": "feature-base"' in notes

    # List should show both branches in a tree structure
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert result.stdout == snapshot(
        """\
◉ extend-feature
│
◯ feature-base
│
◯ main
"""
    )


@pytest.mark.integration
def test_multiple_parallel_stacks(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
) -> None:
    """Test creating multiple independent branch stacks.

    This integration test verifies:
    - Creating multiple stacks from main
    - Each stack is tracked independently
    - ls command shows all stacks
    """
    # Create first stack
    test_file1 = isolated_git_repo / "feature1.txt"
    test_file1.write_text("feature 1")
    stage_all(isolated_git_repo)

    git_editor_script("Feature one")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"
    assert_branch_exists(isolated_git_repo, "feature-one")

    # Switch back to main
    from shortcake.git import GitRepo

    git = GitRepo(isolated_git_repo)
    git.checkout_branch("main")

    # Create second independent stack
    test_file2 = isolated_git_repo / "feature2.txt"
    test_file2.write_text("feature 2")
    stage_all(isolated_git_repo)

    git_editor_script("Feature two")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"
    assert_branch_exists(isolated_git_repo, "feature-two")

    # Both stacks should appear in ls
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert result.stdout == snapshot(
        """\
◯ feature-one
│
◯ main

◉ feature-two
│
◯ main
"""
    )

    # Create a child branch on the second stack
    test_file3 = isolated_git_repo / "extension.txt"
    test_file3.write_text("extension")
    stage_all(isolated_git_repo)

    git_editor_script("Extend feature two")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, f"Failed with output: {result.stdout}\n{result.stderr}"

    # All three branches should appear in ls with proper tree structure
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 0
    assert result.stdout == snapshot(
        """\
◉ extend-feature-two
│
◯ feature-two
│
◯ main

◯ feature-one
│
◯ main
"""
    )

"""Tests for the adopt command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo

runner = CliRunner()


def test_adopt_help():
    result = runner.invoke(app, ["adopt", "--help"])
    assert result.exit_code == 0
    assert "Adopt an existing branch" in result.stdout


def test_adopt_current_branch_no_argument(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a regular git branch
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test")
    git.add_files("test.txt")
    git.commit("Test commit")
    git.create_branch("my-feature", checkout=True)

    # Adopt current branch
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0
    assert "Adopted branch 'my-feature'" in result.stdout

    # Verify notes were added
    notes = git.get_notes("my-feature", "shortcake")
    assert notes is not None


def test_adopt_specific_branch_with_argument(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a regular git branch
    git.create_branch("my-feature", checkout=False)

    # Adopt specific branch
    result = runner.invoke(app, ["adopt", "my-feature"])
    assert result.exit_code == 0
    assert "Adopted branch 'my-feature'" in result.stdout

    # Verify notes were added
    notes = git.get_notes("my-feature", "shortcake")
    assert notes is not None


def test_adopt_error_main_branch(isolated_git_repo: Path, isolated_config: Path):
    # Try to adopt main branch
    result = runner.invoke(app, ["adopt", "main"])
    assert result.exit_code == 1
    assert "Cannot adopt 'main' branch" in result.output


def test_adopt_error_master_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create master branch if it doesn't exist
    if not git.branch_exists("master"):
        git.create_branch("master", checkout=False)

    # Try to adopt master branch
    result = runner.invoke(app, ["adopt", "master"])
    assert result.exit_code == 1
    assert "Cannot adopt 'master' branch" in result.output


def test_adopt_error_branch_does_not_exist(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["adopt", "nonexistent"])
    assert result.exit_code == 1
    assert "Branch 'nonexistent' does not exist" in result.output


def test_adopt_error_already_tracked(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create and adopt a branch
    git.create_branch("my-feature", checkout=True)
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    # Try to adopt again
    result = runner.invoke(app, ["adopt", "my-feature"])
    assert result.exit_code == 1
    assert "already tracked" in result.output


def test_adopt_error_already_adopted_with_current_branch(
    isolated_git_repo: Path, isolated_config: Path
):
    git = GitRepo()

    # Create and adopt current branch
    git.create_branch("my-feature", checkout=True)
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    # Try to adopt same branch again while on it
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 1
    assert "already tracked" in result.output


def test_adopt_branch_appears_in_ls(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a regular git branch
    git.create_branch("my-feature", checkout=True)

    # Adopt it
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    # Check that it appears in ls
    ls_result = runner.invoke(app, ["ls"])
    assert "my-feature" in ls_result.stdout


def test_adopt_multiple_branches(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create multiple git branches with different commits
    # (git notes are attached to commits, not branches)
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("First commit")
    git.create_branch("feature-1", checkout=False)

    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test2")
    git.add_files("test2.txt")
    git.commit("Second commit")
    git.create_branch("feature-2", checkout=False)

    # Adopt both
    result1 = runner.invoke(app, ["adopt", "feature-1"])
    assert result1.exit_code == 0

    result2 = runner.invoke(app, ["adopt", "feature-2"])
    assert result2.exit_code == 0

    # Check both appear in ls
    ls_result = runner.invoke(app, ["ls"])
    assert "feature-1" in ls_result.stdout
    assert "feature-2" in ls_result.stdout


def test_adopt_branch_with_stacked_structure(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a stack of branches manually
    # Each branch needs its own commit for git notes to work properly
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("First feature")
    git.create_branch("feature-1", checkout=True)

    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test2")
    git.add_files("test2.txt")
    git.commit("Second feature")
    git.create_branch("feature-2", checkout=True)

    # Create another commit on feature-2 so it points to a different commit than feature-1
    test_file3 = isolated_git_repo / "test3.txt"
    test_file3.write_text("test3")
    git.add_files("test3.txt")
    git.commit("Third feature")

    # Adopt with parent relationship
    git.checkout_branch("feature-1")
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    git.checkout_branch("feature-2")
    result = runner.invoke(app, ["adopt", "--parent", "feature-1"])
    assert result.exit_code == 0

    # Verify parent relationship in notes
    notes = git.get_notes("feature-2", "shortcake")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "feature-1"


def test_adopt_recursive_ancestors(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a branch
    git.create_branch("my-feature", checkout=True)

    # Adopt with recursive flag and parent
    result = runner.invoke(app, ["adopt", "--recursive", "--parent", "main"])
    assert result.exit_code == 0
    assert "Adopted" in result.stdout


def test_adopt_recursive_descendants(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a branch
    git.create_branch("parent-branch", checkout=True)

    # Adopt with recursive flag (no parent specified, so descendants)
    result = runner.invoke(app, ["adopt", "--recursive"])
    assert result.exit_code == 0


def test_adopt_recursive_middle_of_stack(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create multiple branches
    git.create_branch("base", checkout=False)
    git.create_branch("middle", checkout=False)
    git.create_branch("top", checkout=True)

    # Adopt middle with recursive
    result = runner.invoke(app, ["adopt", "middle", "--recursive", "--parent", "base"])
    assert result.exit_code == 0


def test_adopt_recursive_partial_already_tracked(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create and adopt one branch
    git.create_branch("feature-1", checkout=False)
    runner.invoke(app, ["adopt", "feature-1"])

    # Create another branch
    git.create_branch("feature-2", checkout=True)

    # Adopt with recursive - should handle already tracked branch
    result = runner.invoke(app, ["adopt", "--recursive", "--parent", "feature-1"])
    assert result.exit_code == 0

"""Tests for the adopt command."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from tests.helpers.git_helpers import get_notes

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

    # Verify metadata was added
    notes = get_notes(isolated_git_repo, "my-feature")
    assert notes is not None


def test_adopt_specific_branch_with_argument(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a regular git branch
    git.create_branch("my-feature", checkout=False)

    # Adopt specific branch
    result = runner.invoke(app, ["adopt", "my-feature"])
    assert result.exit_code == 0
    assert "Adopted branch 'my-feature'" in result.stdout

    # Verify metadata was added
    notes = get_notes(isolated_git_repo, "my-feature")
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

    # Verify parent relationship in metadata
    notes = get_notes(isolated_git_repo, "feature-2")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "feature-1"


def test_adopt_auto_detect_parent(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a stack: main -> feature-1 -> feature-2
    # Each branch must point to a different commit for git notes to work properly
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("First feature")
    git.create_branch("feature-1", checkout=False)  # Don't checkout, stay on main

    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test2")
    git.add_files("test2.txt")
    git.commit("Second feature")
    git.create_branch("feature-2", checkout=False)  # Don't checkout

    # Now feature-1 points to first commit, feature-2 points to second commit

    # Adopt feature-1 first
    git.checkout_branch("feature-1")
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    # Adopt feature-2 without specifying parent - should auto-detect feature-1
    git.checkout_branch("feature-2")
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    # Verify parent was set correctly
    notes = get_notes(isolated_git_repo, "feature-2")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "feature-1"


def test_adopt_auto_detect_closest_parent(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a more complex history:
    # main (A) -> feature-1 (B) -> feature-2 (C) -> feature-3 (D)
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("First feature")
    git.create_branch("feature-1", checkout=False)

    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test2")
    git.add_files("test2.txt")
    git.commit("Second feature")
    git.create_branch("feature-2", checkout=False)

    test_file3 = isolated_git_repo / "test3.txt"
    test_file3.write_text("test3")
    git.add_files("test3.txt")
    git.commit("Third feature")
    git.create_branch("feature-3", checkout=False)

    # Adopt feature-1 and feature-2
    git.checkout_branch("feature-1")
    runner.invoke(app, ["adopt"])

    git.checkout_branch("feature-2")
    runner.invoke(app, ["adopt"])

    # Adopt feature-3 - should pick feature-2 (closest) not feature-1
    git.checkout_branch("feature-3")
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    notes = get_notes(isolated_git_repo, "feature-3")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "feature-2"


def test_adopt_dry_run_shows_what_would_happen(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a branch
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("First feature")
    git.create_branch("feature-1", checkout=True)

    # Use dry run
    result = runner.invoke(app, ["adopt", "--dry-run"])
    assert result.exit_code == 0
    assert "Would adopt" in result.stdout

    # Verify nothing was actually adopted
    notes = get_notes(isolated_git_repo, "feature-1")
    assert notes is None


def test_adopt_dry_run_with_auto_detect_parent(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a stack with different commits
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("First feature")
    git.create_branch("feature-1", checkout=False)

    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test2")
    git.add_files("test2.txt")
    git.commit("Second feature")
    git.create_branch("feature-2", checkout=False)

    # Adopt feature-1 first
    git.checkout_branch("feature-1")
    runner.invoke(app, ["adopt"])

    # Dry run feature-2 - should show auto-detected parent
    git.checkout_branch("feature-2")
    result = runner.invoke(app, ["adopt", "--dry-run"])
    assert result.exit_code == 0
    assert "Would adopt" in result.stdout
    assert "Auto-detected parent: feature-1" in result.stdout

    # Verify nothing was adopted
    notes = get_notes(isolated_git_repo, "feature-2")
    assert notes is None


def test_adopt_explicit_parent_overrides_auto_detect(
    isolated_git_repo: Path, isolated_config: Path
):
    git = GitRepo()

    # Create branches: main -> feature-1 -> feature-2
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("First feature")
    git.create_branch("feature-1", checkout=False)

    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test2")
    git.add_files("test2.txt")
    git.commit("Second feature")
    git.create_branch("feature-2", checkout=False)

    # Adopt both with explicit parent for feature-2 set to main
    # (even though feature-1 would be auto-detected)
    git.checkout_branch("feature-1")
    runner.invoke(app, ["adopt"])

    git.checkout_branch("feature-2")
    result = runner.invoke(app, ["adopt", "--parent", "main"])
    assert result.exit_code == 0

    # Verify explicit parent was used
    notes = get_notes(isolated_git_repo, "feature-2")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "main"


def test_adopt_fallback_to_main_when_no_other_parent(
    isolated_git_repo: Path, isolated_config: Path
):
    """Test that adopt falls back to main when no other branch is a closer parent."""
    git = GitRepo()

    # Create a branch off main with its own commit (so distance > 0)
    git.create_branch("my-feature", checkout=True)
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test1")
    git.add_files("test1.txt")
    git.commit("Feature commit")

    # Adopt the branch - should auto-detect main as parent
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0
    assert "Adopted branch 'my-feature' with parent 'main'" in result.stdout

    # Verify parent was set to main
    notes = get_notes(isolated_git_repo, "my-feature")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "main"


def test_adopt_force_update_parent(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create branches
    git.create_branch("feature-1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files("f1.txt")
    git.commit("Feature 1")

    git.create_branch("feature-2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2")
    git.add_files("f2.txt")
    git.commit("Feature 2")

    # Adopt feature-2 with parent main
    result = runner.invoke(app, ["adopt", "--parent", "main"])
    assert result.exit_code == 0

    notes = get_notes(isolated_git_repo, "feature-2")
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "main"

    # Force update parent to feature-1
    result = runner.invoke(app, ["adopt", "--parent", "feature-1", "--force"])
    assert result.exit_code == 0
    assert "Updated" in result.stdout

    notes = get_notes(isolated_git_repo, "feature-2")
    notes_data = json.loads(notes)
    assert notes_data.get("parent") == "feature-1"


def test_adopt_invalid_parent_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "f.txt").write_text("f")
    git.add_files("f.txt")
    git.commit("Feature")

    result = runner.invoke(app, ["adopt", "--parent", "nonexistent"])
    assert result.exit_code == 1
    assert "Parent branch 'nonexistent' does not exist" in result.output


def test_adopt_not_in_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 1
    assert "Error" in result.output

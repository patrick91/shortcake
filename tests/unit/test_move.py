"""Tests for the move command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from tests.helpers.git_helpers import add_notes, get_notes

runner = CliRunner()


def test_move_help():
    result = runner.invoke(app, ["move", "--help"])
    assert result.exit_code == 0
    assert "Move a branch to a new parent" in result.output


def test_move_with_onto(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create feature1 -> feature2 stack
    git.create_branch("feature1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files(["f1.txt"])
    git.commit("Add f1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature1")

    git.create_branch("feature2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2")
    git.add_files(["f2.txt"])
    git.commit("Add f2")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature1"}), "feature2")

    # Move feature2 onto main
    result = runner.invoke(app, ["move", "--onto", "main"])
    assert result.exit_code == 0
    assert "Moved" in result.output
    assert "feature1" in result.output
    assert "main" in result.output

    # Verify metadata was updated
    notes = get_notes(isolated_git_repo, "feature2", "shortcake")
    assert notes is not None
    metadata = json.loads(notes)
    assert metadata["parent"] == "main"


def test_move_specific_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create feature1 -> feature2 stack
    git.create_branch("feature1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files(["f1.txt"])
    git.commit("Add f1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature1")

    git.create_branch("feature2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2")
    git.add_files(["f2.txt"])
    git.commit("Add f2")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature1"}), "feature2")

    # Go to main
    git.checkout_branch("main")

    # Move feature2 onto main (from main branch)
    result = runner.invoke(app, ["move", "feature2", "--onto", "main"])
    assert result.exit_code == 0
    assert "Moved" in result.output


def test_move_no_rebase(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create feature1 -> feature2 stack
    git.create_branch("feature1", checkout=True)
    (isolated_git_repo / "f1.txt").write_text("f1")
    git.add_files(["f1.txt"])
    git.commit("Add f1")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature1")

    git.create_branch("feature2", checkout=True)
    (isolated_git_repo / "f2.txt").write_text("f2")
    git.add_files(["f2.txt"])
    git.commit("Add f2")
    add_notes(isolated_git_repo, json.dumps({"parent": "feature1"}), "feature2")

    # Move with --no-rebase
    result = runner.invoke(app, ["move", "--onto", "main", "--no-rebase"])
    assert result.exit_code == 0
    assert "metadata only" in result.output

    # Verify metadata was updated
    notes = get_notes(isolated_git_repo, "feature2", "shortcake")
    assert notes is not None
    metadata = json.loads(notes)
    assert metadata["parent"] == "main"


def test_move_nonexistent_branch(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["move", "nonexistent", "--onto", "main"])
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_move_trunk_branch(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["move", "main", "--onto", "main"])
    assert result.exit_code == 1
    assert "Cannot move" in result.output


def test_move_untracked_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a branch without shortcake metadata
    git.create_branch("untracked", checkout=True)
    (isolated_git_repo / "file.txt").write_text("content")
    git.add_files(["file.txt"])
    git.commit("Add file")

    # Move should fail because branch is not managed
    result = runner.invoke(app, ["move", "--onto", "main"])
    assert result.exit_code == 1
    assert "not managed" in result.output.lower()


def test_move_onto_nonexistent_parent(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file.txt").write_text("content")
    git.add_files(["file.txt"])
    git.commit("Add file")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    # Move onto nonexistent branch should fail
    result = runner.invoke(app, ["move", "--onto", "nonexistent"])
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_move_onto_itself(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file.txt").write_text("content")
    git.add_files(["file.txt"])
    git.commit("Add file")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    # Move onto itself should fail
    result = runner.invoke(app, ["move", "--onto", "feature"])
    assert result.exit_code == 1
    assert "Cannot move branch onto itself" in result.output


def test_move_already_has_parent(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a tracked branch
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file.txt").write_text("content")
    git.add_files(["file.txt"])
    git.commit("Add file")
    add_notes(isolated_git_repo, json.dumps({"parent": "main"}), "feature")

    # Move to the same parent should be no-op
    result = runner.invoke(app, ["move", "--onto", "main"])
    assert result.exit_code == 0
    assert "already has parent" in result.output

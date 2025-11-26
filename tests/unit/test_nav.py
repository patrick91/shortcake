"""Tests for navigation commands."""

import json
from pathlib import Path

from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo

runner = CliRunner()


def test_up_help():
    result = runner.invoke(app, ["up", "--help"])
    assert result.exit_code == 0
    assert "parent branch" in result.output.lower()


def test_down_help():
    result = runner.invoke(app, ["down", "--help"])
    assert result.exit_code == 0
    assert "child branch" in result.output.lower()


def test_top_help():
    result = runner.invoke(app, ["top", "--help"])
    assert result.exit_code == 0
    assert "top of the stack" in result.output.lower()


def test_bottom_help():
    result = runner.invoke(app, ["bottom", "--help"])
    assert result.exit_code == 0
    assert "bottom of the stack" in result.output.lower()


def test_up_from_child_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create parent branch
    git.create_branch("parent-branch", checkout=True)
    (isolated_git_repo / "parent.txt").write_text("parent")
    git.add_files(["parent.txt"])
    git.commit("Add parent")
    git.add_notes(json.dumps({"parent": "main"}), "HEAD", "shortcake")

    # Create child branch
    git.create_branch("child-branch", checkout=True)
    (isolated_git_repo / "child.txt").write_text("child")
    git.add_files(["child.txt"])
    git.commit("Add child")
    git.add_notes(json.dumps({"parent": "parent-branch"}), "HEAD", "shortcake")

    # We're on child-branch, go up
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert "parent-branch" in result.output

    # Verify we switched
    assert git.get_current_branch() == "parent-branch"


def test_down_from_parent_branch(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create parent branch
    git.create_branch("parent-branch", checkout=True)
    (isolated_git_repo / "parent.txt").write_text("parent")
    git.add_files(["parent.txt"])
    git.commit("Add parent")
    git.add_notes(json.dumps({"parent": "main"}), "HEAD", "shortcake")

    # Create child branch
    git.create_branch("child-branch", checkout=True)
    (isolated_git_repo / "child.txt").write_text("child")
    git.add_files(["child.txt"])
    git.commit("Add child")
    git.add_notes(json.dumps({"parent": "parent-branch"}), "HEAD", "shortcake")

    # Go back to parent
    git.checkout_branch("parent-branch")

    # Go down to child
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "child-branch" in result.output

    # Verify we switched
    assert git.get_current_branch() == "child-branch"


def test_top_moves_to_leaf(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a stack: main -> branch1 -> branch2 -> branch3
    git.create_branch("branch1", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("1")
    git.add_files(["file1.txt"])
    git.commit("Add file1")
    git.add_notes(json.dumps({"parent": "main"}), "HEAD", "shortcake")

    git.create_branch("branch2", checkout=True)
    (isolated_git_repo / "file2.txt").write_text("2")
    git.add_files(["file2.txt"])
    git.commit("Add file2")
    git.add_notes(json.dumps({"parent": "branch1"}), "HEAD", "shortcake")

    git.create_branch("branch3", checkout=True)
    (isolated_git_repo / "file3.txt").write_text("3")
    git.add_files(["file3.txt"])
    git.commit("Add file3")
    git.add_notes(json.dumps({"parent": "branch2"}), "HEAD", "shortcake")

    # Go to branch1
    git.checkout_branch("branch1")

    # Go to top
    result = runner.invoke(app, ["top"])
    assert result.exit_code == 0
    assert "branch3" in result.output

    # Verify we're at branch3
    assert git.get_current_branch() == "branch3"


def test_bottom_moves_to_root(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a stack: main -> branch1 -> branch2 -> branch3
    git.create_branch("branch1", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("1")
    git.add_files(["file1.txt"])
    git.commit("Add file1")
    git.add_notes(json.dumps({"parent": "main"}), "HEAD", "shortcake")

    git.create_branch("branch2", checkout=True)
    (isolated_git_repo / "file2.txt").write_text("2")
    git.add_files(["file2.txt"])
    git.commit("Add file2")
    git.add_notes(json.dumps({"parent": "branch1"}), "HEAD", "shortcake")

    git.create_branch("branch3", checkout=True)
    (isolated_git_repo / "file3.txt").write_text("3")
    git.add_files(["file3.txt"])
    git.commit("Add file3")
    git.add_notes(json.dumps({"parent": "branch2"}), "HEAD", "shortcake")

    # We're on branch3, go to bottom
    result = runner.invoke(app, ["bottom"])
    assert result.exit_code == 0
    assert "branch1" in result.output

    # Verify we're at branch1
    assert git.get_current_branch() == "branch1"


def test_up_from_main(isolated_git_repo: Path, isolated_config: Path):
    # We're on main
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert "trunk" in result.output.lower()


def test_down_no_children(isolated_git_repo: Path, isolated_config: Path):
    git = GitRepo()

    # Create a branch with no children
    git.create_branch("leaf-branch", checkout=True)
    (isolated_git_repo / "leaf.txt").write_text("leaf")
    git.add_files(["leaf.txt"])
    git.commit("Add leaf")
    git.add_notes(json.dumps({"parent": "main"}), "HEAD", "shortcake")

    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "no child" in result.output.lower()

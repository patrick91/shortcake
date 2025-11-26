"""Integration tests for restack workflows."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from tests.helpers.git_helpers import get_notes

type GitEditorScript = Callable[[str], None]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def stage_all(repo_path: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)


def get_commit_sha(repo_path: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_commits_between(repo_path: Path, base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--oneline", f"{base}..{head}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


@pytest.mark.integration
def test_restack_after_parent_amended(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
) -> None:
    """Test restack when parent branch has been amended.

    This tests the scenario:
    1. Create branch-1 on main
    2. Create branch-2 on branch-1
    3. Go back to branch-1 and amend the commit
    4. Restack branch-2 - it should correctly rebase onto the new branch-1
    """
    git = GitRepo(isolated_git_repo)

    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1 original")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    branch1_original_sha = get_commit_sha(isolated_git_repo, "HEAD")

    # Create second branch stacked on first
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Verify branch-2's parent_revision points to original branch-1 SHA
    notes = get_notes(isolated_git_repo, "HEAD", "shortcake")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data["parent_revision"] == branch1_original_sha

    # Go back to branch-1 and amend the commit
    git.checkout_branch("add-feature-1")
    (isolated_git_repo / "feature1.txt").write_text("feature 1 amended")
    stage_all(isolated_git_repo)
    subprocess.run(
        ["git", "commit", "--amend", "-m", "Add feature 1 (amended)"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    branch1_new_sha = get_commit_sha(isolated_git_repo, "HEAD")
    assert branch1_new_sha != branch1_original_sha

    # Go to branch-2 and restack
    git.checkout_branch("add-feature-2")

    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "Rebasing add-feature-2" in result.output

    # Verify branch-2's parent_revision is now updated to new branch-1 SHA
    notes = get_notes(isolated_git_repo, "HEAD", "shortcake")
    assert notes is not None
    notes_data = json.loads(notes)
    assert notes_data["parent_revision"] == branch1_new_sha

    # Verify the commit history is correct (branch-2 has exactly 1 commit on top of branch-1)
    commits = get_commits_between(isolated_git_repo, "add-feature-1", "add-feature-2")
    assert len(commits) == 1
    assert "feature 2" in commits[0].lower()


@pytest.mark.integration
def test_restack_after_parent_rebased_onto_updated_main(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    remote_repo: Path,
    git_editor_script: GitEditorScript,
) -> None:
    """Test restack when parent branch was rebased onto updated main.

    This tests the scenario:
    1. Create branch-1 on main
    2. Create branch-2 on branch-1
    3. Main gets new commits (simulating remote updates)
    4. Rebase branch-1 onto updated main
    5. Restack from branch-2 - both should be correctly rebased
    """
    git = GitRepo(isolated_git_repo)

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Create second branch stacked on first
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Add new commit to main (simulating remote update)
    git.checkout_branch("main")
    (isolated_git_repo / "main_update.txt").write_text("main update")
    stage_all(isolated_git_repo)
    subprocess.run(
        ["git", "commit", "-m", "Update main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Go to branch-2 and restack the entire stack
    git.checkout_branch("add-feature-2")

    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0

    # Both branches should have been restacked
    assert "add-feature-1" in result.output
    assert "add-feature-2" in result.output

    # Verify the commit history includes the main update
    log_result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Update main" in log_result.stdout
    assert "feature 1" in log_result.stdout.lower()
    assert "feature 2" in log_result.stdout.lower()


@pytest.mark.integration
def test_restack_skips_up_to_date_branches(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
) -> None:
    """Test that restack skips branches that don't need rebasing.

    This verifies the parent_revision tracking works correctly to avoid
    unnecessary rebases.
    """
    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Create second branch
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Restack - both branches should be up to date
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "does not need to be restacked" in result.output or "up to date" in result.output

    # Run restack again - should still be up to date
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 0
    assert "does not need to be restacked" in result.output or "up to date" in result.output


@pytest.mark.integration
def test_adopt_force_updates_parent_revision(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    git_editor_script: GitEditorScript,
) -> None:
    """Test that adopt --force updates the parent_revision.

    This tests the fix for when parent_revision becomes stale and needs
    to be manually updated via adopt --force.
    """
    git = GitRepo(isolated_git_repo)

    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    branch1_sha = get_commit_sha(isolated_git_repo, "HEAD")

    # Create second branch
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Verify initial parent_revision
    notes = get_notes(isolated_git_repo, "HEAD", "shortcake")
    notes_data = json.loads(notes)
    assert notes_data["parent_revision"] == branch1_sha

    # Amend branch-1 (simulating it being rebased externally)
    git.checkout_branch("add-feature-1")
    (isolated_git_repo / "feature1.txt").write_text("feature 1 amended")
    stage_all(isolated_git_repo)
    subprocess.run(
        ["git", "commit", "--amend", "-m", "Add feature 1 (amended)"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )
    branch1_new_sha = get_commit_sha(isolated_git_repo, "HEAD")

    # Go back to branch-2
    git.checkout_branch("add-feature-2")

    # Use adopt --force to update parent_revision
    result = runner.invoke(app, ["adopt", "add-feature-2", "--force", "--parent", "add-feature-1"])
    assert result.exit_code == 0
    assert "Updated" in result.output

    # Verify parent_revision is now updated
    notes = get_notes(isolated_git_repo, "HEAD", "shortcake")
    notes_data = json.loads(notes)
    assert notes_data["parent_revision"] == branch1_new_sha

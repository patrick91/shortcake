"""Tests for empty commit handling during rebase operations.

These tests verify that the git CLI-based rebase properly handles empty commits
that can occur when:
1. Changes in a branch are already present in the target (e.g., after squash merge)
2. Conflict resolution results in no changes (user keeps HEAD version)
"""

import subprocess
from pathlib import Path

from shortcake import _git as git
from shortcake._git._rebase import RebaseResult, rebase_branch, rebase_continue
from shortcake._trailers import Trailers
from shortcake.commands.restack import _restack
from tests._git_helpers import Repo, add_paths, commit, switch_branch


def test_rebase_result_dataclass() -> None:
    """Test RebaseResult dataclass fields and defaults."""
    result = RebaseResult(success=True)
    assert result.success is True
    assert result.conflict is False
    assert result.skipped_empty is False
    assert result.error_output == ""

    result_with_conflict = RebaseResult(
        success=False, conflict=True, error_output="merge conflict"
    )
    assert result_with_conflict.success is False
    assert result_with_conflict.conflict is True
    assert result_with_conflict.error_output == "merge conflict"


def test_rebase_branch_normal(temp_repo: Repo, tmp_path: Path) -> None:
    """Test basic rebase works with git CLI."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    # Add a commit on feature
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    commit(temp_repo, b"feat: add feature")
    feature_sha = temp_repo.refs[b"refs/heads/feature"]

    # Add a commit to main
    switch_branch(temp_repo, "main")
    main_file = tmp_path / "main_update.txt"
    main_file.write_text("main update")
    add_paths(temp_repo, main_file)
    commit(temp_repo, b"chore: update main")
    main_new_sha = temp_repo.refs[b"refs/heads/main"]

    # Rebase feature onto main
    result = rebase_branch(temp_repo, "feature", "main", main_sha.decode())

    assert result.success is True
    assert result.conflict is False
    assert result.skipped_empty is False

    # Verify feature is now on top of main
    new_feature_sha = temp_repo.refs[b"refs/heads/feature"]
    assert new_feature_sha != feature_sha
    # Feature's parent should now be main
    feature_commit = temp_repo[new_feature_sha]
    assert feature_commit.parents[0] == main_new_sha


def test_rebase_branch_skips_empty_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test that rebase with --empty=drop skips empty commits."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    # Add a change on feature
    readme = tmp_path / "README.md"
    readme.write_text("# Test\nmodified")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"feat: modify readme")

    # Add same change to main (simulating squash merge)
    switch_branch(temp_repo, "main")
    readme.write_text("# Test\nmodified")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"squash: same change")

    # Rebase feature onto main - the commit should be empty
    switch_branch(temp_repo, "feature")
    result = rebase_branch(temp_repo, "feature", "main", main_sha.decode())

    assert result.success is True
    assert result.skipped_empty is True


def test_reproduce_content_loss_bug(temp_repo: Repo, tmp_path: Path) -> None:
    """
    Reproduce the bug where content is lost during empty commit handling.

    Scenario:
    1. Create feature branch with changes to file.txt AND important.txt
    2. On main, make same changes to file.txt only (simulating partial squash)
    3. Rebase feature onto main
    4. Old behavior: dulwich creates empty commit, content lost
    5. New behavior: git rebase --empty=drop skips empty parts, content preserved
    """
    # Initial state
    file_txt = tmp_path / "file.txt"
    file_txt.write_text("initial\n")
    add_paths(temp_repo, file_txt)
    commit(temp_repo, b"initial file.txt")
    main_base_sha = temp_repo.refs[b"refs/heads/main"]

    # Feature branch with important changes
    temp_repo.refs[b"refs/heads/feature"] = main_base_sha
    switch_branch(temp_repo, "feature")
    file_txt.write_text("initial\nfeature line 1\nfeature line 2\n")
    important_txt = tmp_path / "important.txt"
    important_txt.write_text("important work\n")
    add_paths(temp_repo, file_txt, important_txt)
    commit(temp_repo, b"feat: add feature")

    # Main gets same file.txt change (simulating squash that included file.txt)
    switch_branch(temp_repo, "main")
    file_txt.write_text("initial\nfeature line 1\nfeature line 2\n")
    add_paths(temp_repo, file_txt)
    commit(temp_repo, b"squash merged")

    # Rebase feature onto main
    switch_branch(temp_repo, "feature")
    result = rebase_branch(temp_repo, "feature", "main", main_base_sha.decode())

    # IMPORTANT: Verify content is NOT lost
    assert result.success is True
    # The important.txt should still be there - this was the bug!
    assert important_txt.exists(), "important.txt should exist!"
    assert important_txt.read_text() == "important work\n"


def test_rebase_branch_with_conflict(temp_repo: Repo, tmp_path: Path) -> None:
    """Test that conflicts are properly detected during rebase."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    # Modify README on feature
    readme = tmp_path / "README.md"
    readme.write_text("# Feature Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"feat: modify readme")

    # Modify same file differently on main
    switch_branch(temp_repo, "main")
    readme.write_text("# Main Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"chore: update readme")

    # Rebase feature onto main - should conflict
    result = rebase_branch(temp_repo, "feature", "main", main_sha.decode())

    assert result.success is False
    assert result.conflict is True
    assert git.is_rebase_in_progress(temp_repo)


def test_rebase_continue_success(temp_repo: Repo, tmp_path: Path) -> None:
    """Test rebase continue after resolving conflict."""
    # Create conflicting scenario
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    readme = tmp_path / "README.md"
    readme.write_text("# Feature Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"feat: modify readme")

    switch_branch(temp_repo, "main")
    readme.write_text("# Main Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"chore: update readme")

    # Start rebase - will conflict
    result = rebase_branch(temp_repo, "feature", "main", main_sha.decode())
    assert result.conflict is True

    # Resolve the conflict
    readme.write_text("# Merged Version")
    subprocess.run(["git", "add", str(readme)], cwd=tmp_path, check=True)

    # Continue the rebase
    continue_result = rebase_continue(temp_repo)
    assert continue_result.success is True
    assert not git.is_rebase_in_progress(temp_repo)


def test_rebase_continue_empty_after_conflict_resolution(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """
    Test that sc continue handles conflict resolution correctly.

    When resolving a conflict by keeping the target's version (ours during rebase),
    git rebase --continue succeeds directly because the resolution is staged.
    The result may or may not be flagged as skipped_empty depending on git's output.

    Note: During a rebase, "ours" refers to the rebase target (the branch we're
    rebasing onto), while "theirs" refers to the branch being rebased.
    """
    # Create conflicting scenario
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    readme = tmp_path / "README.md"
    readme.write_text("# Feature Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"feat: modify readme")

    switch_branch(temp_repo, "main")
    readme.write_text("# Main Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"chore: update readme")

    # Start rebase - will conflict
    result = rebase_branch(temp_repo, "feature", "main", main_sha.decode())
    assert result.conflict is True

    # Resolve conflict by keeping ours (which is main's version during rebase)
    subprocess.run(["git", "checkout", "--ours", str(readme)], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", str(readme)], cwd=tmp_path, check=True)

    # Continue should succeed (git handles this gracefully)
    continue_result = rebase_continue(temp_repo)
    assert continue_result.success is True
    assert not git.is_rebase_in_progress(temp_repo)


def test_rebase_abort_after_conflict(temp_repo: Repo, tmp_path: Path) -> None:
    """Test that abort works after conflict."""
    # Create conflicting scenario
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    readme = tmp_path / "README.md"
    readme.write_text("# Feature Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"feat: modify readme")
    feature_sha = temp_repo.refs[b"refs/heads/feature"]

    switch_branch(temp_repo, "main")
    readme.write_text("# Main Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"chore: update readme")

    # Start rebase - will conflict
    result = rebase_branch(temp_repo, "feature", "main", main_sha.decode())
    assert result.conflict is True
    assert git.is_rebase_in_progress(temp_repo)

    # Abort the rebase
    git.rebase_abort(temp_repo)
    assert not git.is_rebase_in_progress(temp_repo)

    # Feature branch should be restored
    # Note: git rebase --abort restores the branch to original position
    assert temp_repo.refs[b"refs/heads/feature"] == feature_sha


def test_restack_with_empty_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _restack properly handles and reports empty commits."""
    # Create tracked feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    # Add a change on feature with trailer
    readme = tmp_path / "README.md"
    readme.write_text("# Test\nmodified")
    add_paths(temp_repo, readme)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: modify readme")
    commit(temp_repo, message)

    # Add same change to main (simulating squash merge)
    switch_branch(temp_repo, "main")
    readme.write_text("# Test\nmodified")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"squash: same change")

    # Switch back to feature and restack
    switch_branch(temp_repo, "feature")
    result = _restack(temp_repo)

    assert result.restacked_branches == ["feature"]
    assert result.conflict_branch is None
    assert result.skipped_empty_commits is True

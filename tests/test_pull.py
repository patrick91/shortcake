"""Tests for pull command."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.pull import (
    PullError,
    _ensure_stack_branches_local,
    _fetch,
    _find_trailer_parent,
    _pull,
    _pull_single_after_fetch,
    _pull_stack,
    _reset_to_remote,
    _update_branch_from_remote,
    pull,  # noqa: F401 - imported for coverage
)
from tests._git_helpers import Repo, add_paths, commit, switch_branch

# Tests for _pull


def test_pull_already_up_to_date(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull when already up to date."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create remote ref at same position as local
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = local_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull(repo_with_feature)

    assert result.branch == "feature"
    assert result.already_up_to_date is True
    assert result.fast_forwarded is False


def test_pull_fast_forward(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull when local is behind remote (fast-forward)."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a new commit to simulate remote being ahead
    new_file = tmp_path / "remote_change.txt"
    new_file.write_text("remote change")
    add_paths(repo_with_feature, new_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Reset local branch back to original position
    repo_with_feature.refs[b"refs/heads/feature"] = local_sha
    switch_branch(repo_with_feature, "feature")

    # Set up remote ref at the newer position
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull(repo_with_feature)

    assert result.branch == "feature"
    assert result.fast_forwarded is True
    assert result.new_sha is not None
    # Verify local branch was updated
    assert repo_with_feature.refs[b"refs/heads/feature"] == remote_sha


def test_pull_diverged_resets_by_default(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test pull resets to remote by default when branches have diverged."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Save original sha
    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a local commit
    local_file = tmp_path / "local_change.txt"
    local_file.write_text("local change")
    add_paths(repo_with_feature, local_file)
    commit(repo_with_feature, b"Local change")

    # Create a different commit on "remote" (from original position)
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    add_paths(repo_with_feature, remote_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/temp"]

    # Set up remote ref
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    # Switch back to feature
    switch_branch(repo_with_feature, "feature")
    del repo_with_feature.refs[b"refs/heads/temp"]

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull(repo_with_feature)

    assert result.branch == "feature"
    assert result.reset is True
    assert result.new_sha is not None
    # Verify local branch was reset to remote
    assert repo_with_feature.refs[b"refs/heads/feature"] == remote_sha


def test_pull_diverged_with_rebase(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull with --rebase when branches have diverged."""
    import subprocess

    # Set up git user config
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )

    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Save original sha
    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a local commit (different file to avoid conflict)
    local_file = tmp_path / "local_change.txt"
    local_file.write_text("local change")
    add_paths(repo_with_feature, local_file)
    commit(repo_with_feature, b"Local change")
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create "remote" commit from original position
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    add_paths(repo_with_feature, remote_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/temp"]

    # Set up remote ref
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    # Switch back to feature (need to restore local commit)
    repo_with_feature.refs[b"refs/heads/feature"] = local_sha
    switch_branch(repo_with_feature, "feature")
    # Delete temp branch
    del repo_with_feature.refs[b"refs/heads/temp"]

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull(repo_with_feature, rebase=True)

    assert result.branch == "feature"
    assert result.rebased is True
    assert result.new_sha is not None


def test_pull_rebase_conflict(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test error when rebase has conflicts."""
    import subprocess

    # Set up git user config
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )

    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Save original sha
    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a local commit modifying the same file
    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text("local version")
    add_paths(repo_with_feature, conflict_file)
    commit(repo_with_feature, b"Local change")
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create "remote" commit modifying the same file
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    conflict_file.write_text("remote version")
    add_paths(repo_with_feature, conflict_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/temp"]

    # Set up remote ref
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    # Switch back to feature
    repo_with_feature.refs[b"refs/heads/feature"] = local_sha
    switch_branch(repo_with_feature, "feature")
    del repo_with_feature.refs[b"refs/heads/temp"]

    with (
        patch("shortcake.commands.pull._fetch", return_value=True),
        pytest.raises(PullError, match="Conflict during rebase"),
    ):
        _pull(repo_with_feature, rebase=True)


def test_pull_no_remote(temp_repo: Repo) -> None:
    """Test error when no remote configured."""
    with pytest.raises(PullError, match="No remote 'origin' configured"):
        _pull(temp_repo)


def test_pull_no_remote_tracking_branch(repo_with_feature: Repo) -> None:
    """Test error when branch has no remote tracking branch."""
    # Set up origin remote but no remote tracking branch for feature
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    with (
        patch("shortcake.commands.pull._fetch", return_value=True),
        pytest.raises(PullError, match="No remote tracking branch"),
    ):
        _pull(repo_with_feature)


def test_pull_uncommitted_changes(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test error when uncommitted changes exist."""
    # Create uncommitted changes
    (tmp_path / "uncommitted.txt").write_text("uncommitted")
    add_paths(repo_with_feature, tmp_path / "uncommitted.txt")

    with pytest.raises(PullError, match="uncommitted changes"):
        _pull(repo_with_feature)


def test_pull_detached_head(temp_repo: Repo, tmp_path: Path) -> None:
    """Test error when on detached HEAD."""
    # Detach HEAD by writing SHA directly to HEAD file
    head_sha = temp_repo.head()
    head_file = tmp_path / ".git" / "HEAD"
    head_file.write_text(head_sha.decode() + "\n")

    with pytest.raises(PullError, match="detached HEAD"):
        _pull(temp_repo)


def test_pull_rebase_in_progress(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test error when rebase is in progress."""
    # Create rebase state directory
    git_dir = tmp_path / ".git"
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir(parents=True)

    with pytest.raises(PullError, match="rebase in progress"):
        _pull(repo_with_feature)


def test_pull_fetch_fails(repo_with_feature: Repo) -> None:
    """Test error when fetch fails."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    with (
        patch("shortcake.commands.pull._fetch", return_value=False),
        pytest.raises(PullError, match="Failed to fetch"),
    ):
        _pull(repo_with_feature)


# Tests for helper functions


def test_fetch_no_remote(temp_repo: Repo) -> None:
    """Test _fetch returns False when no remote."""
    result = _fetch(temp_repo)
    assert result is False


def test_fetch_success(repo_with_feature: Repo) -> None:
    """Test _fetch returns True on success."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    with patch("shortcake.commands.pull.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        result = _fetch(repo_with_feature)

    assert result is True
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0] == ["git", "fetch", "origin"]


def test_fetch_failure(repo_with_feature: Repo) -> None:
    """Test _fetch returns False on failure."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    with patch("shortcake.commands.pull.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        result = _fetch(repo_with_feature)

    assert result is False


def test_reset_to_remote(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test _reset_to_remote resets branch to remote."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Save original sha
    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a local commit
    local_file = tmp_path / "local_change.txt"
    local_file.write_text("local change")
    add_paths(repo_with_feature, local_file)
    commit(repo_with_feature, b"Local change")

    # Set up remote ref at original position (simulating remote is behind)
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = original_sha

    # Reset to remote
    _reset_to_remote(repo_with_feature, "feature")

    # Verify branch was reset
    assert repo_with_feature.refs[b"refs/heads/feature"] == original_sha


# CLI tests

runner = CliRunner()


def test_pull_cli_already_up_to_date(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull CLI when already up to date."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create remote ref at same position as local
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = local_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_pull_cli_fast_forward(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull CLI when fast-forward is possible."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a new commit to simulate remote being ahead
    new_file = tmp_path / "remote_change.txt"
    new_file.write_text("remote change")
    add_paths(repo_with_feature, new_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Reset local branch back to original position
    repo_with_feature.refs[b"refs/heads/feature"] = local_sha
    switch_branch(repo_with_feature, "feature")

    # Set up remote ref at the newer position
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Updated" in result.output


def test_pull_cli_error(temp_repo: Repo, tmp_path: Path) -> None:
    """Test pull CLI error handling."""
    os.chdir(tmp_path)
    result = runner.invoke(app, ["pull"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_pull_cli_reset_by_default(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull CLI resets to remote by default when diverged."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Save original sha
    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a local commit
    local_file = tmp_path / "local_change.txt"
    local_file.write_text("local change")
    add_paths(repo_with_feature, local_file)
    commit(repo_with_feature, b"Local change")

    # Create a different commit on "remote" (from original position)
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    add_paths(repo_with_feature, remote_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/temp"]

    # Set up remote ref
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    # Switch back to feature
    switch_branch(repo_with_feature, "feature")
    del repo_with_feature.refs[b"refs/heads/temp"]

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Updated" in result.output


def test_pull_cli_rebase_flag(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull CLI with --rebase flag."""
    import subprocess

    # Set up git user config
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )

    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Save original sha
    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a local commit (different file to avoid conflict)
    local_file = tmp_path / "local_change.txt"
    local_file.write_text("local change")
    add_paths(repo_with_feature, local_file)
    commit(repo_with_feature, b"Local change")
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create "remote" commit from original position
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    add_paths(repo_with_feature, remote_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/temp"]

    # Set up remote ref
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    # Switch back to feature
    repo_with_feature.refs[b"refs/heads/feature"] = local_sha
    switch_branch(repo_with_feature, "feature")
    del repo_with_feature.refs[b"refs/heads/temp"]

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull", "--rebase"])

    assert result.exit_code == 0
    assert "Rebased" in result.output


# Tests for _update_branch_from_remote


def test_update_branch_from_remote_no_remote_ref(repo_with_feature: Repo) -> None:
    """Test _update_branch_from_remote when no remote ref exists."""
    result = _update_branch_from_remote(repo_with_feature, "feature")
    assert result.skipped_no_remote is True
    assert result.updated is False


def test_update_branch_from_remote_already_up_to_date(
    repo_with_feature: Repo,
) -> None:
    """Test _update_branch_from_remote when already up to date."""
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = local_sha

    result = _update_branch_from_remote(repo_with_feature, "feature")
    assert result.already_up_to_date is True
    assert result.updated is False


def test_update_branch_from_remote_updated(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test _update_branch_from_remote when remote is ahead."""
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a new commit to simulate remote being ahead
    new_file = tmp_path / "remote_change.txt"
    new_file.write_text("remote change")
    add_paths(repo_with_feature, new_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Reset local branch back
    repo_with_feature.refs[b"refs/heads/feature"] = local_sha

    # Set remote ref
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    result = _update_branch_from_remote(repo_with_feature, "feature")
    assert result.updated is True
    assert result.new_sha is not None
    # Verify local ref was updated
    assert repo_with_feature.refs[b"refs/heads/feature"] == remote_sha


# Tests for _pull_stack


def test_pull_stack_all_up_to_date(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _pull_stack when all branches are up to date."""
    # Set up origin remote
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Set remote refs at same position as local for both stack branches
    for branch in ["branch_a", "branch_b"]:
        local_sha = repo_with_stack.refs[f"refs/heads/{branch}".encode()]
        repo_with_stack.refs[f"refs/remotes/origin/{branch}".encode()] = local_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull_stack(repo_with_stack)

    assert result.original_branch == "branch_b"
    assert len(result.branch_results) == 2
    assert all(br.already_up_to_date for br in result.branch_results)
    assert result.restack_result is None


def test_pull_stack_some_updated(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _pull_stack when some branches have remote updates."""
    # Set up origin remote
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # branch_a: up to date
    branch_a_sha = repo_with_stack.refs[b"refs/heads/branch_a"]
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    # branch_b: create a different remote commit (diverged)
    branch_b_sha = repo_with_stack.refs[b"refs/heads/branch_b"]
    # Switch to branch_b, create a new commit, use it as remote
    new_file = tmp_path / "remote_b_change.txt"
    new_file.write_text("remote change on b")
    add_paths(repo_with_stack, new_file)
    commit(repo_with_stack, b"Remote change on b")
    remote_b_sha = repo_with_stack.refs[b"refs/heads/branch_b"]

    # Reset local branch_b back
    repo_with_stack.refs[b"refs/heads/branch_b"] = branch_b_sha
    switch_branch(repo_with_stack, "branch_b")

    # Set remote ref
    repo_with_stack.refs[b"refs/remotes/origin/branch_b"] = remote_b_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull_stack(repo_with_stack)

    assert result.original_branch == "branch_b"
    assert len(result.branch_results) == 2
    # branch_a is up to date
    assert result.branch_results[0].already_up_to_date is True
    # branch_b was updated
    assert result.branch_results[1].updated is True
    assert result.branch_results[1].new_sha is not None


def test_pull_stack_skip_no_remote(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _pull_stack skips branches with no remote tracking ref."""
    # Set up origin remote
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Only set remote ref for branch_a, not branch_b
    branch_a_sha = repo_with_stack.refs[b"refs/heads/branch_a"]
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull_stack(repo_with_stack)

    assert len(result.branch_results) == 2
    assert result.branch_results[0].already_up_to_date is True
    assert result.branch_results[1].skipped_no_remote is True


def test_pull_stack_untracked_fallback(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test _pull_stack falls back to single-branch when on untracked branch."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create remote ref at same position
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = local_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull_stack(repo_with_feature)

    assert result.original_branch == "feature"
    assert len(result.branch_results) == 1
    assert result.branch_results[0].already_up_to_date is True


def test_pull_stack_untracked_fallback_updated(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test _pull_stack fallback updates when remote is ahead."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create remote ahead
    new_file = tmp_path / "remote_change.txt"
    new_file.write_text("remote change")
    add_paths(repo_with_feature, new_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/feature"]

    repo_with_feature.refs[b"refs/heads/feature"] = local_sha
    switch_branch(repo_with_feature, "feature")
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull_stack(repo_with_feature)

    assert result.branch_results[0].updated is True


def test_pull_stack_detached_head(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _pull_stack error on detached HEAD."""
    head_sha = temp_repo.head()
    head_file = tmp_path / ".git" / "HEAD"
    head_file.write_text(head_sha.decode() + "\n")

    with pytest.raises(PullError, match="detached HEAD"):
        _pull_stack(temp_repo)


def test_pull_stack_uncommitted_changes(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _pull_stack error with uncommitted changes."""
    (tmp_path / "uncommitted.txt").write_text("uncommitted")
    add_paths(repo_with_stack, tmp_path / "uncommitted.txt")

    with pytest.raises(PullError, match="uncommitted changes"):
        _pull_stack(repo_with_stack)


def test_pull_stack_no_remote(repo_with_stack: Repo) -> None:
    """Test _pull_stack error when no remote configured."""
    with pytest.raises(PullError, match="No remote 'origin' configured"):
        _pull_stack(repo_with_stack)


def test_pull_stack_fetch_fails(repo_with_stack: Repo) -> None:
    """Test _pull_stack error when fetch fails."""
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    with (
        patch("shortcake.commands.pull._fetch", return_value=False),
        pytest.raises(PullError, match="Failed to fetch"),
    ):
        _pull_stack(repo_with_stack)


def test_pull_stack_rebase_in_progress(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _pull_stack error when rebase is in progress."""
    rebase_dir = tmp_path / ".git" / "rebase-merge"
    rebase_dir.mkdir(parents=True)

    with pytest.raises(PullError, match="rebase in progress"):
        _pull_stack(repo_with_stack)


def test_pull_stack_returns_to_original_branch(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test _pull_stack returns to the original branch after pull."""
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Switch to branch_a first
    switch_branch(repo_with_stack, "branch_a")

    # Set remote refs at same position
    for branch in ["branch_a", "branch_b"]:
        local_sha = repo_with_stack.refs[f"refs/heads/{branch}".encode()]
        repo_with_stack.refs[f"refs/remotes/origin/{branch}".encode()] = local_sha

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull_stack(repo_with_stack)

    assert result.original_branch == "branch_a"
    # Verify we're still on branch_a
    current = repo_with_stack.refs.get_symrefs().get(b"HEAD", b"").decode()
    assert current == "refs/heads/branch_a"


def test_pull_stack_working_tree_updated(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _pull_stack updates working tree for current branch."""
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create a remote commit on branch_b with new content
    branch_b_sha = repo_with_stack.refs[b"refs/heads/branch_b"]
    new_file = tmp_path / "new_remote_file.txt"
    new_file.write_text("new remote content")
    add_paths(repo_with_stack, new_file)
    commit(repo_with_stack, b"Remote: add new file")
    remote_b_sha = repo_with_stack.refs[b"refs/heads/branch_b"]

    # Reset local branch_b back
    repo_with_stack.refs[b"refs/heads/branch_b"] = branch_b_sha
    switch_branch(repo_with_stack, "branch_b")

    # Set remote refs
    branch_a_sha = repo_with_stack.refs[b"refs/heads/branch_a"]
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha
    repo_with_stack.refs[b"refs/remotes/origin/branch_b"] = remote_b_sha

    # The new file should not exist yet
    assert not new_file.exists()

    with patch("shortcake.commands.pull._fetch", return_value=True):
        _pull_stack(repo_with_stack)

    # After pull, working tree should include the new file
    assert new_file.exists()
    assert new_file.read_text() == "new remote content"


# Tests for _pull_single_after_fetch


def test_pull_single_after_fetch_no_remote(repo_with_feature: Repo) -> None:
    """Test _pull_single_after_fetch error when no remote ref."""
    with pytest.raises(PullError, match="No remote tracking branch"):
        _pull_single_after_fetch(repo_with_feature, "feature")


def test_pull_single_after_fetch_up_to_date(repo_with_feature: Repo) -> None:
    """Test _pull_single_after_fetch when up to date."""
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = local_sha

    result = _pull_single_after_fetch(repo_with_feature, "feature")
    assert result.already_up_to_date is True


def test_pull_single_after_fetch_fast_forward(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test _pull_single_after_fetch fast-forwards."""
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    new_file = tmp_path / "remote_change.txt"
    new_file.write_text("remote change")
    add_paths(repo_with_feature, new_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/feature"]

    repo_with_feature.refs[b"refs/heads/feature"] = local_sha
    switch_branch(repo_with_feature, "feature")
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    result = _pull_single_after_fetch(repo_with_feature, "feature")
    assert result.fast_forwarded is True


def test_pull_single_after_fetch_diverged_resets(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test _pull_single_after_fetch resets on divergence."""
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Local commit
    local_file = tmp_path / "local_change.txt"
    local_file.write_text("local change")
    add_paths(repo_with_feature, local_file)
    commit(repo_with_feature, b"Local change")

    # Remote commit from original
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    add_paths(repo_with_feature, remote_file)
    commit(repo_with_feature, b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/temp"]

    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha
    switch_branch(repo_with_feature, "feature")
    del repo_with_feature.refs[b"refs/heads/temp"]

    result = _pull_single_after_fetch(repo_with_feature, "feature")
    assert result.reset is True


# CLI tests for stack pull


def test_pull_cli_rebase_error(temp_repo: Repo, tmp_path: Path) -> None:
    """Test CLI pull --rebase error handling."""
    os.chdir(tmp_path)
    result = runner.invoke(app, ["pull", "--rebase"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_pull_cli_rebase_already_up_to_date(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test CLI pull --rebase when already up to date."""
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    local_sha = repo_with_feature.refs[b"refs/heads/feature"]
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = local_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull", "--rebase"])

    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_pull_cli_stack_all_up_to_date(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test CLI pull with stack all up to date."""
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    for branch in ["branch_a", "branch_b"]:
        local_sha = repo_with_stack.refs[f"refs/heads/{branch}".encode()]
        repo_with_stack.refs[f"refs/remotes/origin/{branch}".encode()] = local_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Checked 2 branches in stack" in result.output
    assert "Already up to date" in result.output


def test_pull_cli_stack_with_updates(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test CLI pull with stack when some branches are updated."""
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # branch_a: up to date
    branch_a_sha = repo_with_stack.refs[b"refs/heads/branch_a"]
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    # branch_b: ahead on remote
    branch_b_sha = repo_with_stack.refs[b"refs/heads/branch_b"]
    new_file = tmp_path / "remote_b_change.txt"
    new_file.write_text("remote change on b")
    add_paths(repo_with_stack, new_file)
    commit(repo_with_stack, b"Remote change on b")
    remote_b_sha = repo_with_stack.refs[b"refs/heads/branch_b"]

    repo_with_stack.refs[b"refs/heads/branch_b"] = branch_b_sha
    switch_branch(repo_with_stack, "branch_b")
    repo_with_stack.refs[b"refs/remotes/origin/branch_b"] = remote_b_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Updated 'branch_b'" in result.output


def test_pull_cli_stack_skip_no_remote(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test CLI pull shows skip message for branches without remote."""
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Only set remote for branch_a, not branch_b
    branch_a_sha = repo_with_stack.refs[b"refs/heads/branch_a"]
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Skipped 'branch_b'" in result.output


def test_pull_cli_stack_with_restack(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test CLI pull shows restack count when branches are restacked."""
    config = repo_with_stack.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create a new "remote" version of branch_a with an extra commit
    branch_a_sha = repo_with_stack.refs[b"refs/heads/branch_a"]
    switch_branch(repo_with_stack, "branch_a")
    extra_file = tmp_path / "extra_a.txt"
    extra_file.write_text("extra on a")
    add_paths(repo_with_stack, extra_file)
    trailers_a = Trailers(parent_branch="main")
    msg = trailers_a.apply_to("feat: extra on branch a")
    commit(repo_with_stack, msg)
    remote_a_sha = repo_with_stack.refs[b"refs/heads/branch_a"]

    # Reset local branch_a back
    repo_with_stack.refs[b"refs/heads/branch_a"] = branch_a_sha

    # Set remote refs
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = remote_a_sha
    branch_b_sha = repo_with_stack.refs[b"refs/heads/branch_b"]
    repo_with_stack.refs[b"refs/remotes/origin/branch_b"] = branch_b_sha

    # Switch back to branch_b
    switch_branch(repo_with_stack, "branch_b")

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Updated 'branch_a'" in result.output
    assert "Restacked" in result.output


# Tests for _ensure_stack_branches_local


def test_ensure_stack_all_local(repo_with_stack: Repo) -> None:
    """Test _ensure_stack_branches_local when all branches exist locally."""
    created = _ensure_stack_branches_local(repo_with_stack, "branch_b")
    assert created == []


def test_ensure_stack_creates_parent_from_remote(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test creating a missing parent branch from remote."""
    # Create branch_a locally with trailer pointing to main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit(temp_repo, msg_a)
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b locally with trailer pointing to branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b content")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    commit(temp_repo, msg_b)

    # Now delete branch_a locally but keep it as remote ref
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha
    del temp_repo.refs[b"refs/heads/branch_a"]

    # Verify branch_a doesn't exist locally
    assert b"refs/heads/branch_a" not in temp_repo.refs

    # _ensure_stack_branches_local should create it
    created = _ensure_stack_branches_local(temp_repo, "branch_b")
    assert "branch_a" in created
    assert b"refs/heads/branch_a" in temp_repo.refs


def test_ensure_stack_creates_child_from_remote(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test creating a missing child branch from remote."""
    # Create branch_a locally
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit(temp_repo, msg_a)
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b commit (will only exist on remote)
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b content")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    commit(temp_repo, msg_b)
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # Move branch_b to remote only
    temp_repo.refs[b"refs/remotes/origin/branch_b"] = branch_b_sha
    switch_branch(temp_repo, "branch_a")
    del temp_repo.refs[b"refs/heads/branch_b"]

    # Verify branch_b doesn't exist locally
    assert b"refs/heads/branch_b" not in temp_repo.refs

    # _ensure_stack_branches_local should create it (found via remote children)
    created = _ensure_stack_branches_local(temp_repo, "branch_a")
    assert "branch_b" in created
    assert b"refs/heads/branch_b" in temp_repo.refs


def test_ensure_stack_start_not_local(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _ensure_stack_branches_local when start branch doesn't exist locally."""
    # Create branch_a locally with trailer pointing to main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit(temp_repo, msg_a)
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Delete branch_a locally but keep it as remote ref
    switch_branch(temp_repo, "main")
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha
    del temp_repo.refs[b"refs/heads/branch_a"]

    # Verify branch_a doesn't exist locally
    assert b"refs/heads/branch_a" not in temp_repo.refs

    # Call with start=branch_a which doesn't exist locally
    created = _ensure_stack_branches_local(temp_repo, "branch_a")
    assert "branch_a" in created
    assert b"refs/heads/branch_a" in temp_repo.refs


def test_ensure_stack_no_remote(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _ensure_stack_branches_local when parent has no remote."""
    # Create branch_a with trailer pointing to nonexistent parent
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="nonexistent")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit(temp_repo, msg_a)

    # No remote ref for nonexistent — should not crash
    created = _ensure_stack_branches_local(temp_repo, "branch_a")
    assert created == []


def test_ensure_stack_does_not_pull_sibling_stacks(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Ensure branches from sibling stacks are not pulled in.

    When walking up to trunk (main), we should NOT pull in children
    of main that belong to different stacks.
    """
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # Create stack 1: main → branch_a
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit(temp_repo, msg_a)

    # Create stack 2 (only on remote): main → other_branch
    temp_repo.refs[b"refs/heads/other_branch"] = main_sha
    switch_branch(temp_repo, "other_branch")
    file_other = tmp_path / "other.txt"
    file_other.write_text("other content")
    add_paths(temp_repo, file_other)
    trailers_other = Trailers(parent_branch="main")
    msg_other = trailers_other.apply_to("feat: other branch")
    commit(temp_repo, msg_other)
    other_sha = temp_repo.refs[b"refs/heads/other_branch"]

    # Move other_branch to remote only
    temp_repo.refs[b"refs/remotes/origin/other_branch"] = other_sha
    switch_branch(temp_repo, "branch_a")
    del temp_repo.refs[b"refs/heads/other_branch"]

    # Ensure stack from branch_a should NOT create other_branch
    created = _ensure_stack_branches_local(temp_repo, "branch_a")
    assert "other_branch" not in created
    assert b"refs/heads/other_branch" not in temp_repo.refs


def test_pull_stack_creates_missing_branches(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _pull_stack creates missing stack branches from remote."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create branch_a locally with trailer
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit(temp_repo, msg_a)
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b commit (will be remote only)
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b content")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    commit(temp_repo, msg_b)
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # Set up remote refs
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha
    temp_repo.refs[b"refs/remotes/origin/branch_b"] = branch_b_sha

    # Delete branch_b locally
    switch_branch(temp_repo, "branch_a")
    del temp_repo.refs[b"refs/heads/branch_b"]

    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = _pull_stack(temp_repo)

    assert result.is_stack is True
    # branch_b should have been created from remote
    assert any(br.created_from_remote for br in result.branch_results)
    assert b"refs/heads/branch_b" in temp_repo.refs


def test_pull_cli_creates_missing_branches(temp_repo: Repo, tmp_path: Path) -> None:
    """Test CLI pull creates missing stack branches from remote."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create branch_a locally with trailer
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit(temp_repo, msg_a)
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b commit (will be remote only)
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b content")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    commit(temp_repo, msg_b)
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # Set up remote refs
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha
    temp_repo.refs[b"refs/remotes/origin/branch_b"] = branch_b_sha

    # Delete branch_b locally, stay on branch_a
    switch_branch(temp_repo, "branch_a")
    del temp_repo.refs[b"refs/heads/branch_b"]

    os.chdir(tmp_path)
    with patch("shortcake.commands.pull._fetch", return_value=True):
        result = runner.invoke(app, ["pull"])

    assert result.exit_code == 0
    assert "Created 'branch_b' from origin/branch_b" in result.output


# Tests for _find_trailer_parent


def test_find_trailer_parent_single_commit(temp_repo: Repo, tmp_path: Path) -> None:
    """Test finding trailer in a single-commit branch (trailer is in HEAD)."""
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers = Trailers(parent_branch="main")
    commit(temp_repo, trailers.apply_to("feat: feature"))

    head_sha = temp_repo.refs[b"refs/heads/feature"]
    result = _find_trailer_parent(temp_repo, head_sha, set())

    assert result == "main"


def test_find_trailer_parent_multi_commit(temp_repo: Repo, tmp_path: Path) -> None:
    """Test finding trailer in a multi-commit branch (trailer is in base)."""
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    # First commit has trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers = Trailers(parent_branch="main")
    commit(temp_repo, trailers.apply_to("feat: first"))

    # Second commit has no trailer
    file_b = tmp_path / "b.txt"
    file_b.write_text("b content")
    add_paths(temp_repo, file_b)
    commit(temp_repo, b"feat: second commit")

    # Third commit (HEAD) has no trailer
    file_c = tmp_path / "c.txt"
    file_c.write_text("c content")
    add_paths(temp_repo, file_c)
    commit(temp_repo, b"feat: third commit")

    head_sha = temp_repo.refs[b"refs/heads/feature"]
    result = _find_trailer_parent(temp_repo, head_sha, set())

    assert result == "main"


def test_find_trailer_parent_stops_at_known_head(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test walk stops at known branch heads."""
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    # Commit with no trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    commit(temp_repo, b"feat: no trailer")

    head_sha = temp_repo.refs[b"refs/heads/feature"]
    # main_sha is a known head — walker should stop there, not walk further
    result = _find_trailer_parent(temp_repo, head_sha, {main_sha})

    assert result is None


def test_find_trailer_parent_no_trailer(temp_repo: Repo, tmp_path: Path) -> None:
    """Test returns None when no trailer found in any commit."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # main has no trailer and no parents (initial commit)
    result = _find_trailer_parent(temp_repo, main_sha, set())

    assert result is None


# Tests for _ensure_children_from_remote with multi-commit branches


def test_ensure_children_discovers_multi_commit_remote_branch(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test _ensure_children_from_remote finds branches with trailer in base commit."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # Create branch_a locally with trailer pointing to main
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    commit(temp_repo, trailers_a.apply_to("feat: branch a"))
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b with trailer pointing to branch_a, then add more commits
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b content")
    add_paths(temp_repo, file_b)
    trailers_b = Trailers(parent_branch="branch_a")
    commit(temp_repo, trailers_b.apply_to("feat: branch b"))

    # Add a second commit to branch_b (HEAD won't have trailer)
    file_c = tmp_path / "c.txt"
    file_c.write_text("c content")
    add_paths(temp_repo, file_c)
    commit(temp_repo, b"feat: second commit on b")
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # Set up remote ref for branch_b and delete it locally
    temp_repo.refs[b"refs/remotes/origin/branch_b"] = branch_b_sha
    switch_branch(temp_repo, "branch_a")
    del temp_repo.refs[b"refs/heads/branch_b"]

    # _ensure_stack_branches_local should discover branch_b
    created = _ensure_stack_branches_local(temp_repo, "branch_a")
    assert "branch_b" in created
    assert b"refs/heads/branch_b" in temp_repo.refs

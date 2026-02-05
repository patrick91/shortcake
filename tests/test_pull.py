"""Tests for pull command."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.commands.pull import (
    PullError,
    _fetch,
    _pull,
    pull,  # noqa: F401 - imported for coverage
)


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


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
    porcelain.add(repo_with_feature, paths=[str(new_file)])
    porcelain.commit(repo_with_feature, message=b"Remote change")
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


def test_pull_diverged_error(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test pull error when branches have diverged without --rebase."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Save original sha
    original_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create a local commit
    local_file = tmp_path / "local_change.txt"
    local_file.write_text("local change")
    porcelain.add(repo_with_feature, paths=[str(local_file)])
    porcelain.commit(repo_with_feature, message=b"Local change")

    # Create a different commit on "remote" (from original position)
    # First reset to original, make a commit, then save as remote ref
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    porcelain.add(repo_with_feature, paths=[str(remote_file)])
    porcelain.commit(repo_with_feature, message=b"Remote change")
    remote_sha = repo_with_feature.refs[b"refs/heads/temp"]

    # Set up remote ref
    repo_with_feature.refs[b"refs/remotes/origin/feature"] = remote_sha

    # Switch back to feature
    switch_branch(repo_with_feature, "feature")
    # Delete temp branch
    del repo_with_feature.refs[b"refs/heads/temp"]

    with (
        patch("shortcake.commands.pull._fetch", return_value=True),
        pytest.raises(PullError, match="has diverged"),
    ):
        _pull(repo_with_feature)


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
    porcelain.add(repo_with_feature, paths=[str(local_file)])
    porcelain.commit(repo_with_feature, message=b"Local change")
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create "remote" commit from original position
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    porcelain.add(repo_with_feature, paths=[str(remote_file)])
    porcelain.commit(repo_with_feature, message=b"Remote change")
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
    porcelain.add(repo_with_feature, paths=[str(conflict_file)])
    porcelain.commit(repo_with_feature, message=b"Local change")
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create "remote" commit modifying the same file
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    conflict_file.write_text("remote version")
    porcelain.add(repo_with_feature, paths=[str(conflict_file)])
    porcelain.commit(repo_with_feature, message=b"Remote change")
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
    porcelain.add(repo_with_feature, paths=[str(tmp_path / "uncommitted.txt")])

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


# Tests for _fetch helper


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

    with patch("shortcake.commands.pull.porcelain.fetch") as mock_fetch:
        mock_fetch.return_value = {}
        result = _fetch(repo_with_feature)

    assert result is True


def test_fetch_failure(repo_with_feature: Repo) -> None:
    """Test _fetch returns False on failure."""
    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    with patch(
        "shortcake.commands.pull.porcelain.fetch",
        side_effect=OSError("Connection failed"),
    ):
        result = _fetch(repo_with_feature)

    assert result is False


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
    porcelain.add(repo_with_feature, paths=[str(new_file)])
    porcelain.commit(repo_with_feature, message=b"Remote change")
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
    assert "Fast-forwarded" in result.output


def test_pull_cli_error(temp_repo: Repo, tmp_path: Path) -> None:
    """Test pull CLI error handling."""
    os.chdir(tmp_path)
    result = runner.invoke(app, ["pull"])

    assert result.exit_code == 1
    assert "Error:" in result.output


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
    porcelain.add(repo_with_feature, paths=[str(local_file)])
    porcelain.commit(repo_with_feature, message=b"Local change")
    local_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Create "remote" commit from original position
    repo_with_feature.refs[b"refs/heads/temp"] = original_sha
    switch_branch(repo_with_feature, "temp")
    remote_file = tmp_path / "remote_change.txt"
    remote_file.write_text("remote change")
    porcelain.add(repo_with_feature, paths=[str(remote_file)])
    porcelain.commit(repo_with_feature, message=b"Remote change")
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

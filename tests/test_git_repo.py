from pathlib import Path

import pytest

from shortcake import _git as git
from tests._git_helpers import Repo, commit_files, init_repo


def test_open_repo_current_dir(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test opening repo from current directory."""
    monkeypatch.chdir(tmp_path)
    repo = git.open_repo()
    # Just verify it opens without error
    assert repo is not None


def test_open_repo_with_path(temp_repo: Repo, tmp_path: Path) -> None:
    """Test opening repo with explicit path."""
    repo = git.open_repo(tmp_path)
    assert repo is not None


def test_open_repo_from_subdirectory(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test opening repo from a subdirectory."""
    # Create a subdirectory
    subdir = tmp_path / "src" / "lib"
    subdir.mkdir(parents=True)

    # Change to the subdirectory
    monkeypatch.chdir(subdir)

    # Should still find the repo
    repo = git.open_repo()
    assert repo is not None
    assert Path(repo.path).resolve() == tmp_path.resolve()


def test_get_current_branch(repo_with_feature: Repo) -> None:
    """Test getting current branch name."""
    branch = git.get_current_branch(repo_with_feature)
    assert branch == "feature"


def test_get_current_branch_detached_head(temp_repo: Repo) -> None:
    """Test error when in detached HEAD state."""
    # Get the commit SHA and write it directly to HEAD file
    head_sha = temp_repo.refs[b"refs/heads/main"]
    # Write raw SHA to HEAD (not a symbolic ref)
    head_path = Path(temp_repo.controldir()) / "HEAD"
    head_path.write_bytes(head_sha.hex().encode() + b"\n")

    assert git.get_current_branch(temp_repo) is None


def test_get_default_branch_from_origin_head(temp_repo: Repo) -> None:
    """Test getting default branch from origin/HEAD."""
    # Set up origin/HEAD pointing to main
    temp_repo.refs[b"refs/remotes/origin/main"] = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs.set_symbolic_ref(
        b"refs/remotes/origin/HEAD", b"refs/remotes/origin/main"
    )

    default = git.get_default_branch(temp_repo)
    assert default == "main"


def test_get_default_branch_fallback_main(temp_repo: Repo) -> None:
    """Test fallback to main when origin/HEAD not set."""
    default = git.get_default_branch(temp_repo)
    assert default == "main"


def test_get_default_branch_fallback_master(tmp_path: Path) -> None:
    """Test fallback to master when main doesn't exist."""
    repo = init_repo(tmp_path, default_branch="master")
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    default = git.get_default_branch(repo)
    assert default == "master"


def test_get_default_branch_none(tmp_path: Path) -> None:
    """Test None when no default branch can be determined."""
    repo = init_repo(tmp_path, default_branch="develop")
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    default = git.get_default_branch(repo)
    assert default is None


def test_delete_branch(repo_with_feature: Repo) -> None:
    """Test deleting a branch."""
    assert git.branch_exists(repo_with_feature, "feature")
    git.delete_branch(repo_with_feature, "feature")
    assert not git.branch_exists(repo_with_feature, "feature")


def test_delete_branch_nonexistent(temp_repo: Repo) -> None:
    """Test deleting a branch that doesn't exist does nothing."""
    # Should not raise an error
    git.delete_branch(temp_repo, "nonexistent")


def test_has_remote_no_remote(temp_repo: Repo) -> None:
    """Test has_remote returns False when no remote configured."""
    assert not git.has_remote(temp_repo, "origin")


def test_has_remote_with_remote(temp_repo: Repo, tmp_path: Path) -> None:
    """Test has_remote returns True when remote is configured."""
    # Add origin remote to config
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"https://github.com/test/test.git")
    config.write_to_path()

    assert git.has_remote(temp_repo, "origin")


def test_fetch_and_fast_forward_trunk_no_remote(temp_repo: Repo) -> None:
    """Test fetch_and_fast_forward_trunk when no remote configured."""
    success, new_sha = git.fetch_and_fast_forward_trunk(temp_repo, "main")
    assert success is True
    assert new_sha is None


def test_fetch_and_fast_forward_trunk_with_new_remote_commits(
    tmp_path: Path,
) -> None:
    """Test fast-forward works when remote has new commits."""
    # Set up a "remote" repo
    remote_path = tmp_path / "remote"
    remote_repo = init_repo(remote_path)
    commit_files(remote_repo, {remote_path / "README.md": "# Test"}, "Initial commit")

    # Set up a "local" repo with the remote as origin
    local_path = tmp_path / "local"
    local_repo = init_repo(local_path)
    commit_files(local_repo, {local_path / "README.md": "# Test"}, "Initial commit")

    # Configure origin to point to the remote repo
    config = local_repo.get_config()
    config.set(
        (b"remote", b"origin"),
        b"url",
        str(remote_path).encode(),
    )
    config.set(
        (b"remote", b"origin"),
        b"fetch",
        b"+refs/heads/*:refs/remotes/origin/*",
    )
    config.write_to_path()

    # Fetch once so local has origin/main and shares history
    from shortcake._git._pygit2 import fetch_remote

    fetch_remote(local_repo, "origin")

    # Reset local main to origin/main so they share history
    remote_main_sha = local_repo.refs[b"refs/remotes/origin/main"]
    local_repo.refs[b"refs/heads/main"] = remote_main_sha

    # Now add new commits to the remote (local is behind)
    commit_files(
        remote_repo,
        {remote_path / "file1.txt": "content 1"},
        "Remote commit 1",
    )
    commit_files(
        remote_repo,
        {remote_path / "file2.txt": "content 2"},
        "Remote commit 2",
    )

    # Re-open local repo to simulate a fresh `sc sync` invocation
    local_repo = git.open_repo(local_path)

    # This should succeed: local main is ancestor of remote main
    success, new_sha = git.fetch_and_fast_forward_trunk(local_repo, "main")
    assert success is True
    assert new_sha is not None


def test_fetch_and_fast_forward_trunk_falls_back_to_git_cli(
    tmp_path: Path,
) -> None:
    """Test fast-forward works even when pygit2 fetch fails.

    Regression test: pygit2's remote.fetch() fails with
    'authentication required but no callback set' for SSH remotes.
    fetch_remote should fall back to `git fetch` via subprocess.
    """
    from unittest.mock import patch

    # Set up a "remote" repo
    remote_path = tmp_path / "remote"
    remote_repo = init_repo(remote_path)
    commit_files(remote_repo, {remote_path / "README.md": "# Test"}, "Initial commit")

    # Set up a "local" repo with the remote as origin
    local_path = tmp_path / "local"
    local_repo = init_repo(local_path)
    commit_files(local_repo, {local_path / "README.md": "# Test"}, "Initial commit")

    # Configure origin
    config = local_repo.get_config()
    config.set(
        (b"remote", b"origin"),
        b"url",
        str(remote_path).encode(),
    )
    config.set(
        (b"remote", b"origin"),
        b"fetch",
        b"+refs/heads/*:refs/remotes/origin/*",
    )
    config.write_to_path()

    # Fetch once and sync history
    from shortcake._git._pygit2 import fetch_remote as real_fetch

    real_fetch(local_repo, "origin")
    remote_main_sha = local_repo.refs[b"refs/remotes/origin/main"]
    local_repo.refs[b"refs/heads/main"] = remote_main_sha

    # Add new commits to remote
    commit_files(
        remote_repo,
        {remote_path / "file1.txt": "content 1"},
        "Remote commit 1",
    )

    # Re-open local repo
    local_repo = git.open_repo(local_path)

    # Make pygit2 fetch fail (simulates SSH auth failure)
    import pygit2

    def failing_pygit2_fetch(repo, remote_name="origin"):
        raise pygit2.GitError("authentication required but no callback set")

    with patch("shortcake._git._pygit2.open_pygit2_repo") as mock_open:
        mock_repo = mock_open.return_value
        mock_repo.remotes.__getitem__.return_value.fetch.side_effect = pygit2.GitError(
            "authentication required but no callback set"
        )

        success, new_sha = git.fetch_and_fast_forward_trunk(local_repo, "main")

    # Should still succeed by falling back to git CLI
    assert success is True, (
        "Fast-forward failed — fetch_remote should fall back to git CLI "
        "when pygit2 fails"
    )
    assert new_sha is not None

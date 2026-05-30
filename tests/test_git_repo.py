from pathlib import Path
from unittest.mock import patch

import pytest

from shortcake import _git as git
from tests._git_helpers import (
    Repo,
    commit_files,
    get_ref,
    init_repo,
    set_ref,
    set_remote,
)


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
    assert Path(repo.workdir).resolve() == tmp_path.resolve()


def test_get_current_branch(repo_with_feature: Repo) -> None:
    """Test getting current branch name."""
    branch = git.get_current_branch(repo_with_feature)
    assert branch == "feature"


def test_get_current_branch_detached_head(temp_repo: Repo) -> None:
    """Test error when in detached HEAD state."""
    # Get the commit SHA and write it directly to HEAD file
    head_sha = get_ref(temp_repo, "refs/heads/main")
    # Detach HEAD by writing SHA directly
    set_ref(temp_repo, "HEAD", head_sha)

    assert git.get_current_branch(temp_repo) is None


def test_get_default_branch_from_origin_head(temp_repo: Repo) -> None:
    """Test getting default branch from origin/HEAD."""
    # Set up origin/HEAD pointing to main
    set_ref(
        temp_repo, "refs/remotes/origin/main", get_ref(temp_repo, "refs/heads/main")
    )
    temp_repo.references.create(
        "refs/remotes/origin/HEAD", "refs/remotes/origin/main", force=True
    )

    default = git.get_default_branch(temp_repo)
    assert default == "main"


def test_get_default_branch_origin_head_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_default_branch falls back when origin/HEAD read raises."""
    import pygit2 as _pygit2

    original_get = temp_repo.references.get

    def broken_get(name):
        if "origin/HEAD" in name:
            raise _pygit2.GitError("broken")
        return original_get(name)

    monkeypatch.setattr(temp_repo.references, "get", broken_get)

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
    set_remote(temp_repo, "origin", "https://github.com/test/test.git")

    assert git.has_remote(temp_repo, "origin")


def test_fetch_and_fast_forward_trunk_no_remote(temp_repo: Repo) -> None:
    """Test fetch_and_fast_forward_trunk when no remote configured."""
    success, new_sha = git.fetch_and_fast_forward_trunk(temp_repo, "main")
    assert success is True
    assert new_sha is None


def test_fetch_and_fast_forward_trunk_with_new_remote_commits(
    tmp_path: Path,
) -> None:
    """Test fast-forward updates the checked-out trunk cleanly."""
    # Set up a "remote" repo
    remote_path = tmp_path / "remote"
    remote_repo = init_repo(remote_path)
    commit_files(remote_repo, {remote_path / "README.md": "# Test"}, "Initial commit")

    # Set up a "local" repo with the remote as origin
    local_path = tmp_path / "local"
    local_repo = init_repo(local_path)
    commit_files(local_repo, {local_path / "README.md": "# Test"}, "Initial commit")

    # Configure origin to point to the remote repo
    set_remote(local_repo, "origin", str(remote_path))

    local_repo.config["remote.origin.fetch"] = "+refs/heads/*:refs/remotes/origin/*"

    # Fetch once so local has origin/main and shares history
    from shortcake._git._pygit2 import fetch_remote

    fetch_remote(local_repo, "origin")

    # Reset local main to origin/main so they share history
    remote_main_sha = get_ref(local_repo, "refs/remotes/origin/main")
    set_ref(local_repo, "refs/heads/main", remote_main_sha)

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
    assert git.get_current_branch(local_repo) == "main"
    assert not git.has_uncommitted_changes(local_repo)
    assert (local_path / "file1.txt").read_text() == "content 1"
    assert (local_path / "file2.txt").read_text() == "content 2"


def test_fetch_and_fast_forward_trunk_checked_out_merge_failure(
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    """A failed checked-out branch fast-forward is reported as failure."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    commit_files(temp_repo, {tmp_path / "remote.txt": "remote"}, "Remote commit")
    remote_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/main", main_sha)
    set_ref(temp_repo, "refs/remotes/origin/main", remote_sha)

    with (
        patch("shortcake._git._remote.has_remote", return_value=True),
        patch("shortcake._git._remote.fetch_remote", return_value=True),
        patch("shortcake._git._remote.is_ancestor", return_value=True),
        patch(
            "shortcake._git._remote._fast_forward_checked_out_branch",
            return_value=False,
        ),
    ):
        success, new_sha = git.fetch_and_fast_forward_trunk(temp_repo, "main")

    assert success is False
    assert new_sha is None


def test_fetch_and_fast_forward_trunk_falls_back_to_git_cli(
    tmp_path: Path,
) -> None:
    """Test fast-forward works even when pygit2 fetch fails.

    Regression test: pygit2's remote.fetch() fails with
    'authentication required but no callback set' for SSH remotes.
    fetch_remote should fall back to `git fetch` via subprocess.
    """

    # Set up a "remote" repo
    remote_path = tmp_path / "remote"
    remote_repo = init_repo(remote_path)
    commit_files(remote_repo, {remote_path / "README.md": "# Test"}, "Initial commit")

    # Set up a "local" repo with the remote as origin
    local_path = tmp_path / "local"
    local_repo = init_repo(local_path)
    commit_files(local_repo, {local_path / "README.md": "# Test"}, "Initial commit")

    # Configure origin
    set_remote(local_repo, "origin", str(remote_path))

    local_repo.config["remote.origin.fetch"] = "+refs/heads/*:refs/remotes/origin/*"

    # Fetch once and sync history
    from shortcake._git._pygit2 import fetch_remote as real_fetch

    real_fetch(local_repo, "origin")
    remote_main_sha = get_ref(local_repo, "refs/remotes/origin/main")
    set_ref(local_repo, "refs/heads/main", remote_main_sha)

    # Add new commits to remote
    commit_files(
        remote_repo,
        {remote_path / "file1.txt": "content 1"},
        "Remote commit 1",
    )

    # Re-open local repo
    local_repo = git.open_repo(local_path)

    # Make pygit2's Remote.fetch fail, forcing git CLI fallback
    import pygit2 as _pygit2

    def failing_fetch(self, *args, **kwargs):
        raise _pygit2.GitError("authentication required but no callback set")

    with patch.object(_pygit2.Remote, "fetch", failing_fetch):
        success, new_sha = git.fetch_and_fast_forward_trunk(local_repo, "main")

    # Should still succeed by falling back to git CLI
    assert success is True, (
        "Fast-forward failed — fetch_remote should fall back to git CLI "
        "when pygit2 fails"
    )
    assert new_sha is not None


def test_get_branch_head_nonexistent(temp_repo: Repo) -> None:
    """Test get_branch_head raises KeyError for nonexistent branch."""
    with pytest.raises(KeyError):
        git.get_branch_head(temp_repo, "nonexistent")


def test_update_branch_creates_new(temp_repo: Repo) -> None:
    """Test update_branch creates a branch if it doesn't exist."""
    head_sha = git.get_branch_head(temp_repo, "main").decode()
    git.update_branch(temp_repo, "new-branch", head_sha)
    assert git.branch_exists(temp_repo, "new-branch")
    assert git.get_branch_head(temp_repo, "new-branch").decode() == head_sha


def test_create_commit_failure(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create_commit raises ValueError on git commit failure."""
    import subprocess

    original_run = subprocess.run

    def failing_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("cmd", [])
        if cmd and cmd[0] == "git" and "commit" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "error: nothing to commit")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", failing_run)
    with pytest.raises(ValueError, match="Commit failed"):
        git.create_commit(temp_repo, "test", allow_empty=False)


def test_amend_commit_failure(temp_repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test amend_commit raises ValueError on failure."""
    import subprocess

    original_run = subprocess.run

    def failing_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("cmd", [])
        if cmd and cmd[0] == "git" and "--amend" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "error: amend failed")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", failing_run)
    with pytest.raises(ValueError, match="Amend failed"):
        git.amend_commit(temp_repo, "test", allow_empty=False)

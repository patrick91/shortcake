from pathlib import Path

from shortcake._git._pygit2 import fetch_remote, get_remote_url
from tests._git_helpers import Repo, commit_files, init_repo, set_remote


def test_fetch_remote_success(tmp_path: Path) -> None:
    """Test fetch succeeds with a real local remote."""
    remote_path = tmp_path / "remote"
    remote_repo = init_repo(remote_path)
    commit_files(remote_repo, {remote_path / "README.md": "# Test"}, "init")

    local_path = tmp_path / "local"
    local_repo = init_repo(local_path)
    commit_files(local_repo, {local_path / "README.md": "# Test"}, "init")
    set_remote(local_repo, "origin", str(remote_path))

    assert fetch_remote(local_repo)


def test_fetch_remote_failure(temp_repo: Repo) -> None:
    """Test fetch returns False when remote doesn't exist."""
    set_remote(temp_repo, "origin", "file:///nonexistent/repo")
    assert not fetch_remote(temp_repo)


def test_get_remote_url_exists(temp_repo: Repo) -> None:
    """Test get_remote_url returns URL when remote configured."""
    set_remote(temp_repo, "origin", "https://github.com/test/test.git")
    assert get_remote_url(temp_repo) == "https://github.com/test/test.git"


def test_get_remote_url_missing(temp_repo: Repo) -> None:
    """Test get_remote_url returns None when no remote."""
    assert get_remote_url(temp_repo) is None

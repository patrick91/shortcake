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


def test_fetch_remote_prunes_deleted_remote_branches(tmp_path: Path) -> None:
    """A branch deleted upstream must stop appearing as a remote-tracking ref.

    Without --prune the ref lingers forever, so get_remote_ref reports a SHA
    for a branch that no longer exists on the remote — and callers conclude
    the branch is still safely pushed when it is not.
    """
    remote_path = tmp_path / "remote"
    remote_repo = init_repo(remote_path)
    commit_files(remote_repo, {remote_path / "README.md": "# Test"}, "init")
    remote_repo.create_branch("doomed", remote_repo[remote_repo.head.target])

    local_path = tmp_path / "local"
    local_repo = init_repo(local_path)
    commit_files(local_repo, {local_path / "README.md": "# Test"}, "init")
    set_remote(local_repo, "origin", str(remote_path))
    # A user may have fetch.prune=true globally, which would make this pass
    # regardless of what the code does. Turn it off for this repo so the test
    # exercises shortcake's own behaviour.
    local_repo.config["fetch.prune"] = False

    assert fetch_remote(local_repo)
    assert local_repo.references.get("refs/remotes/origin/doomed") is not None

    remote_repo.branches.local["doomed"].delete()

    assert fetch_remote(local_repo)
    assert local_repo.references.get("refs/remotes/origin/doomed") is None


def test_fetch_remote_prunes_via_the_cli_fallback(tmp_path: Path) -> None:
    """The CLI fallback must prune too, or the fix only works via pygit2."""
    from unittest.mock import patch

    import pygit2

    remote_path = tmp_path / "remote"
    remote_repo = init_repo(remote_path)
    commit_files(remote_repo, {remote_path / "README.md": "# Test"}, "init")
    remote_repo.create_branch("doomed", remote_repo[remote_repo.head.target])

    local_path = tmp_path / "local"
    local_repo = init_repo(local_path)
    commit_files(local_repo, {local_path / "README.md": "# Test"}, "init")
    set_remote(local_repo, "origin", str(remote_path))
    local_repo.config["fetch.prune"] = False  # see the note above
    assert fetch_remote(local_repo)

    remote_repo.branches.local["doomed"].delete()

    # force the subprocess path the way a libgit2 SSH auth failure would
    with patch.object(pygit2.Remote, "fetch", side_effect=pygit2.GitError("no auth")):
        assert fetch_remote(local_repo)
    assert local_repo.references.get("refs/remotes/origin/doomed") is None

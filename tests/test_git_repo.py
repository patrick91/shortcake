from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git


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
    repo = Repo.init(tmp_path, default_branch=b"master")
    # Need to create a commit for the branch to exist
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    default = git.get_default_branch(repo)
    assert default == "master"


def test_get_default_branch_none(tmp_path: Path) -> None:
    """Test None when no default branch can be determined."""
    repo = Repo.init(tmp_path, default_branch=b"develop")
    # Create commit so develop branch exists
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

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

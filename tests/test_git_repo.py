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

"""Tests for the down command."""

from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake.commands.down import (
    DetachedHeadError,
    DownResult,
    NotTrackedError,
    _down,
)


def test_down_to_parent(repo_with_stack: Repo) -> None:
    """Test moving down from branch_b to branch_a."""
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    result = _down(repo_with_stack)

    assert isinstance(result, DownResult)
    assert result.from_branch == "branch_b"
    assert result.to_branch == "branch_a"
    assert result.at_bottom is False
    assert git.get_current_branch(repo_with_stack) == "branch_a"


def test_down_to_trunk(repo_with_tracked_feature: Repo) -> None:
    """Test moving down to trunk (main)."""
    repo_with_tracked_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    result = _down(repo_with_tracked_feature)

    assert result.from_branch == "feature"
    assert result.to_branch == "main"
    assert result.at_bottom is True
    assert git.get_current_branch(repo_with_tracked_feature) == "main"


def test_down_not_tracked(tmp_path: Path) -> None:
    """Test error when branch is not tracked."""
    # Create repo with untracked branch
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create untracked feature branch
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/untracked"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/untracked")
    file_f = tmp_path / "f.txt"
    file_f.write_text("f")
    porcelain.add(repo, paths=[str(file_f)])
    porcelain.commit(repo, message=b"Add feature")

    with pytest.raises(NotTrackedError):
        _down(repo)


def test_down_detached_head(repo_with_stack: Repo) -> None:
    """Test error when in detached HEAD state."""
    main_sha = repo_with_stack.refs[b"refs/heads/main"]
    del repo_with_stack.refs[b"HEAD"]
    repo_with_stack.refs[b"HEAD"] = main_sha

    with pytest.raises(DetachedHeadError):
        _down(repo_with_stack)

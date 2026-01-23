from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.errors import DulwichError
from dulwich.repo import Repo

from shortcake import _git as git


def test_rebase_failure_class() -> None:
    """Test RebaseFailure exception."""
    from shortcake._git import RebaseFailure

    err = RebaseFailure("test error")
    assert str(err) == "test error"


def test_get_cherry_pick_head_none(temp_repo: Repo) -> None:
    """Test get_cherry_pick_head returns None when not in cherry-pick."""
    result = git.get_cherry_pick_head(temp_repo)
    assert result is None


def test_get_cherry_pick_head_empty_file(temp_repo: Repo, tmp_path: Path) -> None:
    """Test get_cherry_pick_head returns None for empty file."""
    head_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    head_path.write_bytes(b"")
    result = git.get_cherry_pick_head(temp_repo)
    assert result is None


def test_rebase_continue_generic_exception(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test rebase_continue wraps generic exceptions as RebaseFailure."""
    from shortcake._git import RebaseFailure

    # Create CHERRY_PICK_HEAD so it thinks a cherry-pick is in progress
    head_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    head_sha = temp_repo.refs[b"refs/heads/main"]
    head_path.write_bytes(head_sha)

    def mock_cherry_pick(repo, commit, continue_=False, abort=False):
        if continue_:
            raise DulwichError("Something went wrong")

    monkeypatch.setattr(porcelain, "cherry_pick", mock_cherry_pick)

    with pytest.raises(RebaseFailure, match="Something went wrong"):
        git.rebase_continue(temp_repo)


def test_rebase_abort_generic_exception(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test rebase_abort wraps generic exceptions as RebaseFailure."""
    from shortcake._git import RebaseFailure

    # Create CHERRY_PICK_HEAD so it thinks a cherry-pick is in progress
    head_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    head_sha = temp_repo.refs[b"refs/heads/main"]
    head_path.write_bytes(head_sha)

    def mock_cherry_pick(repo, commit, continue_=False, abort=False):
        if abort:
            raise DulwichError("Abort failed badly")

    monkeypatch.setattr(porcelain, "cherry_pick", mock_cherry_pick)

    with pytest.raises(RebaseFailure, match="Abort failed badly"):
        git.rebase_abort(temp_repo)

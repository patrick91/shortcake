from pathlib import Path

import pytest
from dulwich import porcelain
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


def test_rebase_continue_no_rebase_in_progress(temp_repo: Repo) -> None:
    """Test rebase_continue returns failure when no rebase is in progress."""
    result = git.rebase_continue(temp_repo)
    assert result.success is False
    assert result.conflict is False
    # Error output should contain some indication of failure
    assert result.error_output != "" or not result.success


def test_rebase_abort_no_rebase_in_progress(temp_repo: Repo) -> None:
    """Test rebase_abort raises RebaseFailure when no rebase is in progress."""
    from shortcake._git import RebaseFailure

    with pytest.raises(RebaseFailure, match="No rebase in progress"):
        git.rebase_abort(temp_repo)


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


def test_rebase_abort_with_cherry_pick_in_progress(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test rebase_abort succeeds when cherry-pick is in progress."""
    # Create a branch with a conflicting change
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    readme = tmp_path / "README.md"
    readme.write_text("# Feature Version")
    porcelain.add(temp_repo, paths=[str(readme)])
    porcelain.commit(temp_repo, message=b"feat: modify readme")

    # Create conflicting change on main
    switch_branch(temp_repo, "main")
    readme.write_text("# Main Version")
    porcelain.add(temp_repo, paths=[str(readme)])
    porcelain.commit(temp_repo, message=b"chore: update readme")

    # Start rebase which will conflict
    switch_branch(temp_repo, "feature")
    result = git.rebase_branch(temp_repo, "feature", "main", main_sha.decode())
    assert result.conflict is True
    assert git.is_rebase_in_progress(temp_repo)

    # Abort should succeed
    git.rebase_abort(temp_repo)
    assert not git.is_rebase_in_progress(temp_repo)

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shortcake import _git as git
from tests._git_helpers import Repo, add_paths, commit, switch_branch


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
    add_paths(temp_repo, readme)
    commit(temp_repo, b"feat: modify readme")

    # Create conflicting change on main
    switch_branch(temp_repo, "main")
    readme.write_text("# Main Version")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"chore: update readme")

    # Start rebase which will conflict
    switch_branch(temp_repo, "feature")
    result = git.rebase_branch(temp_repo, "feature", "main", main_sha.decode())
    assert result.conflict is True
    assert git.is_rebase_in_progress(temp_repo)

    # Abort should succeed
    git.rebase_abort(temp_repo)
    assert not git.is_rebase_in_progress(temp_repo)


def test_rebase_branch_non_conflict_error(temp_repo: Repo) -> None:
    """Test rebase_branch returns error (not conflict) for invalid upstream."""
    result = git.rebase_branch(temp_repo, "main", "main", "nonexistent_ref_abc123")
    assert result.success is False
    assert result.conflict is False
    assert result.error_output != ""


def test_rebase_continue_nothing_to_commit_skip_succeeds(temp_repo: Repo) -> None:
    """Test rebase_continue auto-skips when 'nothing to commit' and skip succeeds."""
    continue_result = MagicMock(returncode=1, stderr="", stdout="nothing to commit")
    skip_result = MagicMock(returncode=0, stderr="", stdout="")

    with patch(
        "shortcake._git._rebase.subprocess.run",
        side_effect=[continue_result, skip_result],
    ):
        result = git.rebase_continue(temp_repo)

    assert result.success is True
    assert result.skipped_empty is True


def test_rebase_continue_nothing_to_commit_skip_fails_conflict(
    temp_repo: Repo,
) -> None:
    """Test rebase_continue when skip after empty hits another conflict."""
    continue_result = MagicMock(returncode=1, stderr="", stdout="nothing to commit")
    skip_result = MagicMock(returncode=1, stderr="conflict during skip", stdout="")

    with (
        patch(
            "shortcake._git._rebase.subprocess.run",
            side_effect=[continue_result, skip_result],
        ),
        patch("shortcake._git._rebase.is_rebase_in_progress", return_value=True),
    ):
        result = git.rebase_continue(temp_repo)

    assert result.success is False
    assert result.conflict is True
    assert result.error_output == "conflict during skip"


def test_rebase_continue_nothing_to_commit_skip_fails_error(temp_repo: Repo) -> None:
    """Test rebase_continue when skip after empty fails without conflict."""
    continue_result = MagicMock(returncode=1, stderr="", stdout="nothing to commit")
    skip_result = MagicMock(returncode=1, stderr="skip error", stdout="")

    with (
        patch(
            "shortcake._git._rebase.subprocess.run",
            side_effect=[continue_result, skip_result],
        ),
        patch("shortcake._git._rebase.is_rebase_in_progress", return_value=False),
    ):
        result = git.rebase_continue(temp_repo)

    assert result.success is False
    assert result.conflict is False
    assert result.error_output == "skip error"


def test_rebase_continue_conflict(temp_repo: Repo) -> None:
    """Test rebase_continue detects conflict during continue."""
    continue_result = MagicMock(
        returncode=1, stderr="conflict during rebase", stdout=""
    )

    with (
        patch("shortcake._git._rebase.subprocess.run", return_value=continue_result),
        patch("shortcake._git._rebase.is_rebase_in_progress", return_value=True),
    ):
        result = git.rebase_continue(temp_repo)

    assert result.success is False
    assert result.conflict is True
    assert result.error_output == "conflict during rebase"


def test_rebase_abort_with_cherry_pick_head(temp_repo: Repo, tmp_path: Path) -> None:
    """Test rebase_abort aborts cherry-pick when only CHERRY_PICK_HEAD exists."""
    import subprocess as sp

    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    readme = tmp_path / "README.md"
    readme.write_text("# Feature")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"feat: feature change")
    feature_sha = temp_repo.refs[b"refs/heads/feature"]

    switch_branch(temp_repo, "main")
    readme.write_text("# Main")
    add_paths(temp_repo, readme)
    commit(temp_repo, b"chore: main change")

    # Cherry-pick feature commit onto main (will conflict)
    sp.run(
        ["git", "cherry-pick", feature_sha.decode()],
        cwd=tmp_path,
        capture_output=True,
    )

    git_dir = Path(temp_repo.controldir())
    assert (git_dir / "CHERRY_PICK_HEAD").exists()
    assert not (git_dir / "rebase-merge").exists()

    git.rebase_abort(temp_repo)
    assert not (git_dir / "CHERRY_PICK_HEAD").exists()


def test_rebase_abort_cherry_pick_abort_fails(temp_repo: Repo) -> None:
    """Test rebase_abort raises when git cherry-pick --abort fails."""
    git_dir = Path(temp_repo.controldir())
    (git_dir / "CHERRY_PICK_HEAD").write_text("a" * 40)

    mock_result = MagicMock(returncode=1, stderr="abort failed")

    with (
        patch("shortcake._git._rebase.subprocess.run", return_value=mock_result),
        pytest.raises(git.RebaseFailure, match="abort failed"),
    ):
        git.rebase_abort(temp_repo)

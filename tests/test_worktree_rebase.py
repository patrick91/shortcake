"""Tests for rebasing branches that are checked out in another worktree."""

import shutil
import subprocess
from pathlib import Path

import pytest

from shortcake import _git as git
from shortcake._restack_state import RestackState
from shortcake._trailers import Trailers
from shortcake.commands.abort import _abort
from shortcake.commands.continue_ import ContinueError, _continue
from shortcake.commands.move import _move
from shortcake.commands.restack import _restack
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    run_git,
    switch_branch,
)


def _parent(repo: Repo, branch: str) -> str | None:
    all_branches = set(git.get_all_local_branches(repo))
    return git.get_branch_parent(repo, branch, all_branches)


def _add_worktree(repo: Repo, path: Path, branch: str) -> Repo:
    run_git(repo, "worktree", "add", str(path), branch)
    return git.open_repo(path)


def _add_branch_c(repo: Repo, tmp_path: Path) -> None:
    """Extend repo_with_stack to main → branch_a → branch_b → branch_c."""
    create_branch(
        repo, "branch_c", git.get_branch_head(repo, "branch_b"), checkout=True
    )
    message = Trailers(parent_branch="branch_b").apply_to("feat: branch c")
    commit_files(repo, {tmp_path / "c.txt": "branch c content"}, message)


def _commit_on_main(repo: Repo, path: Path, content: str) -> None:
    switch_branch(repo, "main")
    commit_files(repo, {path: content}, "chore: update main")


def test_move_branch_checked_out_in_another_worktree(
    repo_with_stack_behind: Repo, tmp_path: Path
) -> None:
    repo = repo_with_stack_behind
    switch_branch(repo, "main")
    worktree_path = tmp_path / "linked-worktree"
    worktree = _add_worktree(repo, worktree_path, "branch_b")

    result = _move(repo, "branch_b", "main")

    assert result.conflict_branch is None
    assert _parent(repo, "branch_b") == "main"
    assert git.get_current_branch(repo) == "main"
    assert not git.has_uncommitted_changes(repo)
    assert git.get_current_branch(worktree) == "branch_b"
    assert not git.has_uncommitted_changes(worktree)
    assert (worktree_path / "main_update.txt").read_text() == "main update"
    assert (worktree_path / "b.txt").read_text() == "branch b content"
    assert not (worktree_path / "a.txt").exists()
    assert not RestackState.exists(repo)


def test_restack_child_checked_out_in_another_worktree(
    repo_with_stack_behind: Repo, tmp_path: Path
) -> None:
    repo = repo_with_stack_behind
    switch_branch(repo, "branch_a")
    worktree_path = tmp_path / "linked-worktree"
    worktree = _add_worktree(repo, worktree_path, "branch_b")

    result = _restack(repo)

    assert result.restacked_branches == ["branch_a", "branch_b"]
    assert git.get_current_branch(repo) == "branch_a"
    assert git.get_current_branch(worktree) == "branch_b"
    assert not git.has_uncommitted_changes(worktree)
    assert (worktree_path / "main_update.txt").read_text() == "main update"
    assert git.is_ancestor(
        repo,
        git.get_branch_head(repo, "branch_a"),
        git.get_branch_head(repo, "branch_b"),
    )


def test_continue_moves_worktree_branch_after_conflict(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    repo = repo_with_stack
    _add_branch_c(repo, tmp_path)
    _commit_on_main(repo, tmp_path / "b.txt", "main's b")
    worktree_path = tmp_path / "linked-worktree"
    worktree = _add_worktree(repo, worktree_path, "branch_b")

    result = _move(repo, "branch_b", "main")

    assert result.conflict_branch == "branch_b"
    assert git.get_current_branch(repo) is None
    assert git.is_rebase_in_progress(repo)
    assert (worktree_path / "b.txt").read_text() == "branch b content"

    (tmp_path / "b.txt").write_text("resolved")
    run_git(repo, "add", "b.txt")
    continued = _continue(repo)

    assert continued.conflict_branch is None
    assert continued.restacked_branches == ["branch_b", "branch_c"]
    assert _parent(repo, "branch_b") == "main"
    assert _parent(repo, "branch_c") == "branch_b"
    assert git.get_current_branch(repo) == "main"
    assert git.get_current_branch(worktree) == "branch_b"
    assert not git.has_uncommitted_changes(worktree)
    assert (worktree_path / "b.txt").read_text() == "resolved"
    assert not (worktree_path / "a.txt").exists()
    assert not RestackState.exists(repo)


def test_continue_fails_when_worktree_got_dirty(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    repo = repo_with_stack
    _commit_on_main(repo, tmp_path / "b.txt", "main's b")
    worktree_path = tmp_path / "linked-worktree"
    _add_worktree(repo, worktree_path, "branch_b")
    original_head = git.get_branch_head(repo, "branch_b")

    _move(repo, "branch_b", "main")
    (tmp_path / "b.txt").write_text("resolved")
    run_git(repo, "add", "b.txt")
    (worktree_path / "a.txt").write_text("work in progress")

    with pytest.raises(ContinueError, match="uncommitted changes"):
        _continue(repo)

    assert git.get_branch_head(repo, "branch_b") == original_head
    assert (worktree_path / "a.txt").read_text() == "work in progress"


def test_abort_leaves_unmoved_worktree_branch_alone(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    repo = repo_with_stack
    _commit_on_main(repo, tmp_path / "b.txt", "main's b")
    worktree_path = tmp_path / "linked-worktree"
    worktree = _add_worktree(repo, worktree_path, "branch_b")
    original_head = git.get_branch_head(repo, "branch_b")
    _move(repo, "branch_b", "main")
    (worktree_path / "untracked.txt").write_text("agent notes")

    _abort(repo)

    assert git.get_branch_head(repo, "branch_b") == original_head
    assert git.get_current_branch(repo) == "main"
    assert not git.is_rebase_in_progress(repo)
    assert git.get_current_branch(worktree) == "branch_b"
    assert not git.has_uncommitted_changes(worktree)
    assert (worktree_path / "untracked.txt").read_text() == "agent notes"


def test_abort_moves_worktree_back(repo_with_stack: Repo, tmp_path: Path) -> None:
    repo = repo_with_stack
    _add_branch_c(repo, tmp_path)
    _commit_on_main(repo, tmp_path / "c.txt", "main's c")
    worktree_path = tmp_path / "linked-worktree"
    worktree = _add_worktree(repo, worktree_path, "branch_b")
    original_head = git.get_branch_head(repo, "branch_b")

    result = _move(repo, "branch_b", "main")

    # branch_b moved in its worktree; branch_c conflicts with main's c.txt
    assert result.conflict_branch == "branch_c"
    assert (worktree_path / "c.txt").read_text() == "main's c"

    restored = _abort(repo)

    assert set(restored.restored_branches) == {"branch_b", "branch_c"}
    assert git.get_branch_head(repo, "branch_b") == original_head
    assert git.get_current_branch(worktree) == "branch_b"
    assert not git.has_uncommitted_changes(worktree)
    assert (worktree_path / "a.txt").read_text() == "branch a content"
    assert not (worktree_path / "c.txt").exists()


def test_abort_warns_when_worktree_cannot_move_back(
    repo_with_stack: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_stack
    _add_branch_c(repo, tmp_path)
    _commit_on_main(repo, tmp_path / "c.txt", "main's c")
    worktree_path = tmp_path / "linked-worktree"
    _add_worktree(repo, worktree_path, "branch_b")
    original_head = git.get_branch_head(repo, "branch_b")
    _move(repo, "branch_b", "main")
    moved_head = git.get_branch_head(repo, "branch_b")
    (worktree_path / "b.txt").write_text("work in progress")

    restored = _abort(repo)

    assert restored.restored_branches == ["branch_c"]
    assert git.get_branch_head(repo, "branch_b") == moved_head != original_head
    assert (worktree_path / "b.txt").read_text() == "work in progress"
    warning = capsys.readouterr().err
    assert "Could not restore 'branch_b'" in warning
    assert "uncommitted changes" in warning


def _dirty(repo: Repo, worktree_path: Path) -> str:
    (worktree_path / "b.txt").write_text("work in progress")
    return "uncommitted changes"


def _mid_cherry_pick(repo: Repo, worktree_path: Path) -> str:
    commit_files(repo, {Path(repo.workdir) / "b.txt": "main's b"}, "conflict")
    operation = subprocess.run(
        ["git", "cherry-pick", "main"], cwd=worktree_path, capture_output=True
    )
    assert operation.returncode != 0
    return "a rebase or cherry-pick is in progress"


def _mid_rebase(repo: Repo, worktree_path: Path) -> str:
    # git lists a worktree that's mid-rebase as detached, not on the branch
    commit_files(repo, {Path(repo.workdir) / "b.txt": "main's b"}, "conflict")
    operation = subprocess.run(
        ["git", "rebase", "main"], cwd=worktree_path, capture_output=True
    )
    assert operation.returncode != 0
    return "is already used by worktree"


def _missing(repo: Repo, worktree_path: Path) -> str:
    shutil.rmtree(worktree_path)
    return "missing worktree"


def _checked_out_twice(repo: Repo, worktree_path: Path) -> str:
    second = worktree_path.parent / "second-worktree"
    run_git(repo, "worktree", "add", "--force", str(second), "branch_b")
    return "checked out in multiple worktrees"


@pytest.mark.parametrize(
    "block", [_dirty, _mid_cherry_pick, _mid_rebase, _missing, _checked_out_twice]
)
def test_rebase_refuses_when_worktree_cannot_follow(
    repo_with_stack_behind: Repo, tmp_path: Path, block
) -> None:
    repo = repo_with_stack_behind
    switch_branch(repo, "main")
    worktree_path = tmp_path / "linked-worktree"
    _add_worktree(repo, worktree_path, "branch_b")
    original_head = git.get_branch_head(repo, "branch_b")
    expected = block(repo, worktree_path)

    result = git.rebase_branch(
        repo, "branch_b", "main", git.get_branch_head(repo, "branch_a").decode()
    )

    assert not result.success
    assert not result.conflict
    assert expected in result.error_output
    assert git.get_branch_head(repo, "branch_b") == original_head
    assert git.get_current_branch(repo) == "main"
    assert not git.is_rebase_in_progress(repo)


def test_rebase_keeps_branch_when_worktree_files_are_in_the_way(
    repo_with_stack_behind: Repo, tmp_path: Path
) -> None:
    repo = repo_with_stack_behind
    switch_branch(repo, "main")
    worktree_path = tmp_path / "linked-worktree"
    _add_worktree(repo, worktree_path, "branch_b")
    original_head = git.get_branch_head(repo, "branch_b")
    # Untracked, so the up-front check passes, but the rebased branch adds it
    (worktree_path / "main_update.txt").write_text("agent notes")

    result = git.rebase_branch(
        repo, "branch_b", "main", git.get_branch_head(repo, "branch_a").decode()
    )

    assert not result.success
    assert "Could not move 'branch_b' in worktree" in result.error_output
    assert git.get_branch_head(repo, "branch_b") == original_head
    assert (worktree_path / "main_update.txt").read_text() == "agent notes"


def test_finish_detached_rebase_ignores_head_not_on_top_of_onto(
    repo_with_stack: Repo,
) -> None:
    repo = repo_with_stack
    original_head = git.get_branch_head(repo, "branch_b")
    git.switch_branch(repo, "main", detach=True)

    git.finish_detached_rebase(repo, "branch_b", "branch_a")

    assert git.get_branch_head(repo, "branch_b") == original_head


def test_finish_detached_rebase_ignores_missing_onto(repo_with_stack: Repo) -> None:
    repo = repo_with_stack
    original_head = git.get_branch_head(repo, "branch_b")
    git.switch_branch(repo, "main", detach=True)

    git.finish_detached_rebase(repo, "branch_b", "gone")

    assert git.get_branch_head(repo, "branch_b") == original_head

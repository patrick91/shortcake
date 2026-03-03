"""Tests for reorder command."""

from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import RestackState
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.reorder import (
    ReorderError,
    _build_editor_content,
    _get_linear_stack,
    _parse_editor_result,
    _reorder,
    _update_branch_trailer,
)


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


runner = CliRunner()


def _create_stack_3(repo: Repo, tmp_path: Path) -> None:
    """Create a 3-branch linear stack: main -> branch_a -> branch_b -> branch_c.

    Each branch adds a unique file so reorder is conflict-free.
    """
    main_sha = repo.refs[b"refs/heads/main"]

    # branch_a
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    porcelain.reset(repo, "hard")

    (tmp_path / "a.txt").write_text("branch a content")
    porcelain.add(repo, paths=[str(tmp_path / "a.txt")])
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    porcelain.commit(repo, message=msg_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # branch_b
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    porcelain.reset(repo, "hard")

    (tmp_path / "b.txt").write_text("branch b content")
    porcelain.add(repo, paths=[str(tmp_path / "b.txt")])
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    porcelain.commit(repo, message=msg_b.encode())
    branch_b_sha = repo.refs[b"refs/heads/branch_b"]

    # branch_c
    repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    porcelain.reset(repo, "hard")

    (tmp_path / "c.txt").write_text("branch c content")
    porcelain.add(repo, paths=[str(tmp_path / "c.txt")])
    msg_c = Trailers(parent_branch="branch_b").apply_to("feat: branch c")
    porcelain.commit(repo, message=msg_c.encode())


# --- Precondition tests ---


def test_reorder_detached_head(temp_repo: Repo) -> None:
    """ReorderError when HEAD is detached."""
    head_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = head_sha
    with pytest.raises(ReorderError, match="detached HEAD"):
        _reorder(temp_repo, new_order=["a", "b"])


def test_reorder_uncommitted_changes(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """ReorderError when there are uncommitted changes."""
    switch_branch(repo_with_stack, "branch_b")
    (tmp_path / "dirty.txt").write_text("dirty")
    porcelain.add(repo_with_stack, paths=[str(tmp_path / "dirty.txt")])
    with pytest.raises(ReorderError, match="uncommitted changes"):
        _reorder(repo_with_stack, new_order=["branch_b", "branch_a"])


def test_reorder_rebase_in_progress(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """ReorderError when rebase is in progress."""
    switch_branch(repo_with_stack, "branch_b")
    rebase_dir = tmp_path / ".git" / "rebase-merge"
    rebase_dir.mkdir(parents=True)
    (rebase_dir / "head-name").write_text("refs/heads/branch_b")
    with pytest.raises(ReorderError, match="rebase in progress"):
        _reorder(repo_with_stack, new_order=["branch_b", "branch_a"])


def test_reorder_restack_in_progress(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """ReorderError when restack state exists."""
    switch_branch(repo_with_stack, "branch_b")
    state_path = tmp_path / ".git" / "shortcake-restack.json"
    state_path.write_text('{"version": 1}')
    with pytest.raises(ReorderError, match="Restack already in progress"):
        _reorder(repo_with_stack, new_order=["branch_b", "branch_a"])


def test_reorder_untracked_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """ReorderError when current branch is not tracked."""
    # Create an untracked feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    (tmp_path / "feature.txt").write_text("feature")
    porcelain.add(temp_repo, paths=[str(tmp_path / "feature.txt")])
    porcelain.commit(temp_repo, message=b"Add feature")

    with pytest.raises(ReorderError, match="not tracked"):
        _reorder(temp_repo, new_order=["feature"])


def test_reorder_fork_in_stack(repo_with_fork: Repo) -> None:
    """ReorderError when stack has a fork (multiple children)."""
    switch_branch(repo_with_fork, "branch_b")
    with pytest.raises(ReorderError, match="multiple children"):
        _reorder(repo_with_fork, new_order=["branch_b", "branch_c"])


def test_reorder_fork_below_current(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """ReorderError when fork is below current branch (downward walk)."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # A (tracked, 1 child B)
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    msg_a = Trailers(parent_branch="main").apply_to("feat: a")
    porcelain.commit(temp_repo, message=msg_a.encode())
    sha_a = temp_repo.refs[b"refs/heads/branch_a"]

    # B (tracked, child of A, will have 2 children C and D)
    temp_repo.refs[b"refs/heads/branch_b"] = sha_a
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: b")
    porcelain.commit(temp_repo, message=msg_b.encode())
    sha_b = temp_repo.refs[b"refs/heads/branch_b"]

    # C (child of B)
    temp_repo.refs[b"refs/heads/branch_c"] = sha_b
    switch_branch(temp_repo, "branch_c")
    (tmp_path / "c.txt").write_text("c")
    porcelain.add(temp_repo, paths=[str(tmp_path / "c.txt")])
    msg_c = Trailers(parent_branch="branch_b").apply_to("feat: c")
    porcelain.commit(temp_repo, message=msg_c.encode())

    # D (also child of B -> fork at B)
    temp_repo.refs[b"refs/heads/branch_d"] = sha_b
    switch_branch(temp_repo, "branch_d")
    (tmp_path / "d.txt").write_text("d")
    porcelain.add(temp_repo, paths=[str(tmp_path / "d.txt")])
    msg_d = Trailers(parent_branch="branch_b").apply_to("feat: d")
    porcelain.commit(temp_repo, message=msg_d.encode())

    # On branch_a, fork is at branch_b (below current, in downward walk)
    switch_branch(temp_repo, "branch_a")
    with pytest.raises(ReorderError, match="multiple children"):
        _reorder(temp_repo, new_order=["branch_a"])


def test_reorder_single_branch_stack(
    repo_with_tracked_feature: Repo,
) -> None:
    """ReorderError when stack has only one branch."""
    switch_branch(repo_with_tracked_feature, "feature")
    with pytest.raises(ReorderError, match="only one branch"):
        _reorder(repo_with_tracked_feature, new_order=["feature"])


# --- _get_linear_stack tests ---


def test_get_linear_stack_basic(
    repo_with_stack: Repo,
) -> None:
    """Get linear stack from middle branch."""
    switch_branch(repo_with_stack, "branch_b")
    trunk, branches = _get_linear_stack(repo_with_stack, "branch_b")
    assert trunk == "main"
    assert branches == ["branch_a", "branch_b"]


def test_get_linear_stack_3_branches(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Get linear stack of 3 branches."""
    _create_stack_3(temp_repo, tmp_path)
    trunk, branches = _get_linear_stack(temp_repo, "branch_b")
    assert trunk == "main"
    assert branches == ["branch_a", "branch_b", "branch_c"]


def test_get_linear_stack_from_bottom(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Get linear stack when on the bottom branch."""
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_a")
    trunk, branches = _get_linear_stack(temp_repo, "branch_a")
    assert trunk == "main"
    assert branches == ["branch_a", "branch_b", "branch_c"]


def test_get_linear_stack_from_top(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Get linear stack when on the top branch."""
    _create_stack_3(temp_repo, tmp_path)
    trunk, branches = _get_linear_stack(temp_repo, "branch_c")
    assert trunk == "main"
    assert branches == ["branch_a", "branch_b", "branch_c"]


# --- Editor helper tests ---


def test_build_editor_content() -> None:
    """Build editor content with branches and instructions."""
    content = _build_editor_content("main", ["branch_a", "branch_b", "branch_c"])
    lines = content.split("\n")
    assert lines[0] == "branch_a"
    assert lines[1] == "branch_b"
    assert lines[2] == "branch_c"
    assert lines[3] == ""
    assert lines[4].startswith("#")
    assert "main" in lines[4]


def test_parse_editor_result_valid() -> None:
    """Parse valid editor result."""
    content = "branch_c\nbranch_a\nbranch_b"
    result = _parse_editor_result(content, ["branch_a", "branch_b", "branch_c"])
    assert result == ["branch_c", "branch_a", "branch_b"]


def test_parse_editor_result_with_comments() -> None:
    """Comments and blank lines are filtered."""
    content = "branch_c\n# comment\n\nbranch_a\nbranch_b"
    result = _parse_editor_result(content, ["branch_a", "branch_b", "branch_c"])
    assert result == ["branch_c", "branch_a", "branch_b"]


def test_parse_editor_result_unknown_branch() -> None:
    """Error on unknown branch name."""
    with pytest.raises(ReorderError, match="Unknown branch 'unknown'"):
        _parse_editor_result("unknown\nbranch_a", ["branch_a", "branch_b"])


def test_parse_editor_result_duplicate() -> None:
    """Error on duplicate branch."""
    with pytest.raises(ReorderError, match="Duplicate branch"):
        _parse_editor_result(
            "branch_a\nbranch_a", ["branch_a", "branch_b"]
        )


def test_parse_editor_result_missing() -> None:
    """Error on missing branch."""
    with pytest.raises(ReorderError, match="Missing branch"):
        _parse_editor_result("branch_a", ["branch_a", "branch_b"])


def test_parse_editor_result_empty() -> None:
    """Error on empty result."""
    with pytest.raises(ReorderError, match="empty result"):
        _parse_editor_result("", ["branch_a", "branch_b"])


def test_parse_editor_result_only_comments() -> None:
    """Error when only comments remain."""
    with pytest.raises(ReorderError, match="empty result"):
        _parse_editor_result("# just a comment", ["branch_a", "branch_b"])


# --- No-op test ---


def test_reorder_same_order(repo_with_stack: Repo) -> None:
    """Same order returns early with no-op."""
    switch_branch(repo_with_stack, "branch_b")
    result = _reorder(repo_with_stack, new_order=["branch_a", "branch_b"])
    assert result.reordered_branches == []
    assert result.conflict_branch is None


# --- Validation tests ---


def test_reorder_unknown_branch(repo_with_stack: Repo) -> None:
    """Error when new_order contains unknown branch."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(ReorderError, match="unknown"):
        _reorder(repo_with_stack, new_order=["branch_a", "unknown"])


def test_reorder_missing_branch(repo_with_stack: Repo) -> None:
    """Error when new_order is missing a branch."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(ReorderError, match="missing"):
        _reorder(repo_with_stack, new_order=["branch_a"])


def test_reorder_duplicate_branch_in_order(repo_with_stack: Repo) -> None:
    """Error when new_order has duplicates (caught as missing branches)."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(ReorderError, match="missing"):
        _reorder(
            repo_with_stack,
            new_order=["branch_a", "branch_a"],
        )


# --- Core reorder tests ---


def test_reorder_swap_two_branches(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Swap two branches: main -> A -> B becomes main -> B -> A."""
    switch_branch(repo_with_stack, "branch_b")

    result = _reorder(repo_with_stack, new_order=["branch_b", "branch_a"])

    assert result.conflict_branch is None
    assert set(result.reordered_branches) == {"branch_a", "branch_b"}

    # Verify trailers
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    parent_b = git.get_branch_parent(repo_with_stack, "branch_b", all_branches)
    parent_a = git.get_branch_parent(repo_with_stack, "branch_a", all_branches)
    assert parent_b == "main"
    assert parent_a == "branch_b"

    # Verify file contents preserved
    switch_branch(repo_with_stack, "branch_a")
    assert (tmp_path / "a.txt").read_text() == "branch a content"
    assert (tmp_path / "b.txt").read_text() == "branch b content"

    switch_branch(repo_with_stack, "branch_b")
    assert (tmp_path / "b.txt").read_text() == "branch b content"
    assert not (tmp_path / "a.txt").exists()


def test_reorder_move_top_to_bottom(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Move top branch to bottom: A -> B -> C becomes C -> A -> B."""
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_b")

    result = _reorder(temp_repo, new_order=["branch_c", "branch_a", "branch_b"])

    assert result.conflict_branch is None
    assert len(result.reordered_branches) == 3

    # Verify trailers
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_c", all_branches) == "main"
    assert git.get_branch_parent(temp_repo, "branch_a", all_branches) == "branch_c"
    assert git.get_branch_parent(temp_repo, "branch_b", all_branches) == "branch_a"

    # Verify file contents on the top branch
    switch_branch(temp_repo, "branch_b")
    assert (tmp_path / "a.txt").read_text() == "branch a content"
    assert (tmp_path / "b.txt").read_text() == "branch b content"
    assert (tmp_path / "c.txt").read_text() == "branch c content"


def test_reorder_move_bottom_to_top(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Move bottom branch to top: A -> B -> C becomes B -> C -> A."""
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_a")

    result = _reorder(temp_repo, new_order=["branch_b", "branch_c", "branch_a"])

    assert result.conflict_branch is None
    assert len(result.reordered_branches) == 3

    # Verify trailers
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_b", all_branches) == "main"
    assert git.get_branch_parent(temp_repo, "branch_c", all_branches) == "branch_b"
    assert git.get_branch_parent(temp_repo, "branch_a", all_branches) == "branch_c"

    # Verify file contents on top
    switch_branch(temp_repo, "branch_a")
    assert (tmp_path / "a.txt").read_text() == "branch a content"
    assert (tmp_path / "b.txt").read_text() == "branch b content"
    assert (tmp_path / "c.txt").read_text() == "branch c content"


def test_reorder_reverse_full_stack(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Reverse full stack: A -> B -> C becomes C -> B -> A."""
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_a")

    result = _reorder(temp_repo, new_order=["branch_c", "branch_b", "branch_a"])

    assert result.conflict_branch is None
    assert len(result.reordered_branches) == 3

    # Verify trailers
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_c", all_branches) == "main"
    assert git.get_branch_parent(temp_repo, "branch_b", all_branches) == "branch_c"
    assert git.get_branch_parent(temp_repo, "branch_a", all_branches) == "branch_b"


def test_reorder_returns_to_original_branch(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """After reorder, we're back on the original branch."""
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_b")

    _reorder(temp_repo, new_order=["branch_c", "branch_a", "branch_b"])

    assert git.get_current_branch(temp_repo) == "branch_b"


def test_reorder_cleans_up_state(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """State file is cleaned up after successful reorder."""
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_b")

    _reorder(temp_repo, new_order=["branch_c", "branch_a", "branch_b"])

    assert not RestackState.exists(temp_repo)


# --- _update_branch_trailer tests ---


def test_update_branch_trailer(repo_with_tracked_feature: Repo) -> None:
    """Updating trailer changes the parent in the commit message.

    _update_branch_trailer is designed to be called AFTER a branch has been
    rebased onto a new parent. We test with a single-commit branch where the
    parent ref is already the branch's base.
    """
    repo = repo_with_tracked_feature
    switch_branch(repo, "feature")

    # Verify original trailer
    all_branches = set(git.get_all_local_branches(repo))
    assert git.get_branch_parent(repo, "feature", all_branches) == "main"

    # The branch is already on main, so _update_branch_trailer("feature", "main")
    # should work. Let's verify it can change the trailer value even when
    # the parent ref name changes (not the actual base).
    # We need to test it in context: create a new branch name pointing at main
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/trunk2"] = main_sha

    _update_branch_trailer(repo, "feature", "trunk2")

    all_branches = set(git.get_all_local_branches(repo))
    assert git.get_branch_parent(repo, "feature", all_branches) == "trunk2"


def test_reorder_multi_commit_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """Reorder works with branches that have multiple commits."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: 2 commits
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a1.txt").write_text("a1")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a1.txt")])
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a commit 1")
    porcelain.commit(temp_repo, message=msg_a.encode())
    (tmp_path / "a2.txt").write_text("a2")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a2.txt")])
    porcelain.commit(temp_repo, message=b"feat: branch a commit 2")
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: 1 commit
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=msg_b.encode())

    # Swap: main -> B -> A
    result = _reorder(temp_repo, new_order=["branch_b", "branch_a"])

    assert result.conflict_branch is None
    assert len(result.reordered_branches) == 2

    # Verify trailers
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_b", all_branches) == "main"
    assert git.get_branch_parent(temp_repo, "branch_a", all_branches) == "branch_b"

    # Verify files on top
    switch_branch(temp_repo, "branch_a")
    assert (tmp_path / "a1.txt").read_text() == "a1"
    assert (tmp_path / "a2.txt").read_text() == "a2"
    assert (tmp_path / "b.txt").read_text() == "b"


def test_reorder_editor_mode(
    repo_with_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editor mode opens editor and parses result."""
    switch_branch(repo_with_stack, "branch_b")

    # Mock open_editor to return swapped order
    monkeypatch.setattr(
        "shortcake.commands.reorder.open_editor",
        lambda content: "branch_b\nbranch_a",
    )

    result = _reorder(repo_with_stack)

    assert result.conflict_branch is None
    assert set(result.reordered_branches) == {"branch_a", "branch_b"}


def test_reorder_editor_abort(
    repo_with_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editor mode aborts when editor returns None."""
    switch_branch(repo_with_stack, "branch_b")

    monkeypatch.setattr(
        "shortcake.commands.reorder.open_editor",
        lambda content: None,
    )

    with pytest.raises(ReorderError, match="Aborted"):
        _reorder(repo_with_stack)


# --- Conflict tests ---


def test_reorder_conflict(temp_repo: Repo, tmp_path: Path) -> None:
    """Reorder that causes a conflict saves state for sc continue."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: modifies shared.txt
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "shared.txt").write_text("content from A")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=msg_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: modifies same file differently
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "shared.txt").write_text("content from B")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=msg_b.encode())

    # Reorder: swap A and B -> B needs to go onto main, A onto B
    # B modifies shared.txt to "content from B", A modifies to "content from A"
    # B going onto main is fine, but A going onto B will conflict since
    # both modify shared.txt differently from main
    result = _reorder(temp_repo, new_order=["branch_b", "branch_a"])

    assert result.conflict_branch is not None
    assert RestackState.exists(temp_repo)


def test_reorder_conflict_abort(temp_repo: Repo, tmp_path: Path) -> None:
    """After a conflict, sc abort restores original state."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: modifies shared.txt
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "shared.txt").write_text("content from A")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=msg_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]
    original_a = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: modifies same file differently
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "shared.txt").write_text("content from B")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=msg_b.encode())
    original_b = temp_repo.refs[b"refs/heads/branch_b"]

    # Reorder causing conflict
    _reorder(temp_repo, new_order=["branch_b", "branch_a"])

    # Abort
    from shortcake.commands.abort import _abort

    _abort(temp_repo)

    # State cleaned up
    assert not RestackState.exists(temp_repo)

    # Original refs restored
    assert temp_repo.refs[b"refs/heads/branch_a"] == original_a
    assert temp_repo.refs[b"refs/heads/branch_b"] == original_b


def _resolve_conflict_and_continue_rebase(tmp_path: Path, filename: str, content: str) -> None:
    """Helper: resolve a conflict file and continue the git rebase."""
    import os
    import subprocess

    (tmp_path / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )


def test_reorder_conflict_continue(temp_repo: Repo, tmp_path: Path) -> None:
    """After conflicts, resolve with sc continue and verify trailers.

    Both rebases conflict, so we exercise both trailer-update paths
    in continue_.py (current step at line 83 and remaining steps at line 141).
    """
    from shortcake.commands.continue_ import _continue

    main_sha = temp_repo.refs[b"refs/heads/main"]

    # Create shared.txt on main so both branches can conflict on it
    switch_branch(temp_repo, "main")
    (tmp_path / "shared.txt").write_text("original")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    porcelain.commit(temp_repo, message=b"add shared.txt")
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: modifies shared.txt
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "shared.txt").write_text("content from A")
    (tmp_path / "a.txt").write_text("a")
    porcelain.add(
        temp_repo,
        paths=[str(tmp_path / "shared.txt"), str(tmp_path / "a.txt")],
    )
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=msg_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: modifies shared.txt differently
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "shared.txt").write_text("content from B")
    (tmp_path / "b.txt").write_text("b")
    porcelain.add(
        temp_repo,
        paths=[str(tmp_path / "shared.txt"), str(tmp_path / "b.txt")],
    )
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=msg_b.encode())

    # Reorder: swap -> both rebases will conflict on shared.txt
    result = _reorder(temp_repo, new_order=["branch_b", "branch_a"])
    assert result.conflict_branch == "branch_b"

    # Resolve first conflict (branch_b onto main) and continue rebase
    _resolve_conflict_and_continue_rebase(
        tmp_path, "shared.txt", "resolved B"
    )

    # sc continue: finishes branch_b (trailer update, line 83),
    # then starts branch_a which also conflicts
    continue_result = _continue(temp_repo)
    assert continue_result.conflict_branch == "branch_a"

    # Verify branch_b trailer was already updated
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert (
        git.get_branch_parent(temp_repo, "branch_b", all_branches) == "main"
    )

    # Resolve second conflict (branch_a onto branch_b)
    _resolve_conflict_and_continue_rebase(
        tmp_path, "shared.txt", "resolved A"
    )

    # sc continue again: finishes branch_a (trailer update, line 141)
    continue_result2 = _continue(temp_repo)
    assert continue_result2.conflict_branch is None

    # Verify both trailers updated
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert (
        git.get_branch_parent(temp_repo, "branch_b", all_branches) == "main"
    )
    assert (
        git.get_branch_parent(temp_repo, "branch_a", all_branches)
        == "branch_b"
    )
    assert not RestackState.exists(temp_repo)


def test_reorder_conflict_continue_remaining_steps(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Continue after conflict with remaining steps exercises line 141.

    Setup: main -> A (a.txt) -> B (b.txt) -> C (shared.txt modified)
    C modifies shared.txt which exists on main, causing a conflict
    when C is moved to the bottom. A and B only add unique files,
    so they rebase cleanly as remaining steps.
    """
    from shortcake.commands.continue_ import _continue

    main_sha = temp_repo.refs[b"refs/heads/main"]

    # shared.txt on main
    switch_branch(temp_repo, "main")
    (tmp_path / "shared.txt").write_text("original")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    porcelain.commit(temp_repo, message=b"add shared.txt")
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: only adds a.txt (no conflict risk)
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=msg_a.encode())
    sha_a = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: only adds b.txt (no conflict risk)
    temp_repo.refs[b"refs/heads/branch_b"] = sha_a
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=msg_b.encode())
    sha_b = temp_repo.refs[b"refs/heads/branch_b"]

    # branch_c: modifies shared.txt (will conflict when rebased onto main)
    temp_repo.refs[b"refs/heads/branch_c"] = sha_b
    switch_branch(temp_repo, "branch_c")
    (tmp_path / "shared.txt").write_text("content from C")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    msg_c = Trailers(parent_branch="branch_b").apply_to("feat: branch c")
    porcelain.commit(temp_repo, message=msg_c.encode())

    # Update shared.txt on main so C conflicts when rebased onto it
    switch_branch(temp_repo, "main")
    (tmp_path / "shared.txt").write_text("updated on main")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    porcelain.commit(temp_repo, message=b"update shared.txt on main")
    switch_branch(temp_repo, "branch_c")

    # Reorder to [C, A, B]: C onto main conflicts (shared.txt)
    result = _reorder(
        temp_repo, new_order=["branch_c", "branch_a", "branch_b"]
    )
    assert result.conflict_branch == "branch_c"

    # Resolve conflict and continue rebase
    _resolve_conflict_and_continue_rebase(
        tmp_path, "shared.txt", "resolved C"
    )

    # sc continue: finishes C (line 83 - trailer update for current step),
    # then processes A onto C and B onto A as remaining steps
    # (line 141 - trailer update in the remaining steps loop)
    continue_result = _continue(temp_repo)
    assert continue_result.conflict_branch is None

    # Verify all trailers updated
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert (
        git.get_branch_parent(temp_repo, "branch_c", all_branches) == "main"
    )
    assert (
        git.get_branch_parent(temp_repo, "branch_a", all_branches)
        == "branch_c"
    )
    assert (
        git.get_branch_parent(temp_repo, "branch_b", all_branches)
        == "branch_a"
    )
    assert not RestackState.exists(temp_repo)


# --- CLI tests ---


def test_reorder_cli_same_order(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: same order shows no-op message."""
    monkeypatch.chdir(tmp_path)
    switch_branch(repo_with_stack, "branch_b")
    result = runner.invoke(app, ["reorder", "branch_a", "branch_b"])
    assert result.exit_code == 0
    assert "already in the requested order" in result.output


def test_reorder_cli_swap(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: swap two branches."""
    monkeypatch.chdir(tmp_path)
    switch_branch(repo_with_stack, "branch_b")
    result = runner.invoke(app, ["reorder", "branch_b", "branch_a"])
    assert result.exit_code == 0
    assert "Reordered" in result.output


def test_reorder_cli_error(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: error message for untracked branch."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["reorder", "a", "b"])
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_reorder_cli_3_branches(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: reorder 3 branches."""
    monkeypatch.chdir(tmp_path)
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_b")
    result = runner.invoke(
        app, ["reorder", "branch_c", "branch_a", "branch_b"]
    )
    assert result.exit_code == 0
    assert "Reordered" in result.output


def test_reorder_cli_conflict_exit_code(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: conflict returns exit code 1."""
    monkeypatch.chdir(tmp_path)
    main_sha = temp_repo.refs[b"refs/heads/main"]

    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "shared.txt").write_text("content from A")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=msg_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "shared.txt").write_text("content from B")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=msg_b.encode())

    result = runner.invoke(app, ["reorder", "branch_b", "branch_a"])
    assert result.exit_code == 1

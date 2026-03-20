"""Tests for move command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._github import GitHubClient, PRInfo
from shortcake._pr_stack import STACK_END_MARKER, STACK_START_MARKER
from shortcake._restack_state import RestackState
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.move import MoveError, _move
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    get_ref,
    reset_hard,
    set_ref,
    set_remote,
    switch_branch,
)

runner = CliRunner()


def setup_origin_remote(repo: Repo, url: str = "git@github.com:owner/repo.git") -> None:
    """Configure origin remote for a repo."""
    set_remote(repo, "origin", url)


def _create_stack_3(repo: Repo, tmp_path: Path) -> None:
    """Create a 3-branch linear stack: main -> branch_a -> branch_b -> branch_c."""
    main_sha = get_ref(repo, "refs/heads/main")

    # branch_a
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    reset_hard(repo)

    (tmp_path / "a.txt").write_text("branch a content")
    add_paths(repo, tmp_path / "a.txt")
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    commit(repo, msg_a)
    branch_a_sha = get_ref(repo, "refs/heads/branch_a")

    # branch_b
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    reset_hard(repo)

    (tmp_path / "b.txt").write_text("branch b content")
    add_paths(repo, tmp_path / "b.txt")
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    commit(repo, msg_b)
    branch_b_sha = get_ref(repo, "refs/heads/branch_b")

    # branch_c
    set_ref(repo, "refs/heads/branch_c", branch_b_sha)
    repo.set_head("refs/heads/branch_c")
    reset_hard(repo)

    (tmp_path / "c.txt").write_text("branch c content")
    add_paths(repo, tmp_path / "c.txt")
    msg_c = Trailers(parent_branch="branch_b").apply_to("feat: branch c")
    commit(repo, msg_c)


# --- Precondition error tests ---


def test_move_detached_head(temp_repo: Repo) -> None:
    """MoveError when HEAD is detached."""
    head_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", head_sha)
    with pytest.raises(MoveError, match="detached HEAD"):
        _move(temp_repo, branch="main", parent="other")


def test_move_uncommitted_changes(repo_with_stack: Repo, tmp_path: Path) -> None:
    """MoveError when there are uncommitted changes."""
    switch_branch(repo_with_stack, "branch_b")
    (tmp_path / "dirty.txt").write_text("dirty")
    add_paths(repo_with_stack, tmp_path / "dirty.txt")
    with pytest.raises(MoveError, match="uncommitted changes"):
        _move(repo_with_stack, parent="main")


def test_move_rebase_in_progress(repo_with_stack: Repo, tmp_path: Path) -> None:
    """MoveError when rebase is in progress."""
    switch_branch(repo_with_stack, "branch_b")
    rebase_dir = tmp_path / ".git" / "rebase-merge"
    rebase_dir.mkdir(parents=True)
    (rebase_dir / "head-name").write_text("refs/heads/branch_b")
    with pytest.raises(MoveError, match="rebase in progress"):
        _move(repo_with_stack, parent="main")


def test_move_restack_in_progress(repo_with_stack: Repo, tmp_path: Path) -> None:
    """MoveError when restack state exists."""
    switch_branch(repo_with_stack, "branch_b")
    state_path = tmp_path / ".git" / "shortcake-restack.json"
    state_path.write_text('{"version": 1}')
    with pytest.raises(MoveError, match="Restack already in progress"):
        _move(repo_with_stack, parent="main")


def test_move_no_parent_option(repo_with_stack: Repo) -> None:
    """MoveError when --parent is not provided."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(MoveError, match="--parent is required"):
        _move(repo_with_stack)


def test_move_untracked_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """MoveError when branch is not tracked."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")
    reset_hard(temp_repo)

    (tmp_path / "feature.txt").write_text("feature")
    add_paths(temp_repo, tmp_path / "feature.txt")
    commit(temp_repo, b"Add feature")

    with pytest.raises(MoveError, match="not tracked"):
        _move(temp_repo, parent="main")


def test_move_parent_not_found(repo_with_stack: Repo) -> None:
    """MoveError when new parent doesn't exist."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(MoveError, match="not found"):
        _move(repo_with_stack, parent="nonexistent")


def test_move_onto_self(repo_with_stack: Repo) -> None:
    """MoveError when trying to move onto self."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(MoveError, match="onto itself"):
        _move(repo_with_stack, parent="branch_b")


def test_move_circular_dependency(repo_with_stack: Repo) -> None:
    """MoveError when new parent is a descendant (would create cycle)."""
    switch_branch(repo_with_stack, "branch_a")
    with pytest.raises(MoveError, match=r"descendant.*cycle"):
        _move(repo_with_stack, parent="branch_b")


# --- No-op test ---


def test_move_same_parent(repo_with_stack: Repo) -> None:
    """No-op when moving to the same parent."""
    switch_branch(repo_with_stack, "branch_b")
    result = _move(repo_with_stack, parent="branch_a")
    assert result.old_parent == "branch_a"
    assert result.new_parent == "branch_a"
    assert result.restacked_children == []
    assert result.conflict_branch is None


# --- Core move tests ---


def test_move_basic(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Move branch_b from branch_a to main."""
    switch_branch(repo_with_stack, "branch_b")

    result = _move(repo_with_stack, parent="main")

    assert result.branch == "branch_b"
    assert result.old_parent == "branch_a"
    assert result.new_parent == "main"
    assert result.conflict_branch is None

    # Verify trailer updated
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    assert git.get_branch_parent(repo_with_stack, "branch_b", all_branches) == "main"

    # Verify file contents preserved
    switch_branch(repo_with_stack, "branch_b")
    assert (tmp_path / "b.txt").read_text() == "branch b content"


def test_move_defaults_to_current_branch(repo_with_stack: Repo) -> None:
    """Move defaults to current branch when no branch specified."""
    switch_branch(repo_with_stack, "branch_b")

    result = _move(repo_with_stack, parent="main")

    assert result.branch == "branch_b"


def test_move_explicit_branch(repo_with_stack: Repo) -> None:
    """Move a specific branch (not the current one)."""
    switch_branch(repo_with_stack, "branch_a")

    result = _move(repo_with_stack, branch="branch_b", parent="main")

    assert result.branch == "branch_b"
    assert result.new_parent == "main"

    # Verify we're back on the original branch
    assert git.get_current_branch(repo_with_stack) == "branch_a"


def test_move_with_children(temp_repo: Repo, tmp_path: Path) -> None:
    """Move branch_a to a new parent, verify children are restacked."""
    _create_stack_3(temp_repo, tmp_path)

    # Create a new branch from main to be the new parent
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/new_parent", main_sha)
    switch_branch(temp_repo, "new_parent")
    (tmp_path / "np.txt").write_text("new parent content")
    add_paths(temp_repo, tmp_path / "np.txt")
    msg_np = Trailers(parent_branch="main").apply_to("feat: new parent")
    commit(temp_repo, msg_np)

    switch_branch(temp_repo, "branch_a")

    result = _move(temp_repo, parent="new_parent")

    assert result.branch == "branch_a"
    assert result.old_parent == "main"
    assert result.new_parent == "new_parent"
    assert result.conflict_branch is None

    # branch_b is a child of branch_a and should be restacked
    assert "branch_b" in result.restacked_children

    # Verify trailers
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_a", all_branches) == "new_parent"
    # branch_b should still point to branch_a
    assert git.get_branch_parent(temp_repo, "branch_b", all_branches) == "branch_a"

    # Verify file contents on branch_a (should have new_parent's file)
    switch_branch(temp_repo, "branch_a")
    assert (tmp_path / "a.txt").read_text() == "branch a content"
    assert (tmp_path / "np.txt").read_text() == "new parent content"

    # Verify file contents on branch_c (top of stack, has everything)
    switch_branch(temp_repo, "branch_c")
    assert (tmp_path / "a.txt").read_text() == "branch a content"
    assert (tmp_path / "b.txt").read_text() == "branch b content"
    assert (tmp_path / "c.txt").read_text() == "branch c content"


def test_move_returns_to_original_branch(repo_with_stack: Repo) -> None:
    """After move, we're back on the original branch."""
    switch_branch(repo_with_stack, "branch_a")

    _move(repo_with_stack, branch="branch_b", parent="main")

    assert git.get_current_branch(repo_with_stack) == "branch_a"


def test_move_cleans_up_state(repo_with_stack: Repo) -> None:
    """State file is cleaned up after successful move."""
    switch_branch(repo_with_stack, "branch_b")

    _move(repo_with_stack, parent="main")

    assert not RestackState.exists(repo_with_stack)


def test_move_preserves_file_contents(repo_with_stack: Repo, tmp_path: Path) -> None:
    """File contents are preserved after move."""
    switch_branch(repo_with_stack, "branch_b")

    _move(repo_with_stack, parent="main")

    switch_branch(repo_with_stack, "branch_b")
    assert (tmp_path / "b.txt").read_text() == "branch b content"
    # a.txt should NOT be present since branch_b is now directly on main
    assert not (tmp_path / "a.txt").exists()


def test_move_updates_pr_bases_and_descriptions(
    repo_with_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Move updates affected PR bases and stack descriptions."""
    setup_origin_remote(repo_with_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    switch_branch(repo_with_stack, "branch_a")

    old_stack_body_b = (
        f"{STACK_START_MARKER}\n"
        "## Stack\n"
        "\n"
        "- **#20** (`branch_b`) <-- this PR\n"
        "- #10 (`branch_a`)\n"
        f"{STACK_END_MARKER}\n"
        "\n"
        "Original branch_b description"
    )
    old_stack_body_a = (
        f"{STACK_START_MARKER}\n"
        "## Stack\n"
        "\n"
        "- #20 (`branch_b`)\n"
        "- **#10** (`branch_a`) <-- this PR\n"
        f"{STACK_END_MARKER}\n"
        "\n"
        "Original branch_a description"
    )

    mock_pr_a = PRInfo(
        number=10,
        url="https://github.com/owner/repo/pull/10",
        base="main",
        title="feat: branch a",
        body=old_stack_body_a,
        state="open",
        is_draft=False,
    )
    mock_pr_b = PRInfo(
        number=20,
        url="https://github.com/owner/repo/pull/20",
        base="branch_a",
        title="feat: branch b",
        body=old_stack_body_b,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = lambda b: {
        "branch_a": mock_pr_a,
        "branch_b": mock_pr_b,
    }.get(b)
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.move.GitHubClient", return_value=mock_client):
        _move(repo_with_stack, "branch_b", "main")

    base_updates_for_b = [
        call
        for call in mock_client.update_pr.call_args_list
        if call[0][0] == 20 and call[1].get("base") == "main"
    ]
    assert base_updates_for_b, "branch_b's PR base was not updated to main"

    body_updates_for_b = [
        call
        for call in mock_client.update_pr.call_args_list
        if call[0][0] == 20 and call[1].get("body") is not None
    ]
    assert body_updates_for_b, "branch_b's PR body was not updated after move"
    new_body_b = body_updates_for_b[-1][1]["body"]
    assert "branch_a" not in new_body_b
    assert "branch_b" in new_body_b
    assert "Original branch_b description" in new_body_b

    body_updates_for_a = [
        call
        for call in mock_client.update_pr.call_args_list
        if call[0][0] == 10 and call[1].get("body") is not None
    ]
    assert body_updates_for_a, "branch_a's PR body was not updated after move"
    new_body_a = body_updates_for_a[-1][1]["body"]
    assert "branch_b" not in new_body_a
    assert "branch_a" in new_body_a
    assert "Original branch_a description" in new_body_a


def test_move_pr_sync_errors_are_non_fatal(
    repo_with_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub sync failures should not make the move fail."""
    setup_origin_remote(repo_with_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    switch_branch(repo_with_stack, "branch_a")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = httpx.RequestError("network down")
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.move.GitHubClient", return_value=mock_client):
        result = _move(repo_with_stack, "branch_b", "main")

    assert result.new_parent == "main"
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    assert git.get_branch_parent(repo_with_stack, "branch_b", all_branches) == "main"


# --- CLI tests ---


def test_move_cli_basic(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: basic move."""
    monkeypatch.chdir(tmp_path)
    switch_branch(repo_with_stack, "branch_b")
    result = runner.invoke(app, ["move", "--parent", "main"])
    assert result.exit_code == 0
    assert "Moved 'branch_b' from 'branch_a' to 'main'" in result.output


def test_move_cli_same_parent(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: no-op when same parent."""
    monkeypatch.chdir(tmp_path)
    switch_branch(repo_with_stack, "branch_b")
    result = runner.invoke(app, ["move", "--parent", "branch_a"])
    assert result.exit_code == 0
    assert "Nothing to do" in result.output


def test_move_cli_error(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: error message displayed."""
    monkeypatch.chdir(tmp_path)
    switch_branch(repo_with_stack, "branch_b")
    result = runner.invoke(app, ["move", "--parent", "nonexistent"])
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_move_cli_explicit_branch(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: move a specific branch."""
    monkeypatch.chdir(tmp_path)
    switch_branch(repo_with_stack, "branch_a")
    result = runner.invoke(app, ["move", "branch_b", "--parent", "main"])
    assert result.exit_code == 0
    assert "Moved 'branch_b'" in result.output


def test_move_cli_with_children(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: move with children shows restacked count."""
    monkeypatch.chdir(tmp_path)
    _create_stack_3(temp_repo, tmp_path)
    switch_branch(temp_repo, "branch_b")

    result = runner.invoke(app, ["move", "branch_a", "--parent", "branch_b"])
    # branch_a cannot move onto branch_b because branch_b is a descendant
    assert result.exit_code == 1
    assert "descendant" in result.output


def test_move_cli_with_children_message(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: move with children shows restacked count."""
    monkeypatch.chdir(tmp_path)

    # Create new_parent from main
    main_sha = get_ref(repo_with_stack, "refs/heads/main")
    set_ref(repo_with_stack, "refs/heads/new_parent", main_sha)
    switch_branch(repo_with_stack, "new_parent")
    (tmp_path / "np.txt").write_text("np content")
    add_paths(repo_with_stack, tmp_path / "np.txt")
    msg_np = Trailers(parent_branch="main").apply_to("feat: new parent")
    commit(repo_with_stack, msg_np)

    # Move branch_a (which has child branch_b) to new_parent
    switch_branch(repo_with_stack, "branch_a")
    result = runner.invoke(app, ["move", "-p", "new_parent"])
    assert result.exit_code == 0
    assert "Moved 'branch_a'" in result.output
    assert "Restacked 1 child branch(es)" in result.output


def test_move_cli_short_option(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: -p short option works."""
    monkeypatch.chdir(tmp_path)
    switch_branch(repo_with_stack, "branch_b")
    result = runner.invoke(app, ["move", "-p", "main"])
    assert result.exit_code == 0
    assert "Moved 'branch_b'" in result.output


def test_move_cli_conflict_exit_code(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI: conflict returns exit code 1."""
    monkeypatch.chdir(tmp_path)
    main_sha = get_ref(temp_repo, "refs/heads/main")

    # Create shared.txt on main
    switch_branch(temp_repo, "main")
    (tmp_path / "shared.txt").write_text("original")
    add_paths(temp_repo, tmp_path / "shared.txt")
    commit(temp_repo, b"add shared.txt")
    main_sha = get_ref(temp_repo, "refs/heads/main")

    # branch_a: parent of branch_b, modifies shared.txt
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "shared.txt").write_text("content from A")
    add_paths(temp_repo, tmp_path / "shared.txt")
    msg_a = Trailers(parent_branch="main").apply_to("feat: branch a")
    commit(temp_repo, msg_a)
    branch_a_sha = get_ref(temp_repo, "refs/heads/branch_a")

    # branch_b on top of branch_a (modifies shared.txt differently)
    set_ref(temp_repo, "refs/heads/branch_b", branch_a_sha)
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "shared.txt").write_text("content from B")
    add_paths(temp_repo, tmp_path / "shared.txt")
    msg_b = Trailers(parent_branch="branch_a").apply_to("feat: branch b")
    commit(temp_repo, msg_b)

    # new_target: diverges from main, also modifies shared.txt
    set_ref(temp_repo, "refs/heads/new_target", main_sha)
    switch_branch(temp_repo, "new_target")
    (tmp_path / "shared.txt").write_text("content from new_target")
    add_paths(temp_repo, tmp_path / "shared.txt")
    msg_nt = Trailers(parent_branch="main").apply_to("feat: new target")
    commit(temp_repo, msg_nt)

    # Move branch_b (which modifies shared.txt) onto new_target (also modifies it)
    switch_branch(temp_repo, "branch_b")
    result = runner.invoke(app, ["move", "-p", "new_target"])
    assert result.exit_code == 1

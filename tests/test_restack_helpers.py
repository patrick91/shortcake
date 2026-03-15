"""Tests for restack helper functions."""

import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.continue_ import _continue_rebase
from shortcake.commands.restack import (
    RestackError,
    _get_conflict_files,
    _get_stack_in_order,
    _plan_restack,
    _restack,
    _show_conflict_message,
)
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    get_ref,
    run_git,
    set_ref,
    switch_branch,
)

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_get_conflict_files(tmp_path: Path) -> None:
    """Test getting conflict files from a repo."""
    # Create a repo with no conflicts - should return empty list
    files = _get_conflict_files(str(tmp_path))
    assert files == []


def test_show_conflict_message_with_files(capsys: pytest.CaptureFixture[str]) -> None:
    """Test conflict message display with files."""
    _show_conflict_message("branch_a", "main", ["file1.py", "file2.py"])

    captured = capsys.readouterr()
    assert "Conflict while rebasing 'branch_a' onto 'main'" in captured.out
    assert "file1.py" in captured.out
    assert "file2.py" in captured.out
    assert "sc continue" in captured.out
    assert "sc abort" in captured.out


def test_show_conflict_message_no_files(capsys: pytest.CaptureFixture[str]) -> None:
    """Test conflict message display without files."""
    _show_conflict_message("branch_a", "main", [])

    captured = capsys.readouterr()
    assert "Conflict while rebasing 'branch_a' onto 'main'" in captured.out
    assert "sc continue" in captured.out


def test_restack_git_rebase_in_progress(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test error when git rebase is in progress."""
    # Create fake rebase-merge directory
    rebase_dir = Path(repo_with_stack.path.rstrip("/")) / "rebase-merge"
    rebase_dir.mkdir()

    with pytest.raises(RestackError, match="Git rebase in progress"):
        _restack(repo_with_stack)

    # Cleanup
    rebase_dir.rmdir()


def test_get_stack_in_order_with_nonlocal_parent(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test stack order when parent exists but not as local branch."""
    # Create branch_a with trailer pointing to non-existent local branch
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("content")
    add_paths(temp_repo, file_a)
    # Parent points to "origin/main" which is not a local branch
    trailers = Trailers(parent_branch="origin/main")
    message = trailers.apply_to("feat: branch a")
    commit(temp_repo, message)

    order = _get_stack_in_order(temp_repo, "branch_a")
    # Should return just branch_a since parent is not local
    assert order == ["branch_a"]


def test_continue_with_state(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue when state exists, rebase done, no rebase in progress."""
    monkeypatch.chdir(tmp_path)

    # Use repo_with_stack (not _behind) - branches are already up to date
    # Create state as if restack completed current step
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
        ],
        current_index=0,  # Already at last item
        original_refs={
            "branch_a": git.get_branch_head(repo_with_stack, "branch_a").decode(),
        },
    )
    state.save(repo_with_stack)

    # Continue should complete (branch_a is already on main, no more work)
    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 0
    assert "completed" in result.output.lower()


def test_continue_detects_aborted_rebase(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue detects when rebase was manually aborted."""
    monkeypatch.chdir(tmp_path)

    # Create state but branch_a still needs rebasing (simulates manual abort)
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
        ],
        current_index=0,
        original_refs={
            "branch_a": git.get_branch_head(
                repo_with_stack_behind, "branch_a"
            ).decode(),
        },
    )
    state.save(repo_with_stack_behind)

    # Continue should fail - branch_a wasn't rebased
    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert (
        "was not rebased" in result.output.lower()
        or "manually aborted" in result.output.lower()
    )


def test_continue_parent_branch_deleted(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue fails gracefully when parent branch was deleted."""
    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    # Create state referencing a parent branch that will be deleted
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="deleted_parent", merge_base="abc123"),
        ],
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Parent branch "deleted_parent" never existed, simulating deletion
    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert "no longer exists" in result.output.lower()
    assert "deleted_parent" in result.output


def test_continue_parent_deleted_in_remaining_step(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue fails when parent of remaining branch was deleted."""
    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Create state where first step is done but second step's parent doesn't exist
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(
                branch="branch_a",
                onto="main",
                merge_base=main_sha.decode(),
            ),
            RestackStep(
                branch="branch_b",
                onto="deleted_parent",  # This parent doesn't exist
                merge_base=branch_a_sha.decode(),
            ),
        ],
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
            "branch_b": branch_b_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Continue should succeed for branch_a then fail for branch_b
    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert "no longer exists" in result.output.lower()
    assert "deleted_parent" in result.output


def test_abort_with_rebase_in_progress(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test abort when git rebase is also in progress."""
    monkeypatch.chdir(tmp_path)

    # Store original SHAs
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")

    # Create rebase-merge directory to simulate in-progress rebase
    rebase_dir = Path(repo_with_stack_behind.path.rstrip("/")) / "rebase-merge"
    rebase_dir.mkdir()

    # Create state
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
        ],
        current_index=0,
        original_refs={
            "branch_a": original_a.decode(),
        },
    )
    state.save(repo_with_stack_behind)

    result = runner.invoke(app, ["abort"])

    # Should have tried to abort the rebase (even though it will fail)
    # and then restore refs
    assert result.exit_code == 0
    assert "aborted" in result.output.lower()


def test_plan_restack_with_untracked_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """Test plan when branch has no parent trailer."""
    # Create branch without trailer
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/untracked", main_sha)

    plan = _plan_restack(temp_repo, ["untracked"])
    assert plan == []


def test_plan_restack_parent_not_exists(temp_repo: Repo, tmp_path: Path) -> None:
    """Test plan when parent branch doesn't exist."""
    # Create branch with trailer pointing to non-existent branch
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/orphan", main_sha)
    temp_repo.set_head("refs/heads/orphan")

    file_o = tmp_path / "orphan.txt"
    file_o.write_text("content")
    add_paths(temp_repo, file_o)
    trailers = Trailers(parent_branch="nonexistent")
    message = trailers.apply_to("feat: orphan")
    commit(temp_repo, message)

    plan = _plan_restack(temp_repo, ["orphan"])
    assert plan == []


def test_plan_restack_unrelated_histories(temp_repo: Repo, tmp_path: Path) -> None:
    """Test plan raises error when branch has unrelated history with parent."""
    # Create an orphan branch with unrelated history.
    run_git(temp_repo, "checkout", "--orphan", "orphan")
    readme = tmp_path / "README.md"
    if readme.exists():
        readme.unlink()
    orphan_file = tmp_path / "orphan.txt"
    orphan_file.write_text("orphan content")
    run_git(temp_repo, "add", "-A")
    commit(temp_repo, "feat: orphan branch\n\nShortcake-Parent: main")

    # Now add a commit to main to make orphan "need" rebasing
    switch_branch(temp_repo, "main")
    main_file = tmp_path / "main_update.txt"
    main_file.write_text("main update")
    add_paths(temp_repo, main_file)
    commit(temp_repo, b"chore: update main")

    # Should raise RestackError because orphan has no common history with main
    with pytest.raises(RestackError, match="no common history"):
        _plan_restack(temp_repo, ["orphan"])


def test_get_stack_visited_branch(repo_with_fork: Repo) -> None:
    """Test BFS handles visiting same branch from different paths."""
    # The forked repo has branches that might be visited multiple times
    # in BFS if not tracked properly
    order = _get_stack_in_order(repo_with_fork, "branch_b")
    # Should not have duplicates
    assert len(order) == len(set(order))


def test_cli_restack_help() -> None:
    """Test CLI restack --help."""
    result = runner.invoke(app, ["restack", "--help"])
    assert result.exit_code == 0
    output = strip_ansi(result.output)
    assert "--dry-run" in output


def test_cli_continue_help() -> None:
    """Test CLI continue --help."""
    result = runner.invoke(app, ["continue", "--help"])
    assert result.exit_code == 0


def test_cli_abort_help() -> None:
    """Test CLI abort --help."""
    result = runner.invoke(app, ["abort", "--help"])
    assert result.exit_code == 0


def test_continue_with_multiple_remaining_branches(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue with multiple branches left to rebase."""
    monkeypatch.chdir(tmp_path)

    # Get current SHAs
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Create state with multiple remaining branches
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(
                branch="branch_a",
                onto="main",
                merge_base=main_sha.decode(),
            ),
            RestackStep(
                branch="branch_b",
                onto="branch_a",
                merge_base=branch_a_sha.decode(),
            ),
        ],
        current_index=0,  # Start from branch_a
        original_refs={
            "branch_a": branch_a_sha.decode(),
            "branch_b": branch_b_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Continue should process remaining branches
    result = runner.invoke(app, ["continue"])

    # These rebases should be no-ops since everything is up to date
    assert result.exit_code == 0


def test_continue_rebase_in_progress(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue when git rebase is in progress but still has conflicts."""
    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    # Create rebase-merge directory to simulate in-progress rebase
    rebase_dir = Path(repo_with_stack.path.rstrip("/")) / "rebase-merge"
    rebase_dir.mkdir()

    # Create state
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_a",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
        ],
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Mock _continue_rebase to return conflict result (simulating ongoing conflict)
    monkeypatch.setattr(
        "shortcake.commands.continue_._continue_rebase",
        lambda repo: git.RebaseResult(success=False, conflict=True),
    )

    # Continue should try to continue the rebase and fail
    result = runner.invoke(app, ["continue"])

    # The rebase continue will fail, showing conflict message
    assert result.exit_code == 1
    assert "Conflict" in result.output or "continuing" in result.output.lower()


def test_continue_rebase_function(temp_repo: Repo) -> None:
    """Test _continue_rebase function directly."""
    # When no rebase is in progress, git rebase --continue returns failure.
    # This is expected since _continue_rebase is only called after
    # checking is_rebase_in_progress.
    result = _continue_rebase(temp_repo)
    assert result.success is False  # No rebase in progress = failure


def test_continue_rebase_function_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _continue_rebase returns failure when git returns non-zero."""

    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)
    result = _continue_rebase(temp_repo)
    assert result.success is False


def test_cli_restack_dry_run_shows_branches(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test dry run shows branch names in output."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack", "-n"])

    assert result.exit_code == 0
    assert "branch_a" in result.output
    assert "onto" in result.output.lower()


def test_restack_conflict_returns_conflict_branch(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack returns conflict info when rebase fails."""
    monkeypatch.chdir(tmp_path)

    # Create a scenario that will cause a rebase conflict
    # Create branch_a from main with a file
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")

    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text("branch_a content")
    add_paths(temp_repo, conflict_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: branch a")
    commit(temp_repo, message)

    # Now add a conflicting commit to main
    switch_branch(temp_repo, "main")
    conflict_file.write_text("main content - different!")
    add_paths(temp_repo, conflict_file)
    commit(temp_repo, b"chore: conflicting change on main")

    # Switch back to branch_a
    switch_branch(temp_repo, "branch_a")

    # Restack should hit a conflict
    result = runner.invoke(app, ["restack"])

    # Should exit with error due to conflict
    assert result.exit_code == 1
    assert "conflict" in result.output.lower()


def test_continue_conflict_in_remaining_branch(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue when remaining branch has conflict."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a and branch_b with conflicting content
    main_sha = get_ref(temp_repo, "refs/heads/main")

    # Branch A
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")
    file_a = tmp_path / "file.txt"
    file_a.write_text("branch_a content")
    add_paths(temp_repo, file_a)
    trailers_a = Trailers(parent_branch="main")
    commit(temp_repo, trailers_a.apply_to("feat: a"))
    branch_a_sha = get_ref(temp_repo, "refs/heads/branch_a")

    # Branch B with conflicting content
    set_ref(temp_repo, "refs/heads/branch_b", branch_a_sha)
    temp_repo.set_head("refs/heads/branch_b")
    file_a.write_text("branch_b different content")
    add_paths(temp_repo, file_a)
    trailers_b = Trailers(parent_branch="branch_a")
    commit(temp_repo, trailers_b.apply_to("feat: b"))
    branch_b_sha = get_ref(temp_repo, "refs/heads/branch_b")

    # Modify branch_a to create conflict with branch_b
    switch_branch(temp_repo, "branch_a")
    file_a.write_text("branch_a modified - will conflict with b")
    add_paths(temp_repo, file_a)
    commit(temp_repo, b"chore: modify a")

    # Create state as if we just finished rebasing branch_a
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(
                branch="branch_a",
                onto="main",
                merge_base=main_sha.decode(),
            ),
            RestackStep(
                branch="branch_b",
                onto="branch_a",
                merge_base=branch_a_sha.decode(),
            ),
        ],
        current_index=0,  # Just finished branch_a, will do branch_b next
        original_refs={
            "branch_a": branch_a_sha.decode(),
            "branch_b": branch_b_sha.decode(),
        },
    )
    state.save(temp_repo)

    # Continue should try branch_b and hit conflict
    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    # Should show conflict message
    assert "conflict" in result.output.lower() or "branch_b" in result.output


def test_integration_restack_continue_with_real_conflict(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration test: restack creates conflict, resolve it, then continue."""
    monkeypatch.chdir(tmp_path)

    # Ensure user identity is set
    temp_repo.config["user.email"] = "test@example.com"

    temp_repo.config["user.name"] = "Test User"

    # Create branch_a from main with a file
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")

    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text("branch_a content")
    add_paths(temp_repo, conflict_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: branch a")
    commit(temp_repo, message)

    # Add conflicting commit to main
    switch_branch(temp_repo, "main")
    conflict_file.write_text("main content - different!")
    add_paths(temp_repo, conflict_file)
    commit(temp_repo, b"chore: conflicting change on main")

    # Switch back to branch_a and run restack (will hit conflict)
    switch_branch(temp_repo, "branch_a")
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 1
    assert "conflict" in result.output.lower()

    # Verify rebase is in progress
    assert git.is_rebase_in_progress(temp_repo)

    # Resolve the conflict manually
    conflict_file.write_text("resolved content")
    add_paths(temp_repo, conflict_file)

    # Continue the restack
    result = runner.invoke(app, ["continue"])
    assert result.exit_code == 0, f"Continue failed: {result.output}"
    assert "completed" in result.output.lower()

    # Verify rebase is no longer in progress
    assert not git.is_rebase_in_progress(temp_repo)


def test_integration_restack_abort_with_real_conflict(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration test: restack creates conflict, then abort restores state."""
    monkeypatch.chdir(tmp_path)

    # Ensure user identity is set
    temp_repo.config["user.email"] = "test@example.com"

    temp_repo.config["user.name"] = "Test User"

    # Create branch_a from main with a file
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/branch_a", main_sha)
    temp_repo.set_head("refs/heads/branch_a")

    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text("branch_a content")
    add_paths(temp_repo, conflict_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: branch a")
    commit(temp_repo, message)
    original_branch_a_sha = get_ref(temp_repo, "refs/heads/branch_a")

    # Add conflicting commit to main
    switch_branch(temp_repo, "main")
    conflict_file.write_text("main content - different!")
    add_paths(temp_repo, conflict_file)
    commit(temp_repo, b"chore: conflicting change on main")

    # Switch back to branch_a and run restack (will hit conflict)
    switch_branch(temp_repo, "branch_a")
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 1
    assert "conflict" in result.output.lower()

    # Verify rebase is in progress
    assert git.is_rebase_in_progress(temp_repo)

    # Abort the restack
    result = runner.invoke(app, ["abort"])
    assert result.exit_code == 0
    assert "aborted" in result.output.lower()

    # Verify rebase is no longer in progress
    assert not git.is_rebase_in_progress(temp_repo)

    # Verify branch_a was restored to original SHA
    assert get_ref(temp_repo, "refs/heads/branch_a") == original_branch_a_sha

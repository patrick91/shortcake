"""Tests for restack continue edge cases."""

import re
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake._trailers import Trailers
from shortcake.cli import app

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_apply_remaining_commits_commit_not_found(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _apply_remaining_commits when 'after' commit is not in list."""
    from shortcake.commands.continue_ import _apply_remaining_commits

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Use a fake 'after' SHA that won't be found
    fake_after = b"0" * 40

    result = _apply_remaining_commits(
        repo_with_stack,
        "branch_a",
        main_sha.decode(),
        branch_a_sha.decode(),
        fake_after,
    )
    # Should start from beginning (start_index=0) since commit not found
    assert result.success is True


def test_apply_remaining_commits_get_rebase_commits_fails(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _apply_remaining_commits handles get_rebase_commits error."""
    from shortcake.commands.continue_ import _apply_remaining_commits

    # Mock get_rebase_commits to raise ValueError (e.g., non-linear history)
    def mock_get_rebase_commits(repo, head, merge_base):
        raise ValueError("Non-linear history detected")

    monkeypatch.setattr(git, "get_rebase_commits", mock_get_rebase_commits)

    result = _apply_remaining_commits(
        temp_repo,
        "main",
        "abc123",
        "def456",
        None,
    )
    assert result.success is False
    assert "Non-linear history" in (result.error_output or "")


def test_apply_remaining_commits_cherry_pick_fails(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _apply_remaining_commits handles cherry-pick failure."""
    from shortcake.commands.continue_ import _apply_remaining_commits

    # Create branch_a with multiple commits
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a1 = tmp_path / "a1.txt"
    file_a1.write_text("content 1")
    porcelain.add(temp_repo, paths=[str(file_a1)])
    porcelain.commit(temp_repo, message=b"commit 1")

    file_a2 = tmp_path / "a2.txt"
    file_a2.write_text("content 2")
    porcelain.add(temp_repo, paths=[str(file_a2)])
    porcelain.commit(temp_repo, message=b"commit 2")
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Mock cherry_pick to fail
    def mock_cherry_pick(repo, commit):
        raise git.RebaseFailure("Cherry-pick failed")

    monkeypatch.setattr(git, "cherry_pick", mock_cherry_pick)

    result = _apply_remaining_commits(
        temp_repo,
        "branch_a",
        main_sha.decode(),
        branch_a_sha.decode(),
        None,
    )
    assert result.success is False
    assert "Cherry-pick failed" in (result.error_output or "")


def test_continue_apply_remaining_fails_not_rebase(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue shows error when apply_remaining fails, no rebase."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers.apply_to("feat: a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create rebase-merge to simulate rebase in progress
    rebase_dir = Path(temp_repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir()

    # Create CHERRY_PICK_HEAD
    cherry_pick_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    cherry_pick_path.write_bytes(branch_a_sha)

    # Create state
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_a",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base=main_sha.decode()),
        ],
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
        },
    )
    state.save(temp_repo)

    # Mock _continue_rebase to succeed (meaning rebase continued)
    monkeypatch.setattr(
        "shortcake.commands.continue_._continue_rebase", lambda repo: True
    )

    # Mock _apply_remaining_commits to fail without creating rebase state
    def mock_apply(repo, branch, merge_base, original_head, after):
        from shortcake.commands.restack import RebaseResult

        return RebaseResult(success=False, error_output="Some error")

    monkeypatch.setattr(
        "shortcake.commands.continue_._apply_remaining_commits", mock_apply
    )

    # Make sure is_rebase_in_progress returns False after mocked apply
    call_count = [0]

    def mock_is_rebase(repo):
        call_count[0] += 1
        # First call - in progress, after - not in progress
        return call_count[0] == 1

    monkeypatch.setattr(git, "is_rebase_in_progress", mock_is_rebase)

    result = runner.invoke(app, ["continue"])

    # Should show error (not conflict message)
    assert result.exit_code == 1
    assert "Failed to rebase" in result.output or "error" in result.output.lower()


def test_continue_remaining_branch_rebase_not_conflict(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue handles rebase error (not conflict) in remaining branches."""
    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Create state with multiple branches - start at index 0 which is already done
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
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
            "branch_b": branch_b_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Mock _rebase_branch to fail without conflict
    def mock_rebase(repo, branch, onto, merge_base):
        from shortcake.commands.restack import RebaseResult

        return RebaseResult(success=False, error_output="fatal: error")

    monkeypatch.setattr("shortcake.commands.continue_._rebase_branch", mock_rebase)
    # Also mock is_rebase_in_progress to return False (not a conflict)
    monkeypatch.setattr(
        "shortcake.commands.continue_.git.is_rebase_in_progress", lambda repo: False
    )

    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    # Should show the error message
    assert "Failed to rebase" in result.output


def test_continue_apply_remaining_fails_with_conflict(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue shows conflict message when apply_remaining fails with conflict."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers.apply_to("feat: a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create rebase-merge directory to simulate rebase in progress
    rebase_dir = Path(temp_repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir()

    # Create CHERRY_PICK_HEAD
    cherry_pick_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    cherry_pick_path.write_bytes(branch_a_sha)

    # Create state
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_a",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base=main_sha.decode()),
        ],
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
        },
    )
    state.save(temp_repo)

    # Mock _continue_rebase to succeed (meaning rebase continued)
    monkeypatch.setattr(
        "shortcake.commands.continue_._continue_rebase", lambda repo: True
    )

    # Mock _apply_remaining_commits to fail
    def mock_apply(repo, branch, merge_base, original_head, after):
        from shortcake.commands.restack import RebaseResult

        return RebaseResult(success=False, error_output="Conflict")

    monkeypatch.setattr(
        "shortcake.commands.continue_._apply_remaining_commits", mock_apply
    )

    # Keep is_rebase_in_progress returning True (conflict state)
    monkeypatch.setattr(
        "shortcake.commands.continue_.git.is_rebase_in_progress", lambda repo: True
    )

    result = runner.invoke(app, ["continue"])

    # Should show conflict message
    assert result.exit_code == 1
    assert "Conflict" in result.output


def test_continue_remaining_branch_conflict(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue shows conflict message when remaining branch hits conflict."""
    from shortcake.commands import continue_ as continue_module

    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Create state with multiple branches - start at index 0 which is already done
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
        current_index=0,
        original_refs={
            "branch_a": branch_a_sha.decode(),
            "branch_b": branch_b_sha.decode(),
        },
    )
    state.save(repo_with_stack)

    # Track if we got to the loop
    rebase_called = [False]

    # Mock _rebase_branch to fail with conflict on branch_b
    def mock_rebase(repo, branch, onto, merge_base):
        from shortcake.commands.restack import RebaseResult

        rebase_called[0] = True
        # branch_b will fail with conflict
        return RebaseResult(success=False, error_output="")

    # Patch directly on the module object
    monkeypatch.setattr(continue_module, "_rebase_branch", mock_rebase)

    # Mock _needs_restack to return False (branch is up to date)
    def mock_needs_restack(repo, branch, onto):
        return False

    monkeypatch.setattr(continue_module, "_needs_restack", mock_needs_restack)

    # Track calls to is_rebase_in_progress
    # First call should return False, second call returns True
    call_count = [0]

    def mock_is_rebase_in_progress(repo):
        call_count[0] += 1
        return call_count[0] > 1

    monkeypatch.setattr(
        continue_module.git, "is_rebase_in_progress", mock_is_rebase_in_progress
    )

    # Mock _get_conflict_files to return some files
    def mock_get_conflict_files(path):
        return ["file.txt"]

    monkeypatch.setattr(continue_module, "_get_conflict_files", mock_get_conflict_files)

    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert rebase_called[0], f"Mock not called. Output: {result.output}"
    # Should show conflict message with the file
    assert "Conflict" in result.output or "file.txt" in result.output

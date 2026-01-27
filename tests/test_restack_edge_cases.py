"""Tests for restack edge cases."""

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
from shortcake.commands.restack import (
    _get_stack_in_order,
)

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_show_rebase_error_with_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Test _show_rebase_error displays error output."""
    from shortcake.commands.restack import _show_rebase_error

    _show_rebase_error("branch_a", "main", "fatal: some git error\nmore details")

    captured = capsys.readouterr()
    assert "Failed to rebase 'branch_a' onto 'main'" in captured.err
    assert "Git error:" in captured.err
    assert "fatal: some git error" in captured.err
    assert "more details" in captured.err
    assert "sc abort" in captured.err


def test_show_rebase_error_empty_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Test _show_rebase_error with no error output."""
    from shortcake.commands.restack import _show_rebase_error

    _show_rebase_error("branch_a", "main", "")

    captured = capsys.readouterr()
    assert "Failed to rebase 'branch_a' onto 'main'" in captured.err
    assert "Git error:" not in captured.err
    assert "sc abort" in captured.err


def test_restack_non_conflict_failure(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack handles non-conflict rebase failure."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers.apply_to("feat: a").encode())

    # Add commit to main
    porcelain.switch(temp_repo, "main")
    main_file = tmp_path / "main.txt"
    main_file.write_text("main content")
    porcelain.add(temp_repo, paths=[str(main_file)])
    porcelain.commit(temp_repo, message=b"chore: main update")

    porcelain.switch(temp_repo, "branch_a")

    # Mock _rebase_branch to fail without creating a conflict state
    def mock_rebase(repo_path, branch, onto, merge_base):
        from shortcake.commands.restack import RebaseResult

        return RebaseResult(success=False, error_output="fatal: some error")

    monkeypatch.setattr("shortcake.commands.restack._rebase_branch", mock_rebase)

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 1
    assert "Failed to rebase" in result.output or "error" in result.output.lower()


def test_continue_non_conflict_failure_in_remaining(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue handles non-conflict rebase failure in remaining branches."""
    monkeypatch.chdir(tmp_path)

    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Create state with multiple branches
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

    # Track call count to fail only on second call
    call_count = [0]

    def mock_rebase(repo_path, branch, onto, merge_base):
        from shortcake.commands.restack import RebaseResult

        call_count[0] += 1
        if call_count[0] == 1:
            # First call (branch_b) - fail without conflict
            return RebaseResult(success=False, error_output="fatal: some error")
        return RebaseResult(success=True, error_output="")

    monkeypatch.setattr("shortcake.commands.continue_._rebase_branch", mock_rebase)

    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert "Failed to rebase" in result.output or "error" in result.output.lower()


def test_get_stack_in_order_finds_stack_root(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _get_stack_in_order correctly identifies stack root via parent=None path."""
    # Create a deep stack: main → branch_a → branch_b → branch_c
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: b").encode())
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # branch_c
    temp_repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(temp_repo, paths=[str(file_c)])
    trailers_c = Trailers(parent_branch="branch_b")
    porcelain.commit(temp_repo, message=trailers_c.apply_to("feat: c").encode())

    # Get stack from branch_c - should find branch_a as root
    order = _get_stack_in_order(temp_repo, "branch_c")

    # Should include all three in order: branch_a, branch_b, branch_c
    assert order == ["branch_a", "branch_b", "branch_c"]

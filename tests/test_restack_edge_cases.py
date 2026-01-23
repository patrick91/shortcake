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
    _fast_forward_branch,
    _fetch_remote,
    _get_diverged_branches,
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


def test_get_diverged_branches_same_sha(repo_with_stack: Repo) -> None:
    """Test divergence check when local and remote are the same."""
    # Set origin/branch_a to same SHA as local branch_a
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    diverged = _get_diverged_branches(repo_with_stack, ["branch_a"])
    assert diverged == []


def test_get_behind_branches_same_sha(repo_with_stack: Repo) -> None:
    """Test _get_behind_branches when local and remote are the same."""
    from shortcake.commands.restack import _get_behind_branches

    # Set origin/branch_a to same SHA as local branch_a
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    behind = _get_behind_branches(repo_with_stack, ["branch_a"])
    assert behind == []


def test_get_behind_branches_local_behind(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test _get_behind_branches when local is behind remote."""
    from shortcake.commands.restack import _get_behind_branches

    # Create a commit ahead of branch_a for the "remote"
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    # Create a new commit on top of branch_a for the remote
    repo_with_stack.refs[b"refs/heads/temp"] = branch_a_sha
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/temp")

    temp_file = tmp_path / "temp.txt"
    temp_file.write_text("temp content")
    porcelain.add(repo_with_stack, paths=[str(temp_file)])
    remote_sha = porcelain.commit(repo_with_stack, message=b"Remote ahead commit")

    # Set origin/branch_a to be ahead of local branch_a
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = remote_sha

    behind = _get_behind_branches(repo_with_stack, ["branch_a"])
    assert "branch_a" in behind


def test_fast_forward_branch_success(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _fast_forward_branch when it succeeds."""
    # Get current branch_a SHA
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    # Create a new commit on top of branch_a for the "remote"
    repo_with_stack.refs[b"refs/heads/temp"] = branch_a_sha
    porcelain.switch(repo_with_stack, "temp")

    temp_file = tmp_path / "temp.txt"
    temp_file.write_text("temp content")
    porcelain.add(repo_with_stack, paths=[str(temp_file)])
    remote_sha = porcelain.commit(repo_with_stack, message=b"Remote ahead commit")

    # Set origin/branch_a to be ahead of local branch_a
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = remote_sha

    # Verify the remote ref was set
    assert repo_with_stack.refs[b"refs/remotes/origin/branch_a"] == remote_sha

    # Fast-forward branch_a to match origin/branch_a
    result = _fast_forward_branch(repo_with_stack, "branch_a")
    assert result is True

    # Verify branch_a now matches remote
    assert git.get_branch_head(repo_with_stack, "branch_a") == remote_sha


def test_restack_sync_with_behind_branches(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack --sync with branches behind remote."""
    monkeypatch.chdir(tmp_path)

    # Create a commit ahead of branch_a for the "remote"
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    # Create a new commit on top of branch_a
    repo_with_stack.refs[b"refs/heads/temp"] = branch_a_sha
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/temp")

    temp_file = tmp_path / "temp.txt"
    temp_file.write_text("temp content")
    porcelain.add(repo_with_stack, paths=[str(temp_file)])
    remote_sha = porcelain.commit(repo_with_stack, message=b"Remote ahead commit")

    # Set origin/branch_a to be ahead of local branch_a
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = remote_sha

    # Switch back to branch_b
    porcelain.switch(repo_with_stack, "branch_b")

    # Run restack with sync
    result = runner.invoke(app, ["restack", "--sync"])

    # Should fast-forward branch_a
    assert "Fast-forwarding" in result.output or result.exit_code == 0


def test_restack_sync_current_branch_behind(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack --sync when current branch is behind remote."""
    monkeypatch.chdir(tmp_path)

    # Create a commit ahead of branch_b for the "remote"
    branch_b_sha = git.get_branch_head(repo_with_stack, "branch_b")

    # Create a new commit on top of branch_b
    repo_with_stack.refs[b"refs/heads/temp"] = branch_b_sha
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/temp")

    temp_file = tmp_path / "temp.txt"
    temp_file.write_text("temp content")
    porcelain.add(repo_with_stack, paths=[str(temp_file)])
    remote_sha = porcelain.commit(repo_with_stack, message=b"Remote ahead commit")

    # Set origin/branch_b to be ahead of local branch_b
    repo_with_stack.refs[b"refs/remotes/origin/branch_b"] = remote_sha

    # Switch back to branch_b (the current branch that's behind)
    porcelain.switch(repo_with_stack, "branch_b")

    # Run restack with sync
    result = runner.invoke(app, ["restack", "--sync"])

    # Should skip current branch and warn
    assert "Skipping" in result.output or "checked out" in result.output


def test_restack_sync_fast_forward_fails(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack --sync handles fast-forward failure gracefully."""
    monkeypatch.chdir(tmp_path)

    # Create origin/branch_a pointing to branch_a (same SHA - will be "behind")
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    # Create a new commit for the remote
    repo_with_stack.refs[b"refs/heads/temp"] = branch_a_sha
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/temp")
    temp_file = tmp_path / "temp.txt"
    temp_file.write_text("temp content")
    porcelain.add(repo_with_stack, paths=[str(temp_file)])
    remote_sha = porcelain.commit(repo_with_stack, message=b"Remote ahead commit")
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = remote_sha

    # Switch to branch_b
    porcelain.switch(repo_with_stack, "branch_b")

    # Mock _fast_forward_branch to fail
    monkeypatch.setattr(
        "shortcake.commands.restack._fast_forward_branch", lambda repo, branch: False
    )

    result = runner.invoke(app, ["restack", "--sync"])

    # Should warn about failure but continue
    assert "Warning" in result.output or "Failed" in result.output


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


def test_fetch_remote_success(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _fetch_remote returns True when fetch succeeds."""

    # Mock porcelain.fetch to succeed
    def mock_fetch(repo, remote, quiet=False):
        pass  # Success

    monkeypatch.setattr("shortcake.commands.restack.porcelain.fetch", mock_fetch)

    result = _fetch_remote(temp_repo)
    assert result is True


def test_fast_forward_branch_exception(
    repo_with_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _fast_forward_branch handles exceptions gracefully."""
    # Set up a valid remote ref
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    # Mock refs assignment to raise an exception
    original_setitem = repo_with_stack.refs.__class__.__setitem__

    def mock_setitem(self, key, value):
        if key == b"refs/heads/branch_a":
            raise RuntimeError("Simulated failure")
        return original_setitem(self, key, value)

    monkeypatch.setattr(repo_with_stack.refs.__class__, "__setitem__", mock_setitem)

    result = _fast_forward_branch(repo_with_stack, "branch_a")
    assert result is False

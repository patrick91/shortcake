import re
import subprocess
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.abort import AbortError, _abort
from shortcake.commands.continue_ import ContinueError, _continue, _continue_rebase
from shortcake.commands.restack import (
    RestackError,
    _fast_forward_branch,
    _fetch_remote,
    _get_conflict_files,
    _get_diverged_branches,
    _get_stack_in_order,
    _needs_restack,
    _plan_restack,
    _rebase_onto_remote,
    _restack,
    _show_conflict_message,
)

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def repo_with_tracked_feature(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with main and a tracked feature branch."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add a commit on feature with trailer
    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    porcelain.add(temp_repo, paths=[str(test_file)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    porcelain.commit(temp_repo, message=message.encode())

    return temp_repo


@pytest.fixture
def repo_with_stack(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a linear stack: main → branch_a → branch_b."""
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # Commit on branch_a with trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    # Commit on branch_b with trailer
    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    return temp_repo


@pytest.fixture
def repo_with_stack_behind(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with stack where main has moved ahead.

    Creates: main → branch_a → branch_b
    Then adds a commit to main, so branch_a needs rebasing.
    """
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # Commit on branch_a with trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    # Commit on branch_b with trailer
    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    # Now add a commit to main (to make branch_a behind)
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    porcelain.switch(temp_repo, "main")
    main_file = tmp_path / "main_update.txt"
    main_file.write_text("main update")
    porcelain.add(temp_repo, paths=[str(main_file)])
    porcelain.commit(temp_repo, message=b"chore: update main")

    # Switch back to branch_b
    porcelain.switch(temp_repo, "branch_b")

    return temp_repo


@pytest.fixture
def repo_with_fork(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with forked stack: main → branch_a → (branch_b, branch_c)."""
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    # Create branch_c from branch_a (fork)
    temp_repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    file_c = tmp_path / "c.txt"
    file_c.write_text("branch c content")
    porcelain.add(temp_repo, paths=[str(file_c)])
    trailers_c = Trailers(parent_branch="branch_a")
    message_c = trailers_c.apply_to("feat: branch c")
    porcelain.commit(temp_repo, message=message_c.encode())

    return temp_repo


# ============================================================================
# Unit Tests: _needs_restack
# ============================================================================


def test_needs_restack_up_to_date(repo_with_tracked_feature: Repo) -> None:
    """Branch doesn't need restack when parent hasn't changed."""
    assert not _needs_restack(repo_with_tracked_feature, "feature", "main")


def test_needs_restack_behind(repo_with_stack_behind: Repo) -> None:
    """Branch needs restack when parent has new commits."""
    assert _needs_restack(repo_with_stack_behind, "branch_a", "main")


# ============================================================================
# Unit Tests: _get_stack_in_order
# ============================================================================


def test_get_stack_in_order_linear(repo_with_stack: Repo) -> None:
    """Get topological order for linear stack."""
    order = _get_stack_in_order(repo_with_stack, "branch_b")
    assert order == ["branch_a", "branch_b"]


def test_get_stack_in_order_forked(repo_with_fork: Repo) -> None:
    """Get topological order for forked stack."""
    order = _get_stack_in_order(repo_with_fork, "branch_c")
    assert order[0] == "branch_a"
    # branch_b and branch_c can be in any order after branch_a
    assert set(order[1:]) == {"branch_b", "branch_c"}


# ============================================================================
# Unit Tests: _plan_restack
# ============================================================================


def test_plan_restack_nothing_to_do(repo_with_stack: Repo) -> None:
    """No plan when everything up to date."""
    branches = _get_stack_in_order(repo_with_stack, "branch_b")
    plan = _plan_restack(repo_with_stack, branches)
    assert plan == []


def test_plan_restack_linear_stack(repo_with_stack_behind: Repo) -> None:
    """Plan includes branches that need rebasing."""
    branches = _get_stack_in_order(repo_with_stack_behind, "branch_b")
    plan = _plan_restack(repo_with_stack_behind, branches)

    assert len(plan) == 2
    assert plan[0].branch == "branch_a"
    assert plan[0].onto == "main"
    assert plan[1].branch == "branch_b"
    assert plan[1].onto == "branch_a"


# ============================================================================
# Integration Tests: _restack
# ============================================================================


def test_restack_nothing_to_do(repo_with_stack: Repo) -> None:
    """Restack when everything up to date."""
    result = _restack(repo_with_stack)
    assert result.restacked_branches == []
    assert result.conflict_branch is None


def test_restack_dry_run(repo_with_stack_behind: Repo) -> None:
    """Dry run shows plan without executing."""
    # Store original SHAs
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    result = _restack(repo_with_stack_behind, dry_run=True)

    # Should show plan but not execute
    assert result.restacked_branches == []

    # Branches should be unchanged
    assert git.get_branch_head(repo_with_stack_behind, "branch_a") == original_a
    assert git.get_branch_head(repo_with_stack_behind, "branch_b") == original_b


def test_restack_linear_stack(repo_with_stack_behind: Repo, tmp_path: Path) -> None:
    """Restack propagates through linear stack."""
    # Store original SHAs for verification
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    result = _restack(repo_with_stack_behind)

    assert result.restacked_branches == ["branch_a", "branch_b"]
    assert result.conflict_branch is None

    # Branches should have moved
    new_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    new_b = git.get_branch_head(repo_with_stack_behind, "branch_b")
    assert new_a != original_a
    assert new_b != original_b

    # branch_a should now be on top of main
    assert not _needs_restack(repo_with_stack_behind, "branch_a", "main")

    # branch_b should now be on top of branch_a
    assert not _needs_restack(repo_with_stack_behind, "branch_b", "branch_a")


def test_restack_detached_head(temp_repo: Repo) -> None:
    """Error in detached HEAD state."""
    # Detach HEAD by removing the symbolic ref
    head_sha = temp_repo.refs[b"refs/heads/main"]
    # Remove symbolic ref and set HEAD directly to SHA
    temp_repo.refs.remove_if_equals(b"HEAD", temp_repo.refs.read_ref(b"HEAD"))
    temp_repo.refs.add_if_new(b"HEAD", head_sha)

    with pytest.raises(RestackError, match="detached HEAD"):
        _restack(temp_repo)


def test_restack_uncommitted_changes(
    repo_with_stack_behind: Repo, tmp_path: Path
) -> None:
    """Error with uncommitted changes."""
    # Create uncommitted change
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    porcelain.add(repo_with_stack_behind, paths=[str(test_file)])

    with pytest.raises(RestackError, match="uncommitted changes"):
        _restack(repo_with_stack_behind)


def test_restack_already_in_progress(repo_with_stack_behind: Repo) -> None:
    """Error when restack already in progress."""
    # Create fake state file
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[],
        current_index=0,
        original_refs={},
    )
    state.save(repo_with_stack_behind)

    with pytest.raises(RestackError, match="already in progress"):
        _restack(repo_with_stack_behind)


# ============================================================================
# Integration Tests: Continue and Abort
# ============================================================================


def test_continue_nothing_in_progress(temp_repo: Repo) -> None:
    """Error when no restack to continue."""
    with pytest.raises(ContinueError, match="No restack in progress"):
        _continue(temp_repo)


def test_abort_nothing_in_progress(temp_repo: Repo) -> None:
    """Error when no restack to abort."""
    with pytest.raises(AbortError, match="No restack in progress"):
        _abort(temp_repo)


def test_abort_restores_original(repo_with_stack_behind: Repo) -> None:
    """Abort restores all branches to original SHAs."""
    # Store original SHAs
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    # Create state as if restack started
    # dulwich SHAs are 40 ASCII hex bytes, decode to string for storage
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
            RestackStep(branch="branch_b", onto="branch_a", merge_base="def456"),
        ],
        current_index=0,
        original_refs={
            "branch_a": original_a.decode(),
            "branch_b": original_b.decode(),
        },
    )
    state.save(repo_with_stack_behind)

    result = _abort(repo_with_stack_behind)

    assert set(result.restored_branches) == {"branch_a", "branch_b"}

    # Branches should be restored
    assert git.get_branch_head(repo_with_stack_behind, "branch_a") == original_a
    assert git.get_branch_head(repo_with_stack_behind, "branch_b") == original_b

    # State should be deleted
    assert not RestackState.exists(repo_with_stack_behind)


def test_abort_with_invalid_sha(
    repo_with_stack_behind: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abort handles invalid SHA gracefully with warning."""
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    # Create state
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
            RestackStep(branch="branch_b", onto="branch_a", merge_base="def456"),
        ],
        current_index=0,
        original_refs={
            "branch_a": original_a.decode(),
            "branch_b": original_b.decode(),
        },
    )
    state.save(repo_with_stack_behind)

    # Mock update_branch to raise KeyError for branch_b (simulating deleted commit)
    original_update = git.update_branch

    def mock_update_branch(repo: Repo, branch: str, sha_hex: str) -> None:
        if branch == "branch_b":
            raise KeyError(sha_hex)
        original_update(repo, branch, sha_hex)

    monkeypatch.setattr(git, "update_branch", mock_update_branch)

    result = _abort(repo_with_stack_behind)

    # branch_a should be restored, branch_b should fail gracefully
    assert "branch_a" in result.restored_branches
    assert "branch_b" not in result.restored_branches

    # State should still be deleted
    assert not RestackState.exists(repo_with_stack_behind)


# ============================================================================
# Unit Tests: RestackState
# ============================================================================


def test_restack_state_save_load(temp_repo: Repo) -> None:
    """Test state save and load roundtrip."""
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_c",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
            RestackStep(branch="branch_b", onto="branch_a", merge_base="def456"),
        ],
        current_index=1,
        original_refs={
            "branch_a": "aaa111",
            "branch_b": "bbb222",
        },
    )

    state.save(temp_repo)
    loaded = RestackState.load(temp_repo)

    assert loaded is not None
    assert loaded.version == state.version
    assert loaded.original_branch == state.original_branch
    assert len(loaded.plan) == 2
    assert loaded.plan[0].branch == "branch_a"
    assert loaded.plan[0].onto == "main"
    assert loaded.plan[1].branch == "branch_b"
    assert loaded.current_index == 1
    assert loaded.original_refs == state.original_refs


def test_restack_state_delete(temp_repo: Repo) -> None:
    """Test state deletion."""
    state = RestackState(
        version=STATE_VERSION,
        original_branch="test",
        plan=[],
        current_index=0,
        original_refs={},
    )

    state.save(temp_repo)
    assert RestackState.exists(temp_repo)

    state.delete(temp_repo)
    assert not RestackState.exists(temp_repo)


def test_restack_state_load_nonexistent(temp_repo: Repo) -> None:
    """Test loading when no state file exists."""
    assert RestackState.load(temp_repo) is None


# ============================================================================
# Unit Tests: _git functions
# ============================================================================


def test_get_merge_base(repo_with_stack: Repo) -> None:
    """Test merge base calculation."""
    main_sha = git.get_branch_head(repo_with_stack, "main")
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    merge_base = git.get_merge_base(repo_with_stack, main_sha, branch_a_sha)
    assert merge_base == main_sha


def test_is_rebase_in_progress_false(temp_repo: Repo) -> None:
    """Test no rebase in progress."""
    assert not git.is_rebase_in_progress(temp_repo)


def test_has_uncommitted_changes_false(temp_repo: Repo) -> None:
    """Test no uncommitted changes."""
    assert not git.has_uncommitted_changes(temp_repo)


def test_has_uncommitted_changes_staged(temp_repo: Repo, tmp_path: Path) -> None:
    """Test staged changes detected."""
    test_file = tmp_path / "new.txt"
    test_file.write_text("content")
    porcelain.add(temp_repo, paths=[str(test_file)])

    assert git.has_uncommitted_changes(temp_repo)


def test_has_uncommitted_changes_unstaged(temp_repo: Repo, tmp_path: Path) -> None:
    """Test unstaged changes detected."""
    # Modify the README that's already tracked
    readme = tmp_path / "README.md"
    readme.write_text("modified content")

    assert git.has_uncommitted_changes(temp_repo)


def test_is_ancestor_true(repo_with_stack: Repo) -> None:
    """Test is_ancestor when true."""
    main_sha = git.get_branch_head(repo_with_stack, "main")
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    assert git.is_ancestor(repo_with_stack, main_sha, branch_a_sha)


def test_is_ancestor_false(repo_with_stack: Repo) -> None:
    """Test is_ancestor when false."""
    main_sha = git.get_branch_head(repo_with_stack, "main")
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")

    assert not git.is_ancestor(repo_with_stack, branch_a_sha, main_sha)


def test_is_ancestor_same_commit(repo_with_stack: Repo) -> None:
    """Test is_ancestor for same commit."""
    main_sha = git.get_branch_head(repo_with_stack, "main")

    assert git.is_ancestor(repo_with_stack, main_sha, main_sha)


def test_get_remote_ref_nonexistent(temp_repo: Repo) -> None:
    """Test getting nonexistent remote ref."""
    assert git.get_remote_ref(temp_repo, "origin/nonexistent") is None


# ============================================================================
# CLI Tests
# ============================================================================


def test_cli_restack_nothing_to_do(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack when nothing to do."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 0
    assert "Everything up to date" in result.output


def test_cli_restack_dry_run(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack --dry-run."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack", "--dry-run"])

    assert result.exit_code == 0
    assert "Would restack" in result.output


def test_cli_restack_success(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack success."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 0
    assert "Restacked" in result.output
    assert "successfully" in result.output


def test_cli_restack_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack in detached HEAD state."""
    monkeypatch.chdir(tmp_path)
    # Detach HEAD
    head_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs.remove_if_equals(b"HEAD", temp_repo.refs.read_ref(b"HEAD"))
    temp_repo.refs.add_if_new(b"HEAD", head_sha)

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_restack_untracked_branch(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI restack from an untracked branch shows informative message."""
    monkeypatch.chdir(tmp_path)

    # We're on main which has no Shortcake-Parent trailer
    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 0
    assert "not tracked" in result.output
    assert "Nothing to restack" in result.output


def test_cli_continue_nothing_in_progress(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI continue when no restack in progress."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 1
    assert "No restack in progress" in result.output


def test_cli_abort_nothing_in_progress(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI abort when no restack in progress."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["abort"])

    assert result.exit_code == 1
    assert "No restack in progress" in result.output


def test_cli_abort_success(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI abort restores state."""
    monkeypatch.chdir(tmp_path)

    # Store original SHAs
    original_a = git.get_branch_head(repo_with_stack_behind, "branch_a")
    original_b = git.get_branch_head(repo_with_stack_behind, "branch_b")

    # Create state as if restack started
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
        ],
        current_index=0,
        original_refs={
            "branch_a": original_a.decode(),
            "branch_b": original_b.decode(),
        },
    )
    state.save(repo_with_stack_behind)

    result = runner.invoke(app, ["abort"])

    assert result.exit_code == 0
    assert "aborted" in result.output.lower()


# ============================================================================
# Unit Tests: Helper Functions
# ============================================================================


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


def test_get_diverged_branches_no_remote(repo_with_stack: Repo) -> None:
    """Test divergence check with no remote refs."""
    branches = ["branch_a", "branch_b"]
    diverged = _get_diverged_branches(repo_with_stack, branches)
    assert diverged == []


def test_fetch_remote(temp_repo: Repo) -> None:
    """Test fetch remote - should fail gracefully when no remote exists."""
    result = _fetch_remote(temp_repo)
    assert result is False


def test_restack_git_rebase_in_progress(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test error when git rebase is in progress."""
    # Create fake rebase-merge directory
    rebase_dir = Path(repo_with_stack.controldir()) / "rebase-merge"
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
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    # Parent points to "origin/main" which is not a local branch
    trailers = Trailers(parent_branch="origin/main")
    message = trailers.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message.encode())

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
    rebase_dir = Path(repo_with_stack_behind.controldir()) / "rebase-merge"
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
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/untracked"] = main_sha

    from shortcake.commands.restack import _plan_restack

    plan = _plan_restack(temp_repo, ["untracked"])
    assert plan == []


def test_plan_restack_parent_not_exists(temp_repo: Repo, tmp_path: Path) -> None:
    """Test plan when parent branch doesn't exist."""
    # Create branch with trailer pointing to non-existent branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/orphan"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/orphan")

    file_o = tmp_path / "orphan.txt"
    file_o.write_text("content")
    porcelain.add(temp_repo, paths=[str(file_o)])
    trailers = Trailers(parent_branch="nonexistent")
    message = trailers.apply_to("feat: orphan")
    porcelain.commit(temp_repo, message=message.encode())

    from shortcake.commands.restack import _plan_restack

    plan = _plan_restack(temp_repo, ["orphan"])
    assert plan == []


def test_plan_restack_unrelated_histories(temp_repo: Repo, tmp_path: Path) -> None:
    """Test plan raises error when branch has unrelated history with parent."""
    # Create an orphan branch with unrelated history
    # First, create an orphan commit (no parent)
    from dulwich.objects import Blob, Commit, Tree

    # Create a blob
    blob = Blob.from_string(b"orphan content")
    temp_repo.object_store.add_object(blob)

    # Create a tree with the blob
    tree = Tree()
    tree.add(b"orphan.txt", 0o100644, blob.id)
    temp_repo.object_store.add_object(tree)

    # Create an orphan commit (no parents)
    import time

    commit = Commit()
    commit.tree = tree.id
    commit.author = b"Test <test@example.com>"
    commit.committer = b"Test <test@example.com>"
    commit.author_time = commit.commit_time = int(time.time())
    commit.author_timezone = commit.commit_timezone = 0
    commit.encoding = b"UTF-8"
    # Add Shortcake-Parent trailer pointing to main
    commit.message = b"feat: orphan branch\n\nShortcake-Parent: main"
    temp_repo.object_store.add_object(commit)

    # Create branch pointing to this orphan commit
    temp_repo.refs[b"refs/heads/orphan"] = commit.id

    # Now add a commit to main to make orphan "need" rebasing
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    porcelain.switch(temp_repo, "main")
    main_file = tmp_path / "main_update.txt"
    main_file.write_text("main update")
    porcelain.add(temp_repo, paths=[str(main_file)])
    porcelain.commit(temp_repo, message=b"chore: update main")

    from shortcake.commands.restack import _plan_restack

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


def test_fast_forward_branch(temp_repo: Repo) -> None:
    """Test fast forward branch - should fail gracefully when no remote exists."""
    result = _fast_forward_branch(temp_repo, "main")
    # Returns False because there's no origin remote to fetch from
    assert result is False


def test_cli_restack_help() -> None:
    """Test CLI restack --help."""
    result = runner.invoke(app, ["restack", "--help"])
    assert result.exit_code == 0
    output = strip_ansi(result.output)
    assert "--dry-run" in output
    assert "--sync" in output


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
    rebase_dir = Path(repo_with_stack.controldir()) / "rebase-merge"
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

    # Mock _continue_rebase to return False (simulating ongoing conflict)
    monkeypatch.setattr(
        "shortcake.commands.continue_._continue_rebase", lambda repo: False
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
    result = _continue_rebase(temp_repo.path)
    assert result is False  # No rebase in progress = failure


def test_continue_rebase_function_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _continue_rebase returns False when git returns non-zero."""

    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)
    result = _continue_rebase(temp_repo.path)
    assert result is False


def test_restack_with_sync_flag(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack --sync flag."""
    monkeypatch.chdir(tmp_path)

    # Will try to fetch (and fail gracefully since no remote)
    result = runner.invoke(app, ["restack", "--sync"])

    # Should still work (fetch fails silently)
    assert result.exit_code == 0


def test_cli_restack_dry_run_shows_branches(
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test dry run shows branch names in output."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack", "-n"])

    assert result.exit_code == 0
    assert "branch_a" in result.output
    assert "onto" in result.output.lower()


def test_get_diverged_branches_with_diverged_branch(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test divergence detection when branch has truly diverged from remote.

    True divergence means both local and remote have commits the other doesn't.
    """
    # Create a sibling branch from main (not branch_a)
    main_sha = git.get_branch_head(repo_with_stack, "main")
    repo_with_stack.refs[b"refs/heads/sibling"] = main_sha
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/sibling")

    # Create a commit on sibling branch
    sibling_file = tmp_path / "sibling.txt"
    sibling_file.write_text("sibling content")
    porcelain.add(repo_with_stack, paths=[str(sibling_file)])
    sibling_sha = porcelain.commit(repo_with_stack, message=b"Sibling commit on remote")

    # Switch back to branch_a
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # Set up origin/branch_a pointing to sibling commit
    # Now local branch_a and origin/branch_a have diverged:
    # - Local branch_a has commits sibling doesn't have
    # - Sibling has commits branch_a doesn't have
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = sibling_sha

    diverged = _get_diverged_branches(repo_with_stack, ["branch_a"])

    # branch_a should be detected as diverged
    assert "branch_a" in diverged


def test_get_diverged_branches_allows_local_ahead(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test that local-ahead branches are NOT flagged as diverged."""
    # Set origin/branch_a to main (which is an ancestor of branch_a)
    # This simulates "local has unpushed commits" - not true divergence
    main_sha = git.get_branch_head(repo_with_stack, "main")
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = main_sha

    diverged = _get_diverged_branches(repo_with_stack, ["branch_a"])

    # branch_a should NOT be detected as diverged (just local-ahead)
    assert "branch_a" not in diverged


def test_restack_with_diverged_branches(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack fails with truly diverged branches."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a with a commit
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    branch_a_file = tmp_path / "branch_a.txt"
    branch_a_file.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(branch_a_file)])
    porcelain.commit(
        temp_repo,
        message=b"feat: branch_a\n\nShortcake-Parent: main",
    )

    # Create a sibling branch from main for the "remote" commit
    temp_repo.refs[b"refs/heads/sibling"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/sibling")

    sibling_file = tmp_path / "sibling.txt"
    sibling_file.write_text("sibling content")
    porcelain.add(temp_repo, paths=[str(sibling_file)])
    sibling_sha = porcelain.commit(temp_repo, message=b"Sibling commit on remote")

    # Switch back to branch_a
    porcelain.switch(temp_repo, "branch_a")

    # Set up diverged remote ref
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = sibling_sha

    result = runner.invoke(app, ["restack"])

    assert result.exit_code == 1
    assert "diverged" in result.output.lower()


def test_restack_conflict_returns_conflict_branch(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack returns conflict info when rebase fails."""
    monkeypatch.chdir(tmp_path)

    # Create a scenario that will cause a rebase conflict
    # Create branch_a from main with a file
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(conflict_file)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message.encode())

    # Now add a conflicting commit to main
    porcelain.switch(temp_repo, "main")
    conflict_file.write_text("main content - different!")
    porcelain.add(temp_repo, paths=[str(conflict_file)])
    porcelain.commit(temp_repo, message=b"chore: conflicting change on main")

    # Switch back to branch_a
    porcelain.switch(temp_repo, "branch_a")

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
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # Branch A
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    file_a = tmp_path / "file.txt"
    file_a.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Branch B with conflicting content
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    file_a.write_text("branch_b different content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: b").encode())
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # Modify branch_a to create conflict with branch_b
    porcelain.switch(temp_repo, "branch_a")
    file_a.write_text("branch_a modified - will conflict with b")
    porcelain.add(temp_repo, paths=[str(file_a)])
    porcelain.commit(temp_repo, message=b"chore: modify a")

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
    config = temp_repo.get_config()
    config.set((b"user",), b"email", b"test@example.com")
    config.set((b"user",), b"name", b"Test User")
    config.write_to_path()

    # Create branch_a from main with a file
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(conflict_file)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message.encode())

    # Add conflicting commit to main
    porcelain.switch(temp_repo, "main")
    conflict_file.write_text("main content - different!")
    porcelain.add(temp_repo, paths=[str(conflict_file)])
    porcelain.commit(temp_repo, message=b"chore: conflicting change on main")

    # Switch back to branch_a and run restack (will hit conflict)
    porcelain.switch(temp_repo, "branch_a")
    result = runner.invoke(app, ["restack"])
    assert result.exit_code == 1
    assert "conflict" in result.output.lower()

    # Verify rebase is in progress
    assert git.is_rebase_in_progress(temp_repo)

    # Resolve the conflict manually
    conflict_file.write_text("resolved content")
    porcelain.add(temp_repo, paths=[str(conflict_file)])

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
    config = temp_repo.get_config()
    config.set((b"user",), b"email", b"test@example.com")
    config.set((b"user",), b"name", b"Test User")
    config.write_to_path()

    # Create branch_a from main with a file
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(conflict_file)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message.encode())
    original_branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Add conflicting commit to main
    porcelain.switch(temp_repo, "main")
    conflict_file.write_text("main content - different!")
    porcelain.add(temp_repo, paths=[str(conflict_file)])
    porcelain.commit(temp_repo, message=b"chore: conflicting change on main")

    # Switch back to branch_a and run restack (will hit conflict)
    porcelain.switch(temp_repo, "branch_a")
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
    assert temp_repo.refs[b"refs/heads/branch_a"] == original_branch_a_sha


# ============================================================================
# Coverage Tests: Edge Cases
# ============================================================================


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


# ============================================================================
# Coverage Tests: continue_.py edge cases
# ============================================================================


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
        raise RuntimeError("Cherry-pick failed")

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


# ============================================================================
# Coverage Tests: _porcelain_rebase functions
# ============================================================================


def test_porcelain_rebase_control_both_flags() -> None:
    """Test _porcelain_rebase_control raises when both abort and continue."""
    from shortcake._git import RebaseFailure, _porcelain_rebase_control

    with pytest.raises(RebaseFailure, match="Cannot abort and continue"):
        _porcelain_rebase_control(None, abort=True, continue_rebase=True)


def test_porcelain_rebase_start_no_rebase_function(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start raises when dulwich has no rebase."""
    from shortcake._git import RebaseFailure, _porcelain_rebase_start

    # Remove rebase function
    monkeypatch.delattr(porcelain, "rebase", raising=False)

    with pytest.raises(RebaseFailure, match="unavailable"):
        _porcelain_rebase_start(temp_repo, "main", None, None)


def test_porcelain_rebase_control_no_rebase_function(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control raises when dulwich has no rebase."""
    from shortcake._git import RebaseFailure, _porcelain_rebase_control

    # Remove rebase functions
    monkeypatch.delattr(porcelain, "rebase", raising=False)
    monkeypatch.delattr(porcelain, "rebase_abort", raising=False)
    monkeypatch.delattr(porcelain, "rebase_continue", raising=False)

    with pytest.raises(RebaseFailure, match="unavailable"):
        _porcelain_rebase_control(temp_repo, abort=True, continue_rebase=False)


def test_porcelain_rebase_control_uses_rebase_abort(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control uses rebase_abort if available."""
    from shortcake._git import _porcelain_rebase_control

    called = [False]

    def mock_rebase_abort(repo):
        called[0] = True

    monkeypatch.setattr(porcelain, "rebase_abort", mock_rebase_abort, raising=False)

    _porcelain_rebase_control(temp_repo, abort=True, continue_rebase=False)
    assert called[0] is True


def test_porcelain_rebase_control_uses_rebase_continue(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control uses rebase_continue if available."""
    from shortcake._git import _porcelain_rebase_control

    called = [False]

    def mock_rebase_continue(repo):
        called[0] = True

    monkeypatch.setattr(
        porcelain, "rebase_continue", mock_rebase_continue, raising=False
    )

    _porcelain_rebase_control(temp_repo, abort=False, continue_rebase=True)
    assert called[0] is True


def test_porcelain_rebase_start_with_upstream_ref_param(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start with upstream_ref parameter variant."""
    from shortcake._git import _porcelain_rebase_start

    called_with = [None]

    def mock_rebase(repo, **kwargs):
        called_with[0] = kwargs

    # Create a mock with upstream_ref parameter
    import inspect

    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream_ref", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_start(temp_repo, "main", None, None)
    assert called_with[0] == {"upstream_ref": "main"}


def test_porcelain_rebase_start_with_onto_name_param(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start with onto_name parameter variant."""
    from shortcake._git import _porcelain_rebase_start

    called_with = [None]

    def mock_rebase(repo, **kwargs):
        called_with[0] = kwargs

    import inspect

    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.KEYWORD_ONLY),
            inspect.Parameter("onto_name", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_start(temp_repo, "main", "target", None)
    assert called_with[0] == {"upstream": "main", "onto_name": "target"}


def test_porcelain_rebase_start_with_branch_name_param(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start with branch_name parameter variant."""
    from shortcake._git import _porcelain_rebase_start

    called_with = [None]

    def mock_rebase(repo, **kwargs):
        called_with[0] = kwargs

    import inspect

    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.KEYWORD_ONLY),
            inspect.Parameter("branch_name", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_start(temp_repo, "main", None, "feature")
    assert called_with[0] == {"upstream": "main", "branch_name": "feature"}


def test_porcelain_rebase_start_switches_branch(
    repo_with_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start switches branch when needed."""
    from shortcake._git import _porcelain_rebase_start

    # Start on feature branch
    assert git.get_current_branch(repo_with_feature) == "feature"

    called = [False]

    def mock_rebase(repo, **kwargs):
        called[0] = True

    import inspect

    # Signature without branch parameter
    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    # Request rebase on main (different from current)
    _porcelain_rebase_start(repo_with_feature, "feature", None, "main")

    # Should have switched to main
    assert git.get_current_branch(repo_with_feature) == "main"
    assert called[0] is True


def test_porcelain_rebase_start_positional_args(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start with positional argument style (no kwargs)."""
    from shortcake._git import _porcelain_rebase_start

    called_with = [None]

    def mock_rebase(repo, upstream, onto=None, **kwargs):
        called_with[0] = {"upstream": upstream, "onto": onto}

    import inspect

    # Signature with positional-only onto parameter (no upstream/onto keyword)
    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("onto", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_start(temp_repo, "main", "target", None)
    # The function detects 'onto' in params and adds it as keyword arg
    assert called_with[0] == {"upstream": "main", "onto": "target"}


def test_porcelain_rebase_control_abort_param_variants(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control with different abort parameter names."""
    from shortcake._git import _porcelain_rebase_control

    # Remove dedicated functions
    monkeypatch.delattr(porcelain, "rebase_abort", raising=False)
    monkeypatch.delattr(porcelain, "rebase_continue", raising=False)

    called_with = [None]

    def mock_rebase(repo, **kwargs):
        called_with[0] = kwargs

    import inspect

    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("abort_rebase", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_control(temp_repo, abort=True, continue_rebase=False)
    assert called_with[0] == {"abort_rebase": True}


def test_porcelain_rebase_control_continue_param_variants(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control with different continue parameter names."""
    from shortcake._git import _porcelain_rebase_control

    # Remove dedicated functions
    monkeypatch.delattr(porcelain, "rebase_abort", raising=False)
    monkeypatch.delattr(porcelain, "rebase_continue", raising=False)

    called_with = [None]

    def mock_rebase(repo, **kwargs):
        called_with[0] = kwargs

    import inspect

    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("continue_", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_control(temp_repo, abort=False, continue_rebase=True)
    assert called_with[0] == {"continue_": True}


def test_porcelain_rebase_control_no_continue_param(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control raises when no continue param available."""
    from shortcake._git import RebaseFailure, _porcelain_rebase_control

    # Remove dedicated functions
    monkeypatch.delattr(porcelain, "rebase_abort", raising=False)
    monkeypatch.delattr(porcelain, "rebase_continue", raising=False)

    def mock_rebase(repo, **kwargs):
        pass

    import inspect

    # No continue parameter at all
    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    with pytest.raises(RebaseFailure, match="continue is unavailable"):
        _porcelain_rebase_control(temp_repo, abort=False, continue_rebase=True)


def test_porcelain_rebase_control_no_abort_param(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control raises when no abort param available."""
    from shortcake._git import RebaseFailure, _porcelain_rebase_control

    # Remove dedicated functions
    monkeypatch.delattr(porcelain, "rebase_abort", raising=False)
    monkeypatch.delattr(porcelain, "rebase_continue", raising=False)

    def mock_rebase(repo, **kwargs):
        pass

    import inspect

    # No abort parameter at all
    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    with pytest.raises(RebaseFailure, match="abort is unavailable"):
        _porcelain_rebase_control(temp_repo, abort=True, continue_rebase=False)


def test_porcelain_rebase_start_onto_not_supported(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start raises when onto not supported."""
    from shortcake._git import RebaseFailure, _porcelain_rebase_start

    def mock_rebase(repo, **kwargs):
        pass

    import inspect

    # Has upstream but no onto parameter
    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    with pytest.raises(RebaseFailure, match="does not support --onto"):
        _porcelain_rebase_start(temp_repo, "main", "target", None)


def test_porcelain_rebase_control_signature_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control handles signature inspection error."""
    from shortcake._git import RebaseFailure, _porcelain_rebase_control

    # Remove dedicated functions
    monkeypatch.delattr(porcelain, "rebase_abort", raising=False)
    monkeypatch.delattr(porcelain, "rebase_continue", raising=False)

    # Create a callable that raises on signature inspection
    class BadCallable:
        def __call__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(porcelain, "rebase", BadCallable())

    # Should fail to find the right parameters
    with pytest.raises(RebaseFailure):
        _porcelain_rebase_control(temp_repo, abort=True, continue_rebase=False)


def test_porcelain_rebase_start_signature_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start handles signature inspection error."""
    from shortcake._git import _porcelain_rebase_start

    called = [False]

    # Create a callable that fails signature inspection (params will be {})
    class BadCallable:
        def __call__(self, repo, upstream, *args, **kwargs):
            called[0] = True

    monkeypatch.setattr(porcelain, "rebase", BadCallable())

    # Should call without kwargs since params is empty
    _porcelain_rebase_start(temp_repo, "main", None, None)
    assert called[0] is True


def test_porcelain_rebase_start_with_branch_param(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start with branch parameter."""
    from shortcake._git import _porcelain_rebase_start

    called_with = [None]

    def mock_rebase(repo, **kwargs):
        called_with[0] = kwargs

    import inspect

    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.KEYWORD_ONLY),
            inspect.Parameter("branch", inspect.Parameter.KEYWORD_ONLY),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_start(temp_repo, "main", None, "feature")
    assert called_with[0] == {"upstream": "main", "branch": "feature"}


def test_porcelain_rebase_start_positional_no_onto(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start calls positional style without onto."""
    from shortcake._git import _porcelain_rebase_start

    called_with = [None]

    def mock_rebase(repo, upstream, **kwargs):
        called_with[0] = {"upstream": upstream, "kwargs": kwargs}

    import inspect

    # Signature without upstream/onto keywords - positional calling style
    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_start(temp_repo, "main", None, None)
    assert called_with[0] == {"upstream": "main", "kwargs": {}}


def test_porcelain_rebase_start_positional_with_onto(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_start calls positional style with onto."""
    from shortcake._git import _porcelain_rebase_start

    called_with = [None]

    def mock_rebase(repo, upstream, onto, **kwargs):
        called_with[0] = {"upstream": upstream, "onto": onto, "kwargs": kwargs}

    import inspect

    # Signature without upstream keyword but with positional onto
    mock_rebase.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("upstream", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("onto", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ]
    )

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    _porcelain_rebase_start(temp_repo, "main", "target", None)
    # The function detects 'onto' in params and uses keyword style
    assert called_with[0]["upstream"] == "main"
    assert called_with[0]["onto"] == "target"


def test_porcelain_rebase_control_continue_keyword(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _porcelain_rebase_control with 'continue' parameter (Python reserved)."""
    from shortcake._git import _porcelain_rebase_control

    # Remove dedicated functions
    monkeypatch.delattr(porcelain, "rebase_abort", raising=False)
    monkeypatch.delattr(porcelain, "rebase_continue", raising=False)

    called_with = [None]

    def mock_rebase(repo, **kwargs):
        called_with[0] = kwargs

    import inspect

    # Use 'continue' as parameter name (Python reserved word, but valid in signature)
    params = [
        inspect.Parameter("repo", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ]
    # Can't actually add 'continue' as a parameter name in Python
    # Instead test with the actual code path that checks for it

    mock_rebase.__signature__ = inspect.Signature(parameters=params)

    monkeypatch.setattr(porcelain, "rebase", mock_rebase)

    # Will fail because no continue param available
    from shortcake._git import RebaseFailure

    with pytest.raises(RebaseFailure, match="continue is unavailable"):
        _porcelain_rebase_control(temp_repo, abort=False, continue_rebase=True)


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


# Tests for _rebase_onto_remote


def test_rebase_onto_remote_no_remote_ref(repo_with_stack: Repo) -> None:
    """Test _rebase_onto_remote returns failure when no remote ref exists."""
    result = _rebase_onto_remote(repo_with_stack, "branch_a")

    assert not result.success
    assert "No remote tracking branch" in result.error_output


def test_rebase_onto_remote_no_common_ancestor(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test _rebase_onto_remote returns failure when no common ancestor."""
    # Create an orphan branch for origin/branch_a with no common history
    # First create a new disconnected commit
    orphan_file = tmp_path / "orphan.txt"
    orphan_file.write_text("orphan content")

    # Create orphan branch manually by creating a commit with no parents
    from dulwich.objects import Blob, Commit, Tree

    blob = Blob.from_string(b"orphan content")
    repo_with_stack.object_store.add_object(blob)

    tree = Tree()
    tree.add(b"orphan.txt", 0o100644, blob.id)
    repo_with_stack.object_store.add_object(tree)

    commit = Commit()
    commit.tree = tree.id
    commit.author = commit.committer = b"Test <test@test.com>"
    commit.commit_time = commit.author_time = 1234567890
    commit.commit_timezone = commit.author_timezone = 0
    commit.message = b"Orphan commit"
    commit.encoding = b"UTF-8"
    repo_with_stack.object_store.add_object(commit)

    # Set origin/branch_a to this orphan commit
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = commit.id

    result = _rebase_onto_remote(repo_with_stack, "branch_a")

    assert not result.success
    assert "No common ancestor" in result.error_output


def test_rebase_onto_remote_success(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test _rebase_onto_remote successfully rebases diverged branch."""
    # Create a new commit on main that will be the "remote" version
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

    remote_file = tmp_path / "remote.txt"
    remote_file.write_text("remote content")
    porcelain.add(repo_with_stack, paths=[str(remote_file)])
    remote_sha = porcelain.commit(repo_with_stack, message=b"Remote commit")

    # Set origin/branch_a to this new commit (simulating remote has advanced)
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = remote_sha

    # Switch back to branch_a
    repo_with_stack.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    result = _rebase_onto_remote(repo_with_stack, "branch_a")

    assert result.success


def test_rebase_onto_remote_with_exception(
    repo_with_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _rebase_onto_remote handles exceptions gracefully."""
    # Set up a remote ref
    branch_a_sha = git.get_branch_head(repo_with_stack, "branch_a")
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = branch_a_sha

    # Mock rebase_branch to raise an exception
    def mock_rebase_branch(*args, **kwargs):
        raise RuntimeError("Rebase failed")

    monkeypatch.setattr(git, "rebase_branch", mock_rebase_branch)

    result = _rebase_onto_remote(repo_with_stack, "branch_a")

    assert not result.success
    assert "Rebase failed" in result.error_output


# Tests for auto-rebase with sync=True


def test_restack_sync_auto_rebases_diverged_branch(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack --sync auto-rebases diverged branches."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a with a commit
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    branch_a_file = tmp_path / "branch_a.txt"
    branch_a_file.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(branch_a_file)])
    porcelain.commit(
        temp_repo,
        message=b"feat: branch_a\n\nShortcake-Parent: main",
    )

    # Create a commit on main that will be the "remote" version
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    remote_file = tmp_path / "remote.txt"
    remote_file.write_text("remote content")
    porcelain.add(temp_repo, paths=[str(remote_file)])
    remote_sha = porcelain.commit(temp_repo, message=b"Remote commit")

    # Set origin/branch_a to point to main's new commit
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = remote_sha

    # Switch back to branch_a
    porcelain.switch(temp_repo, "branch_a")

    # Mock fetch to do nothing
    def mock_fetch(*args, **kwargs):
        pass

    monkeypatch.setattr(porcelain, "fetch", mock_fetch)

    result = runner.invoke(app, ["restack", "--sync"])

    # Should succeed and show rebasing message
    assert result.exit_code == 0, f"Output: {result.output}"
    assert "Rebasing 'branch_a' onto 'origin/branch_a'" in result.output


def test_restack_sync_auto_rebase_with_failure(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack --sync shows error when auto-rebase fails."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a with a commit
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    branch_a_file = tmp_path / "branch_a.txt"
    branch_a_file.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(branch_a_file)])
    porcelain.commit(
        temp_repo,
        message=b"feat: branch_a\n\nShortcake-Parent: main",
    )

    # Create a sibling branch from main for the "remote" commit (divergence)
    temp_repo.refs[b"refs/heads/sibling"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/sibling")

    sibling_file = tmp_path / "sibling.txt"
    sibling_file.write_text("sibling content")
    porcelain.add(temp_repo, paths=[str(sibling_file)])
    sibling_sha = porcelain.commit(temp_repo, message=b"Sibling commit on remote")

    # Set origin/branch_a to sibling (creating true divergence)
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = sibling_sha

    # Switch back to branch_a
    porcelain.switch(temp_repo, "branch_a")

    # Mock fetch to do nothing
    def mock_fetch(*args, **kwargs):
        pass

    monkeypatch.setattr(porcelain, "fetch", mock_fetch)

    # Mock rebase to fail
    def mock_rebase_branch(*args, **kwargs):
        raise RuntimeError("Rebase failed")

    monkeypatch.setattr(git, "rebase_branch", mock_rebase_branch)

    result = runner.invoke(app, ["restack", "--sync"])

    assert result.exit_code == 1
    assert "Failed to rebase" in result.output or "Cannot rebase" in result.output


def test_restack_sync_auto_rebase_with_conflict(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack --sync shows conflict message when auto-rebase hits conflict."""
    monkeypatch.chdir(tmp_path)

    # Create branch_a with a commit
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    branch_a_file = tmp_path / "branch_a.txt"
    branch_a_file.write_text("branch_a content")
    porcelain.add(temp_repo, paths=[str(branch_a_file)])
    porcelain.commit(
        temp_repo,
        message=b"feat: branch_a\n\nShortcake-Parent: main",
    )

    # Create a sibling branch from main for the "remote" commit (divergence)
    temp_repo.refs[b"refs/heads/sibling"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/sibling")

    sibling_file = tmp_path / "sibling.txt"
    sibling_file.write_text("sibling content")
    porcelain.add(temp_repo, paths=[str(sibling_file)])
    sibling_sha = porcelain.commit(temp_repo, message=b"Sibling commit on remote")

    # Set origin/branch_a to sibling (creating true divergence)
    temp_repo.refs[b"refs/remotes/origin/branch_a"] = sibling_sha

    # Switch back to branch_a
    porcelain.switch(temp_repo, "branch_a")

    # Mock fetch to do nothing
    def mock_fetch(*args, **kwargs):
        pass

    monkeypatch.setattr(porcelain, "fetch", mock_fetch)

    # Track call count for is_rebase_in_progress
    # First call (precondition check) should return False
    # Second call (after failed rebase) should return True
    call_count = [0]

    def mock_is_rebase_in_progress(repo):
        call_count[0] += 1
        # First call is the precondition check, return False
        # Second call is after failed rebase, return True (conflict state)
        return call_count[0] > 1

    monkeypatch.setattr(git, "is_rebase_in_progress", mock_is_rebase_in_progress)

    # Mock rebase to fail
    def mock_rebase_branch(*args, **kwargs):
        raise RuntimeError("Conflict during rebase")

    monkeypatch.setattr(git, "rebase_branch", mock_rebase_branch)

    # Mock _get_conflict_files
    import shortcake.commands.restack as restack_module

    monkeypatch.setattr(
        restack_module, "_get_conflict_files", lambda repo: ["file.txt"]
    )

    result = runner.invoke(app, ["restack", "--sync"])

    assert result.exit_code == 1
    assert "Conflict" in result.output

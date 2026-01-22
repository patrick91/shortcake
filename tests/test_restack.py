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
from shortcake.commands.abort import AbortError, _abort
from shortcake.commands.continue_ import ContinueError, _continue, _continue_rebase
from shortcake.commands.restack import (
    RestackError,
    _check_remote_divergence,
    _fast_forward_branch,
    _fetch_remote,
    _get_conflict_files,
    _get_stack_in_order,
    _needs_restack,
    _plan_restack,
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


def test_check_remote_divergence_no_remote(repo_with_stack: Repo) -> None:
    """Test divergence check with no remote refs."""
    branches = ["branch_a", "branch_b"]
    diverged = _check_remote_divergence(repo_with_stack, branches)
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
    repo_with_stack_behind: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test continue when state exists but no rebase in progress."""
    monkeypatch.chdir(tmp_path)

    # Create state as if restack completed current step
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_b",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
        ],
        current_index=0,  # Already at last item
        original_refs={
            "branch_a": git.get_branch_head(
                repo_with_stack_behind, "branch_a"
            ).decode(),
        },
    )
    state.save(repo_with_stack_behind)

    # Continue should complete (no more branches to rebase)
    result = runner.invoke(app, ["continue"])

    assert result.exit_code == 0
    assert "completed" in result.output.lower()


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
    # When no rebase is in progress, dulwich returns success (no-op).
    # This is fine since _continue_rebase is only called after
    # checking is_rebase_in_progress.
    result = _continue_rebase(temp_repo)
    assert result is True


def test_continue_rebase_function_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _continue_rebase returns False when dulwich raises an error."""
    from dulwich.porcelain import Error as DulwichError

    def mock_rebase(*args, **kwargs):
        raise DulwichError("Conflict during rebase")

    monkeypatch.setattr("dulwich.porcelain.rebase", mock_rebase)
    result = _continue_rebase(temp_repo)
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


def test_check_remote_divergence_with_diverged_branch(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test divergence detection when branch has diverged from remote."""
    # Create a different commit to use as "remote" state
    main_sha = git.get_branch_head(repo_with_stack, "main")

    # Set up origin/branch_a pointing to a different commit (main)
    # This simulates divergence where local and remote have different commits
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = main_sha

    diverged = _check_remote_divergence(repo_with_stack, ["branch_a"])

    # branch_a should be detected as diverged since local != remote
    # and local is not an ancestor of remote
    assert "branch_a" in diverged


def test_restack_with_diverged_branches(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test restack fails with diverged branches."""
    monkeypatch.chdir(tmp_path)

    # Set up diverged remote ref
    main_sha = git.get_branch_head(repo_with_stack, "main")
    repo_with_stack.refs[b"refs/remotes/origin/branch_a"] = main_sha

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

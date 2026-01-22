from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake._trailers import Trailers
from shortcake.commands.abort import AbortError, _abort
from shortcake.commands.continue_ import ContinueError, _continue
from shortcake.commands.restack import (
    RestackError,
    _get_stack_in_order,
    _needs_restack,
    _plan_restack,
    _restack,
)

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

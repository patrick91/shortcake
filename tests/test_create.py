import stat
from pathlib import Path

import pytest

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.adopt import _adopt
from shortcake.commands.create import (
    BranchExistsError,
    EmptyBranchNameError,
    InsertError,
    _create,
    _create_insert_after,
    _create_insert_before,
    _slugify,
    _validate_branch_name,
)
from shortcake.commands.ls import _ls
from tests._git_helpers import Repo, add_paths, get_ref, run_git, set_ref, switch_branch

# Slugify tests


def test_slugify_simple() -> None:
    """Test basic message slugification."""
    assert _slugify("Add user model") == "add-user-model"


def test_slugify_conventional_commit() -> None:
    """Test handling conventional commit format."""
    assert _slugify("feat: add login form") == "feat-add-login-form"


def test_slugify_with_scope() -> None:
    """Test handling conventional commit with scope."""
    assert _slugify("fix(auth): token refresh") == "fix-auth-token-refresh"


def test_slugify_special_chars() -> None:
    """Test handling special characters."""
    assert _slugify("WIP: testing stuff!") == "wip-testing-stuff"


def test_slugify_multiline() -> None:
    """Test uses first line only."""
    assert _slugify("First line\n\nBody text here") == "first-line"


def test_slugify_max_length() -> None:
    """Test truncation at 50 characters."""
    long_message = "a" * 100
    assert len(_slugify(long_message)) == 50


def test_slugify_strips_leading_trailing_hyphens() -> None:
    """Test stripping leading/trailing hyphens."""
    assert _slugify("---test---") == "test"


def test_slugify_truncation_strips_trailing_hyphen() -> None:
    """Test that truncation doesn't leave a trailing hyphen."""
    # This message produces a slug longer than 50 chars with a hyphen at position 50
    result = _slugify("Add GitHub accounts endpoint and base integration infra")
    assert not result.endswith("-")
    assert result == "add-github-accounts-endpoint-and-base-integration"


def test_slugify_gitmoji() -> None:
    """Test handling emoji prefix."""
    assert _slugify("✨ add new feature") == "add-new-feature"


# Create tests


def test_create_from_main(temp_repo: Repo) -> None:
    """Test basic branch creation from main."""
    message = "feat: add login form"
    branch_name = _slugify(message)
    result = _create(temp_repo, message, branch_name)

    assert result.branch == "feat-add-login-form"
    assert result.parent == "main"
    assert result.message == "feat: add login form"

    # Verify we're on the new branch
    assert git.get_current_branch(temp_repo) == "feat-add-login-form"

    # Verify commit has trailer
    head = git.get_branch_head(temp_repo, "feat-add-login-form")
    commit_message = git.get_commit_message(temp_repo, head)
    assert Trailers.from_message(commit_message).parent_branch == "main"


def test_create_from_feature(repo_with_feature: Repo) -> None:
    """Test stacking - creating from a tracked branch."""
    _adopt(repo_with_feature)

    message = "feat: add validation"
    branch_name = _slugify(message)
    result = _create(repo_with_feature, message, branch_name)

    assert result.branch == "feat-add-validation"
    assert result.parent == "feature"

    head = git.get_branch_head(repo_with_feature, "feat-add-validation")
    commit_message = git.get_commit_message(repo_with_feature, head)
    assert Trailers.from_message(commit_message).parent_branch == "feature"


def test_create_with_staged_changes(temp_repo: Repo, tmp_path: Path) -> None:
    """Test that staged changes are committed."""
    new_file = tmp_path / "new_feature.py"
    new_file.write_text("print('hello')")
    add_paths(temp_repo, new_file)

    message = "feat: add feature file"
    branch_name = _slugify(message)
    result = _create(temp_repo, message, branch_name)

    head = git.get_branch_head(temp_repo, result.branch)
    commit = temp_repo.get(head.decode() if isinstance(head, bytes) else str(head))
    tree = temp_repo.get(str(commit.tree_id))
    assert b"new_feature.py" in [entry.name.encode() for entry in tree]


def test_create_only_commits_staged_changes(temp_repo: Repo, tmp_path: Path) -> None:
    """Test that only staged changes are committed, unstaged changes remain."""
    # Create and stage one file
    staged_file = tmp_path / "staged.py"
    staged_file.write_text("print('staged')")
    add_paths(temp_repo, staged_file)

    # Create another file but don't stage it
    unstaged_file = tmp_path / "unstaged.py"
    unstaged_file.write_text("print('unstaged')")

    message = "feat: add staged file only"
    branch_name = _slugify(message)
    result = _create(temp_repo, message, branch_name)

    # Verify staged file is in commit
    head = git.get_branch_head(temp_repo, result.branch)
    commit = temp_repo.get(head.decode() if isinstance(head, bytes) else str(head))
    tree = temp_repo.get(str(commit.tree_id))
    committed_files = [entry.name.encode() for entry in tree]
    assert b"staged.py" in committed_files
    assert b"unstaged.py" not in committed_files

    # Verify unstaged file still exists in working directory
    assert unstaged_file.exists()
    assert unstaged_file.read_text() == "print('unstaged')"


def test_create_empty_commit(temp_repo: Repo) -> None:
    """Test creating with no staged changes creates empty commit."""
    message = "feat: start feature"
    branch_name = _slugify(message)
    result = _create(temp_repo, message, branch_name)

    assert result.branch == "feat-start-feature"
    head = git.get_branch_head(temp_repo, result.branch)
    commit_message = git.get_commit_message(temp_repo, head)
    assert "feat: start feature" in commit_message


def test_create_branch_exists(temp_repo: Repo) -> None:
    """Test error when branch already exists."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feat-existing", main_sha)

    with pytest.raises(BranchExistsError) as exc_info:
        _validate_branch_name(temp_repo, "feat-existing")
    assert exc_info.value.branch == "feat-existing"


def test_create_detached_head_asserts(temp_repo: Repo) -> None:
    """Test that _create asserts if called in detached HEAD state."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    with pytest.raises(AssertionError):
        _create(temp_repo, "feat: something", "feat-something")


def test_validate_empty_slug(temp_repo: Repo) -> None:
    """Test error when branch name is empty."""
    with pytest.raises(EmptyBranchNameError, match="Cannot generate branch name"):
        _validate_branch_name(temp_repo, "")


def test_create_with_explicit_branch_name(temp_repo: Repo) -> None:
    """Test creating with explicit branch name when slug would be empty."""
    result = _create(temp_repo, "...", "my-branch")

    assert result.branch == "my-branch"
    assert result.parent == "main"


# Pre-commit hook tests


def test_precommit_hook_passes(temp_repo: Repo, tmp_path: Path) -> None:
    """Test pre-commit hook that passes."""
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 0\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    add_paths(temp_repo, new_file)

    success, error = git.run_precommit_hook(temp_repo)
    assert success is True
    assert error is None


def test_precommit_hook_fails(temp_repo: Repo, tmp_path: Path) -> None:
    """Test pre-commit hook that fails."""
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'Hook failed!'\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    add_paths(temp_repo, new_file)

    success, error = git.run_precommit_hook(temp_repo)
    assert success is False
    assert error is not None


def test_has_precommit_hook_exists(temp_repo: Repo) -> None:
    """Test detection of existing pre-commit hook."""
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 0\n")

    assert git.has_precommit_hook(temp_repo) is True


def test_has_precommit_hook_missing(temp_repo: Repo) -> None:
    """Test detection when no pre-commit hook."""
    assert git.has_precommit_hook(temp_repo) is False


def test_run_precommit_hook_missing(temp_repo: Repo) -> None:
    """Test running pre-commit hook when it doesn't exist."""
    # No hook exists - should return success
    success, error = git.run_precommit_hook(temp_repo)
    assert success is True
    assert error is None


def test_run_precommit_hook_preserves_partial_staging(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Running hooks must not promote unstaged hunks into the index."""
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 0\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    tracked_file = tmp_path / "README.md"
    tracked_file.write_text("# Test\nstaged change\n")
    add_paths(temp_repo, tracked_file)
    tracked_file.write_text("# Test\nstaged change\nunstaged change\n")

    success, error = git.run_precommit_hook(temp_repo)

    assert success is True
    assert error is None
    assert run_git(temp_repo, "show", ":README.md").stdout == "# Test\nstaged change\n"
    assert tracked_file.read_text() == "# Test\nstaged change\nunstaged change\n"


def test_run_precommit_hook_exception(temp_repo: Repo, tmp_path: Path) -> None:
    """Test pre-commit hook when subprocess raises an exception."""
    from unittest.mock import patch

    # Create a hook file so we get past the existence check
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 0\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    with patch(
        "shortcake._git._core.subprocess.Popen",
        side_effect=OSError("Permission denied"),
    ):
        success, error = git.run_precommit_hook(temp_repo)

    assert success is False
    assert error is not None
    assert "Permission denied" in error


# Integration tests


def test_create_shows_in_ls(temp_repo: Repo) -> None:
    """Test that newly created branch shows in sc ls."""
    message = "feat: new feature"
    branch_name = _slugify(message)
    _create(temp_repo, message, branch_name)

    result = _ls(temp_repo)

    assert "feat-new-feature" in result
    assert "main" in result


# Insert-before tests


def test_create_insert_before_basic(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test inserting a branch before current branch in the stack.

    Stack: main → branch_a → branch_b
    On branch_b, insert before → main → branch_a → NEW → branch_b
    """
    # Switch to branch_b (it's already on branch_b in the fixture)
    result = _create_insert_before(repo_with_stack, "fix: inserted", "fix-inserted")

    assert result.branch == "fix-inserted"
    assert result.parent == "branch_a"
    assert result.inserted_before == "branch_b"
    assert result.rebased_branches == ["branch_b"]
    assert result.conflict_branch is None

    # Verify we're on the new branch
    assert git.get_current_branch(repo_with_stack) == "fix-inserted"

    # Verify new branch's trailer points to branch_a
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    new_parent = git.get_branch_parent(repo_with_stack, "fix-inserted", all_branches)
    assert new_parent == "branch_a"

    # Verify branch_b's trailer now points to fix-inserted
    branch_b_parent = git.get_branch_parent(repo_with_stack, "branch_b", all_branches)
    assert branch_b_parent == "fix-inserted"


def test_create_insert_before_bottom(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test inserting before the first tracked branch.

    Stack: main → branch_a → branch_b
    On branch_a, insert before → main → NEW → branch_a → branch_b
    """
    switch_branch(repo_with_stack, "branch_a")

    result = _create_insert_before(repo_with_stack, "fix: bottom", "fix-bottom")

    assert result.branch == "fix-bottom"
    assert result.parent == "main"
    assert result.inserted_before == "branch_a"
    assert result.rebased_branches == ["branch_a"]

    # Verify new branch's trailer points to main
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    new_parent = git.get_branch_parent(repo_with_stack, "fix-bottom", all_branches)
    assert new_parent == "main"

    # Verify branch_a's trailer now points to fix-bottom
    branch_a_parent = git.get_branch_parent(repo_with_stack, "branch_a", all_branches)
    assert branch_a_parent == "fix-bottom"


def test_create_insert_before_untracked_error(temp_repo: Repo) -> None:
    """Test error when trying to insert before an untracked branch."""
    # main is not tracked (no Shortcake-Parent trailer)
    with pytest.raises(InsertError, match="not tracked"):
        _create_insert_before(temp_repo, "fix: something", "fix-something")


# Insert-after tests


def test_create_insert_after_basic(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test inserting a branch after current branch in the stack.

    Stack: main → branch_a → branch_b
    On branch_a, insert after → main → branch_a → NEW → branch_b
    """
    switch_branch(repo_with_stack, "branch_a")

    result = _create_insert_after(repo_with_stack, "fix: after-a", "fix-after-a")

    assert result.branch == "fix-after-a"
    assert result.parent == "branch_a"
    assert result.inserted_after == "branch_a"
    assert result.rebased_branches == ["branch_b"]
    assert result.conflict_branch is None

    # Verify we're on the new branch
    assert git.get_current_branch(repo_with_stack) == "fix-after-a"

    # Verify new branch's trailer points to branch_a
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    new_parent = git.get_branch_parent(repo_with_stack, "fix-after-a", all_branches)
    assert new_parent == "branch_a"

    # Verify branch_b's trailer now points to fix-after-a
    branch_b_parent = git.get_branch_parent(repo_with_stack, "branch_b", all_branches)
    assert branch_b_parent == "fix-after-a"


def test_create_insert_after_no_children(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test insert-after with no children is like normal create.

    Stack: main → branch_a → branch_b
    On branch_b (leaf), insert after → main → branch_a → branch_b → NEW
    """
    # branch_b is the leaf, already checked out
    result = _create_insert_after(repo_with_stack, "fix: leaf", "fix-leaf")

    assert result.branch == "fix-leaf"
    assert result.parent == "branch_b"
    assert result.inserted_after == "branch_b"
    assert result.rebased_branches == []
    assert result.conflict_branch is None

    # Verify new branch's trailer points to branch_b
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    new_parent = git.get_branch_parent(repo_with_stack, "fix-leaf", all_branches)
    assert new_parent == "branch_b"


def test_create_insert_after_multiple_children_error(
    repo_with_fork: Repo,
) -> None:
    """Test error when inserting after a branch with multiple children."""
    # repo_with_fork: main → branch_a → (branch_b, branch_c)
    switch_branch(repo_with_fork, "branch_a")

    with pytest.raises(InsertError, match="multiple children"):
        _create_insert_after(repo_with_fork, "fix: something", "fix-something")


def test_create_insert_with_allow_empty(repo_with_stack: Repo) -> None:
    """Test that insert-before works with empty commits (no staged changes)."""
    # No staged changes, but _create_insert_before creates an empty commit
    result = _create_insert_before(repo_with_stack, "fix: empty", "fix-empty")

    assert result.branch == "fix-empty"
    assert result.inserted_before == "branch_b"


def test_create_insert_before_with_staged_changes(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test that insert-before works when there are staged changes.

    Staged changes are committed temporarily, cherry-picked onto the new
    branch, then the temp commit is dropped.
    """
    # Stage a new file while on branch_b
    new_file = tmp_path / "new_feature.py"
    new_file.write_text("print('hello')")
    add_paths(repo_with_stack, new_file)

    result = _create_insert_before(
        repo_with_stack, "fix: with-staged", "fix-with-staged"
    )

    assert result.branch == "fix-with-staged"
    assert result.parent == "branch_a"
    assert result.inserted_before == "branch_b"
    assert result.rebased_branches == ["branch_b"]

    # Verify the staged file is in the new branch's commit
    head = git.get_branch_head(repo_with_stack, "fix-with-staged")
    commit = repo_with_stack.get(
        head.decode() if isinstance(head, bytes) else str(head)
    )
    tree = repo_with_stack.get(str(commit.tree_id))
    assert b"new_feature.py" in [entry.name.encode() for entry in tree]


def test_create_insert_before_staged_changes_on_shared_file(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    """Test insert-before with staged changes to a file that differs between branches.

    b.txt exists on branch_b but not on branch_a. Modifying it and inserting
    before should still work because we copy exact file contents (no 3-way merge).
    """
    b_file = tmp_path / "b.txt"
    b_file.write_text("modified b content")
    add_paths(repo_with_stack, b_file)

    result = _create_insert_before(
        repo_with_stack, "fix: shared-file", "fix-shared-file"
    )

    assert result.branch == "fix-shared-file"
    assert result.parent == "branch_a"

    # Verify the modified file is in the new branch's commit
    head = git.get_branch_head(repo_with_stack, "fix-shared-file")
    commit = repo_with_stack.get(
        head.decode() if isinstance(head, bytes) else str(head)
    )
    tree = repo_with_stack.get(str(commit.tree_id))
    assert b"b.txt" in [entry.name.encode() for entry in tree]


def test_create_insert_before_conflict(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test insert-before returns conflict result when rebase fails."""
    from unittest.mock import patch

    mock_rebase_result = git.RebaseResult(success=False, conflict=True)

    with (
        patch(
            "shortcake.commands.restack._rebase_branch",
            return_value=mock_rebase_result,
        ),
        patch("shortcake._git.is_rebase_in_progress", return_value=True),
    ):
        result = _create_insert_before(repo_with_stack, "fix: conflict", "fix-conflict")

    assert result.branch == "fix-conflict"
    assert result.conflict_branch == "branch_b"
    assert result.inserted_before == "branch_b"


def test_create_insert_after_conflict(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Test insert-after returns conflict result when rebase fails."""
    from unittest.mock import patch

    switch_branch(repo_with_stack, "branch_a")

    mock_rebase_result = git.RebaseResult(success=False, conflict=True)

    with (
        patch(
            "shortcake.commands.restack._rebase_branch",
            return_value=mock_rebase_result,
        ),
        patch("shortcake._git.is_rebase_in_progress", return_value=True),
    ):
        result = _create_insert_after(repo_with_stack, "fix: conflict", "fix-conflict")

    assert result.branch == "fix-conflict"
    assert result.conflict_branch == "branch_b"
    assert result.inserted_after == "branch_a"

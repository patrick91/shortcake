import stat
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.adopt import _adopt
from shortcake.commands.create import (
    BranchExistsError,
    EmptyBranchNameError,
    _create,
    _slugify,
    _validate_branch_name,
)
from shortcake.commands.ls import _ls

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
    porcelain.add(temp_repo, paths=[str(new_file)])

    message = "feat: add feature file"
    branch_name = _slugify(message)
    result = _create(temp_repo, message, branch_name)

    head = git.get_branch_head(temp_repo, result.branch)
    commit = temp_repo[head]
    tree = temp_repo[commit.tree]
    assert b"new_feature.py" in [entry.path for entry in tree.items()]


def test_create_only_commits_staged_changes(temp_repo: Repo, tmp_path: Path) -> None:
    """Test that only staged changes are committed, unstaged changes remain."""
    # Create and stage one file
    staged_file = tmp_path / "staged.py"
    staged_file.write_text("print('staged')")
    porcelain.add(temp_repo, paths=[str(staged_file)])

    # Create another file but don't stage it
    unstaged_file = tmp_path / "unstaged.py"
    unstaged_file.write_text("print('unstaged')")

    message = "feat: add staged file only"
    branch_name = _slugify(message)
    result = _create(temp_repo, message, branch_name)

    # Verify staged file is in commit
    head = git.get_branch_head(temp_repo, result.branch)
    commit = temp_repo[head]
    tree = temp_repo[commit.tree]
    committed_files = [entry.path for entry in tree.items()]
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
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feat-existing"] = main_sha

    with pytest.raises(BranchExistsError) as exc_info:
        _validate_branch_name(temp_repo, "feat-existing")
    assert exc_info.value.branch == "feat-existing"


def test_create_detached_head_asserts(temp_repo: Repo) -> None:
    """Test that _create asserts if called in detached HEAD state."""
    main_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = main_sha

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
    hooks_dir = Path(temp_repo.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 0\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(temp_repo, paths=[str(new_file)])

    success, error = git.run_precommit_hook(temp_repo)
    assert success is True
    assert error is None


def test_precommit_hook_fails(temp_repo: Repo, tmp_path: Path) -> None:
    """Test pre-commit hook that fails."""
    hooks_dir = Path(temp_repo.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'Hook failed!'\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(temp_repo, paths=[str(new_file)])

    success, error = git.run_precommit_hook(temp_repo)
    assert success is False
    assert "Hook failed!" in (error or "")


def test_has_precommit_hook_exists(temp_repo: Repo) -> None:
    """Test detection of existing pre-commit hook."""
    hooks_dir = Path(temp_repo.controldir()) / "hooks"
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


def test_run_precommit_hook_exception(temp_repo: Repo, tmp_path: Path) -> None:
    """Test pre-commit hook when subprocess raises an exception."""
    from unittest.mock import patch

    # Create a hook file so we get past the existence check
    hooks_dir = Path(temp_repo.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 0\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Mock subprocess.run to raise an exception
    with patch("shortcake._git._core.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("Permission denied")
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

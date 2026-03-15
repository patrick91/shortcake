from pathlib import Path

import pytest

from shortcake.commands.log import LogResult, _log, _render_log
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_branch_head,
    get_ref,
    init_repo,
    set_ref,
    update_branch,
)


def test_log_tracked_branch(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test log on a tracked branch shows commits between parent and HEAD."""
    from shortcake.commands.adopt import _adopt

    # Adopt the branch to track it
    _adopt(repo_with_feature)

    result = _log(repo_with_feature)

    assert result.branch == "feature"
    assert result.parent == "main"
    assert len(result.commits) == 1
    assert result.commits[0][1] == "Add feature"


def test_log_untracked_branch(repo_with_feature: Repo) -> None:
    """Test log on untracked branch shows commits to default branch."""
    # Don't adopt - branch is untracked
    result = _log(repo_with_feature)

    assert result.branch == "feature"
    assert result.parent is None  # Untracked
    assert len(result.commits) == 1
    assert result.commits[0][1] == "Add feature"


def test_log_multiple_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test log shows multiple commits in order."""
    from shortcake.commands.adopt import _adopt

    # Create feature branch with multiple commits
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )

    # First commit
    commit_files(temp_repo, {tmp_path / "file1.txt": "content1"}, "First commit")

    # Second commit
    commit_files(temp_repo, {tmp_path / "file2.txt": "content2"}, "Second commit")

    # Adopt and log
    _adopt(temp_repo)
    result = _log(temp_repo)

    assert result.branch == "feature"
    assert result.parent == "main"
    assert len(result.commits) == 2
    # Commits are newest first
    assert result.commits[0][1] == "Second commit"
    assert result.commits[1][1].startswith("First commit")


def test_log_no_commits_at_parent(temp_repo: Repo, tmp_path: Path) -> None:
    """Test log when parent has caught up to branch shows no commits."""
    from shortcake.commands.adopt import _adopt

    # Create feature branch with one commit
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )

    commit_files(temp_repo, {tmp_path / "file1.txt": "content"}, "Feature commit")

    # Adopt the feature branch
    _adopt(temp_repo)

    # Now fast-forward main to feature's head (simulating merge)
    # When main catches up, the trailer commit is now on main too,
    # so get_branch_parent returns None (stops at main's head)
    feature_sha = get_branch_head(temp_repo, "feature")
    update_branch(temp_repo, "main", feature_sha)

    result = _log(temp_repo)

    assert result.branch == "feature"
    # Parent is None because the commit is now on main too
    assert result.parent is None
    # No commits because feature and main are at same point
    assert len(result.commits) == 0


def test_log_detached_head(temp_repo: Repo) -> None:
    """Test log error in detached HEAD state."""
    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    with pytest.raises(ValueError, match="detached HEAD"):
        _log(temp_repo)


def test_log_untracked_no_default_branch(tmp_path: Path) -> None:
    """Test log on untracked branch with no default shows just HEAD."""
    # Create repo without main or master
    repo = init_repo(tmp_path, default_branch="develop")
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    result = _log(repo)

    assert result.branch == "develop"
    assert result.parent is None
    assert len(result.commits) == 1
    assert result.commits[0][1] == "Initial commit"


def test_log_short_sha_format(repo_with_feature: Repo) -> None:
    """Test log returns 7-char short SHA."""
    result = _log(repo_with_feature)

    assert len(result.commits) == 1
    short_sha = result.commits[0][0]
    assert len(short_sha) == 7
    # Should be hex characters
    assert all(c in "0123456789abcdef" for c in short_sha)


def test_log_message_first_line_only(temp_repo: Repo, tmp_path: Path) -> None:
    """Test log only shows first line of multi-line commit messages."""
    # Create feature branch
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )

    # Commit with multi-line message
    commit_files(
        temp_repo,
        {tmp_path / "file.txt": "content"},
        "First line\n\nBody paragraph\n\nMore text\n",
    )

    result = _log(temp_repo)

    assert len(result.commits) == 1
    assert result.commits[0][1] == "First line"


def test_render_log_with_parent() -> None:
    """Test render log with tracked branch shows tree with parent."""
    result = LogResult(
        commits=[("abc1234", "First commit"), ("def5678", "Second commit")],
        branch="feature",
        parent="main",
    )

    output = _render_log(result)

    assert "◉ feature" in output
    assert "● abc1234 First commit" in output
    assert "● def5678 Second commit" in output
    assert "◯ main" in output
    assert "│" in output


def test_render_log_without_parent() -> None:
    """Test render log without parent omits parent line."""
    result = LogResult(
        commits=[("abc1234", "First commit")],
        branch="feature",
        parent=None,
    )

    output = _render_log(result)

    assert "◉ feature" in output
    assert "● abc1234 First commit" in output
    # Should not end with a pipe when no parent
    assert not output.endswith("│")

from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.adopt import AdoptError, _adopt
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_branch_head,
    init_repo,
    switch_branch,
)

runner = CliRunner()


def test_adopt_current_branch(repo_with_feature: Repo) -> None:
    """Test adopting the current feature branch."""
    result = _adopt(repo_with_feature)

    assert result.branch == "feature"
    assert result.parent == "main"

    # Verify trailer was added
    head = git.get_branch_head(repo_with_feature, "feature")
    message = git.get_commit_message(repo_with_feature, head)
    assert Trailers.from_message(message).parent_branch == "main"


def test_adopt_specified_branch(repo_with_feature: Repo) -> None:
    """Test adopting a specified branch."""
    # Switch back to main
    switch_branch(repo_with_feature, "main")

    result = _adopt(repo_with_feature, branch="feature")

    assert result.branch == "feature"


def test_adopt_with_explicit_parent(repo_with_feature: Repo) -> None:
    """Test adopting with explicit parent."""
    result = _adopt(repo_with_feature, parent="main")

    assert result.branch == "feature"
    assert result.parent == "main"


def test_adopt_already_tracked(repo_with_feature: Repo) -> None:
    """Test error when branch already tracked."""
    # First adopt
    _adopt(repo_with_feature)

    # Try again - should fail with hint to use --force
    with pytest.raises(AdoptError, match=r"already tracked.*--force"):
        _adopt(repo_with_feature)


def test_adopt_force_reparent(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test re-parenting with --force flag."""
    # First adopt with main as parent
    result = _adopt(repo_with_feature)
    assert result.parent == "main"

    # Create a new branch to use as parent
    create_branch(
        repo_with_feature,
        "develop",
        get_branch_head(repo_with_feature, "main"),
    )

    # Re-parent to develop with --force
    result = _adopt(repo_with_feature, parent="develop", force=True)
    assert result.parent == "develop"

    # Verify the trailer was updated
    from shortcake._git._stack import get_branch_parent

    all_branches = {"feature", "main", "develop"}
    parent = get_branch_parent(repo_with_feature, "feature", all_branches)
    assert parent == "develop"


def test_adopt_force_reparent_diverged_lineage(temp_repo: Repo, tmp_path: Path) -> None:
    """Test --force when new parent diverges earlier in history.

    Reproduces a bug where re-parenting to a branch with a different
    lineage caused adopt to amend the wrong commit (an ancestor's
    trailer instead of the branch's own trailer).
    """
    main_sha = get_branch_head(temp_repo, "main")

    # Create parent-a from main
    create_branch(temp_repo, "parent-a", main_sha, checkout=True)
    trailers_a = Trailers(parent_branch="main")
    commit_files(
        temp_repo,
        {tmp_path / "a.txt": "a"},
        trailers_a.apply_to("feat: parent a"),
    )
    parent_a_sha = get_branch_head(temp_repo, "parent-a")

    # Create child branch (3 commits on top of parent-a)
    create_branch(temp_repo, "child", parent_a_sha, checkout=True)
    trailers_c = Trailers(parent_branch="parent-a")
    commit_files(
        temp_repo,
        {tmp_path / "c1.txt": "c1"},
        trailers_c.apply_to("feat: child"),
    )
    commit_files(temp_repo, {tmp_path / "c2.txt": "c2"}, "style: format")

    # Create parent-b from main (diverged lineage, not from parent-a)
    create_branch(temp_repo, "parent-b", main_sha, checkout=True)
    trailers_b = Trailers(parent_branch="main")
    commit_files(
        temp_repo,
        {tmp_path / "b.txt": "b"},
        trailers_b.apply_to("feat: parent b"),
    )

    # Delete parent-a (simulates the old parent being gone)
    del temp_repo.refs[b"refs/heads/parent-a"]

    # Re-parent child to parent-b
    result = _adopt(temp_repo, branch="child", parent="parent-b", force=True)
    assert result.parent == "parent-b"

    # Verify child's first commit trailer was updated (not parent-a's)
    child_head = git.get_branch_head(temp_repo, "child")
    parent_b_head = git.get_branch_head(temp_repo, "parent-b")
    commits = git.get_commits_between(temp_repo, child_head, parent_b_head)

    # Check each commit — only the child's first commit should reference parent-b
    found_child_trailer = False
    for c in commits:
        msg = git.get_commit_message(temp_repo, c)
        t = Trailers.from_message(msg)
        if "feat: child" in msg:
            assert t.parent_branch == "parent-b"
            found_child_trailer = True
        elif "feat: parent a" in msg:
            # Ancestor commit should keep its original trailer
            assert t.parent_branch == "main"
    assert found_child_trailer


def test_adopt_default_branch(temp_repo: Repo) -> None:
    """Test error when trying to adopt default branch."""
    with pytest.raises(AdoptError, match="Cannot adopt default branch"):
        _adopt(temp_repo, branch="main")


def test_adopt_no_parent_detected(tmp_path: Path) -> None:
    """Test error when no parent branch can be detected."""
    # Create repo without main or master
    repo = init_repo(tmp_path, default_branch="develop")
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    # Create feature branch
    create_branch(repo, "feature", get_branch_head(repo, "develop"), checkout=True)

    # Add commit on feature
    commit_files(repo, {tmp_path / "feature.txt": "feature"}, "Add feature")

    with pytest.raises(AdoptError, match="Cannot detect parent branch"):
        _adopt(repo)


def test_adopt_parent_not_found(repo_with_feature: Repo) -> None:
    """Test error when explicit parent doesn't exist."""
    with pytest.raises(AdoptError, match="Parent branch 'nonexistent' not found"):
        _adopt(repo_with_feature, parent="nonexistent")


def test_adopt_no_commits_on_branch(temp_repo: Repo) -> None:
    """Test error when branch has no commits relative to parent."""
    # Create feature branch at same commit as main (no new commits)
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )

    with pytest.raises(AdoptError, match="No commits on 'feature' relative to 'main'"):
        _adopt(temp_repo)


def test_adopt_multiple_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test adopting branch with multiple commits triggers replay."""
    # Create feature branch
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )

    # Add first commit
    commit_files(
        temp_repo,
        {tmp_path / "file1.txt": "content1"},
        "First feature commit",
    )

    # Add second commit
    commit_files(
        temp_repo,
        {tmp_path / "file2.txt": "content2"},
        "Second feature commit",
    )

    result = _adopt(temp_repo)

    assert result.branch == "feature"

    # Verify trailer on first commit
    head = git.get_branch_head(temp_repo, "feature")
    # Walk back to first commit
    commits = git.get_commits_between(
        temp_repo, head, git.get_branch_head(temp_repo, "main")
    )
    first_commit = commits[-1]
    message = git.get_commit_message(temp_repo, first_commit)
    assert Trailers.from_message(message).parent_branch == "main"


def test_trailers_from_message() -> None:
    """Test trailer extraction."""
    message = "feat: something\n\nBody text\n\nShortcake-Parent: main\n"
    trailers = Trailers.from_message(message)
    assert trailers.parent_branch == "main"


def test_trailers_from_message_empty() -> None:
    """Test trailer extraction with no trailers."""
    message = "feat: something\n\nBody text\n"
    trailers = Trailers.from_message(message)
    assert trailers.parent_branch is None


def test_trailers_apply_to() -> None:
    """Test trailer addition."""
    message = "feat: something"
    trailers = Trailers(parent_branch="main")
    result = trailers.apply_to(message)
    assert "Shortcake-Parent: main" in result


def test_trailers_apply_to_empty() -> None:
    """Test apply with no trailers does nothing."""
    message = "feat: something"
    trailers = Trailers()
    result = trailers.apply_to(message)
    assert result == message


def test_trailers_apply_to_preserves_existing() -> None:
    """Test apply preserves existing trailers."""
    message = "feat: something\n\nBody\n\nExisting-Trailer: value\n"
    trailers = Trailers(parent_branch="main")
    result = trailers.apply_to(message)
    assert "Existing-Trailer: value" in result
    assert "Shortcake-Parent: main" in result


def test_trailers_apply_to_replaces_existing_parent() -> None:
    """Test apply updates an existing Shortcake trailer instead of duplicating it."""
    message = "feat: something\n\nBody\n\nShortcake-Parent: old-parent\n"
    trailers = Trailers(parent_branch="main")
    result = trailers.apply_to(message)
    assert "Shortcake-Parent: old-parent" not in result
    assert "Shortcake-Parent: main" in result


# CLI tests


def test_adopt_cli_force_reparent(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test CLI re-parenting with --force flag."""
    import os

    # First adopt with main as parent
    _adopt(repo_with_feature)

    # Create a new branch to use as parent
    create_branch(
        repo_with_feature,
        "develop",
        get_branch_head(repo_with_feature, "main"),
    )

    os.chdir(tmp_path)
    result = runner.invoke(app, ["adopt", "--force", "--parent", "develop"])

    assert result.exit_code == 0
    assert "Re-parented 'feature' to 'develop'" in result.output

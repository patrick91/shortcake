from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.adopt import AdoptError, _adopt


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
    repo_with_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

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
    main_sha = repo_with_feature.refs[b"refs/heads/main"]
    repo_with_feature.refs[b"refs/heads/develop"] = main_sha

    # Re-parent to develop with --force
    result = _adopt(repo_with_feature, parent="develop", force=True)
    assert result.parent == "develop"

    # Verify the trailer was updated
    from shortcake._git._stack import get_branch_parent

    all_branches = {"feature", "main", "develop"}
    parent = get_branch_parent(repo_with_feature, "feature", all_branches)
    assert parent == "develop"


def test_adopt_default_branch(temp_repo: Repo) -> None:
    """Test error when trying to adopt default branch."""
    with pytest.raises(AdoptError, match="Cannot adopt default branch"):
        _adopt(temp_repo, branch="main")


def test_adopt_no_parent_detected(tmp_path: Path) -> None:
    """Test error when no parent branch can be detected."""
    # Create repo without main or master
    repo = Repo.init(tmp_path, default_branch=b"develop")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create feature branch
    develop_sha = repo.refs[b"refs/heads/develop"]
    repo.refs[b"refs/heads/feature"] = develop_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add commit on feature
    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature")
    porcelain.add(repo, paths=[str(test_file)])
    porcelain.commit(repo, message=b"Add feature")

    with pytest.raises(AdoptError, match="Cannot detect parent branch"):
        _adopt(repo)


def test_adopt_parent_not_found(repo_with_feature: Repo) -> None:
    """Test error when explicit parent doesn't exist."""
    with pytest.raises(AdoptError, match="Parent branch 'nonexistent' not found"):
        _adopt(repo_with_feature, parent="nonexistent")


def test_adopt_no_commits_on_branch(temp_repo: Repo) -> None:
    """Test error when branch has no commits relative to parent."""
    # Create feature branch at same commit as main (no new commits)
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    with pytest.raises(AdoptError, match="No commits on 'feature' relative to 'main'"):
        _adopt(temp_repo)


def test_adopt_multiple_commits(temp_repo: Repo, tmp_path: Path) -> None:
    """Test adopting branch with multiple commits triggers replay."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add first commit
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    porcelain.add(temp_repo, paths=[str(file1)])
    porcelain.commit(temp_repo, message=b"First feature commit")

    # Add second commit
    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")
    porcelain.add(temp_repo, paths=[str(file2)])
    porcelain.commit(temp_repo, message=b"Second feature commit")

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

from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake.commands.adopt import (
    TRAILER_KEY,
    _adopt,
    _add_trailer,
    _get_trailer,
)


def test_adopt_current_branch(repo_with_feature):
    """Test adopting the current feature branch."""
    result = _adopt(repo_with_feature)

    assert result.success
    assert result.branch == "feature"
    assert result.parent == "main"

    # Verify trailer was added
    head = git.get_branch_head(repo_with_feature, "feature")
    message = git.get_commit_message(repo_with_feature, head)
    assert _get_trailer(message, TRAILER_KEY) == "main"


def test_adopt_specified_branch(repo_with_feature):
    """Test adopting a specified branch."""
    # Switch back to main
    repo_with_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

    result = _adopt(repo_with_feature, branch="feature")

    assert result.success
    assert result.branch == "feature"


def test_adopt_with_explicit_parent(repo_with_feature):
    """Test adopting with explicit parent."""
    result = _adopt(repo_with_feature, parent="main")

    assert result.success


def test_adopt_already_tracked(repo_with_feature):
    """Test error when branch already tracked."""
    # First adopt
    _adopt(repo_with_feature)

    # Try again
    result = _adopt(repo_with_feature)

    assert not result.success
    assert "already tracked" in result.error


def test_adopt_default_branch(temp_repo):
    """Test error when trying to adopt default branch."""
    result = _adopt(temp_repo, branch="main")

    assert not result.success
    assert "Cannot adopt default branch" in result.error


def test_adopt_no_parent_detected(tmp_path: Path):
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

    result = _adopt(repo)

    assert not result.success
    assert "Cannot detect parent branch. Use --parent to specify." in result.error


def test_adopt_parent_not_found(repo_with_feature):
    """Test error when explicit parent doesn't exist."""
    result = _adopt(repo_with_feature, parent="nonexistent")

    assert not result.success
    assert "Parent branch 'nonexistent' not found" in result.error


def test_adopt_no_commits_on_branch(temp_repo):
    """Test error when branch has no commits relative to parent."""
    # Create feature branch at same commit as main (no new commits)
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    result = _adopt(temp_repo)

    assert not result.success
    assert "No commits on 'feature' relative to 'main'" in result.error


def test_adopt_multiple_commits(temp_repo, tmp_path: Path):
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

    assert result.success
    assert result.branch == "feature"

    # Verify trailer on first commit
    head = git.get_branch_head(temp_repo, "feature")
    # Walk back to first commit
    commits = git.get_commits_between(
        temp_repo, head, git.get_branch_head(temp_repo, "main")
    )
    first_commit = commits[-1]
    message = git.get_commit_message(temp_repo, first_commit)
    assert _get_trailer(message, TRAILER_KEY) == "main"


def test_get_trailer():
    """Test trailer extraction."""
    message = "feat: something\n\nBody text\n\nShortcake-Parent: main\n"
    assert _get_trailer(message, "Shortcake-Parent") == "main"
    assert _get_trailer(message, "Other") is None


def test_add_trailer():
    """Test trailer addition."""
    message = "feat: something"
    result = _add_trailer(message, "Shortcake-Parent", "main")
    assert "Shortcake-Parent: main" in result

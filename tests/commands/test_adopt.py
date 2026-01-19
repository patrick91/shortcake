import pytest
from shortcake.commands.adopt import adopt, get_trailer, add_trailer, TRAILER_KEY
from shortcake import _git as git


def test_adopt_current_branch(repo_with_feature):
    """Test adopting the current feature branch."""
    result = adopt(repo_with_feature)

    assert result.success
    assert result.branch == "feature"
    assert result.parent == "main"

    # Verify trailer was added
    head = git.get_branch_head(repo_with_feature, "feature")
    message = git.get_commit_message(repo_with_feature, head)
    assert get_trailer(message, TRAILER_KEY) == "main"


def test_adopt_specified_branch(repo_with_feature):
    """Test adopting a specified branch."""
    # Switch back to main
    repo_with_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

    result = adopt(repo_with_feature, branch="feature")

    assert result.success
    assert result.branch == "feature"


def test_adopt_with_explicit_parent(repo_with_feature):
    """Test adopting with explicit parent."""
    result = adopt(repo_with_feature, parent="main")

    assert result.success


def test_adopt_already_tracked(repo_with_feature):
    """Test error when branch already tracked."""
    # First adopt
    adopt(repo_with_feature)

    # Try again
    result = adopt(repo_with_feature)

    assert not result.success
    assert "already tracked" in result.error


def test_adopt_trunk_branch(temp_repo):
    """Test error when trying to adopt trunk."""
    result = adopt(temp_repo, branch="main")

    assert not result.success
    assert "Cannot adopt trunk" in result.error


def test_get_trailer():
    """Test trailer extraction."""
    message = "feat: something\n\nBody text\n\nShortcake-Parent: main\n"
    assert get_trailer(message, "Shortcake-Parent") == "main"
    assert get_trailer(message, "Other") is None


def test_add_trailer():
    """Test trailer addition."""
    message = "feat: something"
    result = add_trailer(message, "Shortcake-Parent", "main")
    assert "Shortcake-Parent: main" in result

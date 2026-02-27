import stat
from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers, strip_trailers
from shortcake.commands.adopt import _adopt
from shortcake.commands.modify import _modify_amend, _modify_with_new_commit

# strip_trailers tests


def test_strip_trailers_with_shortcake_trailer() -> None:
    """Test stripping Shortcake-Parent trailer."""
    message = "feat: add feature\n\nSome body text.\n\nShortcake-Parent: main"
    result = strip_trailers(message)
    assert result == "feat: add feature\n\nSome body text."


def test_strip_trailers_no_trailer() -> None:
    """Test message without trailers is returned as-is."""
    message = "feat: add feature\n\nSome body text."
    result = strip_trailers(message)
    assert result == "feat: add feature\n\nSome body text."


def test_strip_trailers_only_subject() -> None:
    """Test message with only subject line."""
    message = "feat: add feature"
    result = strip_trailers(message)
    assert result == "feat: add feature"


def test_strip_trailers_subject_with_trailer() -> None:
    """Test subject line with trailer."""
    message = "feat: add feature\n\nShortcake-Parent: main"
    result = strip_trailers(message)
    assert result == "feat: add feature"


def test_strip_trailers_preserves_other_trailers() -> None:
    """Test that non-Shortcake trailers are preserved."""
    message = (
        "feat: add feature\n\nCo-authored-by: test@test.com\nShortcake-Parent: main"
    )
    result = strip_trailers(message)
    # Should only strip Shortcake-Parent, keeping Co-authored-by
    assert "Co-authored-by: test@test.com" in result
    assert "Shortcake-Parent" not in result


def test_strip_trailers_empty_message() -> None:
    """Test empty message."""
    message = ""
    result = strip_trailers(message)
    assert result == ""


def test_strip_trailers_whitespace_only() -> None:
    """Test whitespace-only message is returned as-is (no trailers to strip)."""
    message = "   \n\n  "
    result = strip_trailers(message)
    # No trailers present, so message returned as-is
    assert result == "   \n\n  "


# amend_commit tests


def test_amend_commit_changes_message(temp_repo: Repo) -> None:
    """Test amend_commit changes the commit message."""
    old_sha = temp_repo.head()
    old_message = git.get_commit_message(temp_repo, old_sha)
    assert old_message.strip() == "Initial commit"

    new_sha = git.amend_commit(temp_repo, "Updated commit message")

    assert new_sha != old_sha
    new_message = git.get_commit_message(temp_repo, new_sha)
    assert new_message.strip() == "Updated commit message"


def test_amend_commit_includes_staged_changes(temp_repo: Repo, tmp_path: Path) -> None:
    """Test amend_commit includes staged changes."""
    # Stage a new file
    new_file = tmp_path / "new_file.txt"
    new_file.write_text("new content")
    porcelain.add(temp_repo, paths=[str(new_file)])

    new_sha = git.amend_commit(temp_repo, "Amended with new file")

    # Verify file is in the new commit
    commit = temp_repo[new_sha]
    tree = temp_repo[commit.tree]
    files = [entry.path for entry in tree.items()]
    assert b"new_file.txt" in files


def test_amend_commit_preserves_parents(temp_repo: Repo) -> None:
    """Test amend_commit preserves commit parents."""
    old_sha = temp_repo.head()
    old_commit = temp_repo[old_sha]
    old_parents = old_commit.parents

    new_sha = git.amend_commit(temp_repo, "Amended message")

    new_commit = temp_repo[new_sha]
    assert new_commit.parents == old_parents


# _modify_amend tests


def test_modify_message_only(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test modifying just the message."""
    # First adopt the branch so it has a trailer
    _adopt(repo_with_feature)

    old_sha = repo_with_feature.head()

    result = _modify_amend(repo_with_feature, "feat: updated message")

    assert result.old_sha == old_sha
    assert result.new_sha != old_sha
    assert "feat: updated message" in result.message


def test_modify_preserves_trailer(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test that _modify preserves the Shortcake-Parent trailer."""
    # First adopt the branch so it has a trailer
    _adopt(repo_with_feature)

    # Verify trailer exists before modify
    old_sha = repo_with_feature.head()
    old_message = git.get_commit_message(repo_with_feature, old_sha)
    old_trailers = Trailers.from_message(old_message)
    assert old_trailers.parent_branch == "main"

    # Modify with new message
    result = _modify_amend(repo_with_feature, "feat: completely new message")

    # Verify trailer is preserved
    new_message = git.get_commit_message(repo_with_feature, result.new_sha)
    new_trailers = Trailers.from_message(new_message)
    assert new_trailers.parent_branch == "main"
    assert "feat: completely new message" in new_message


def test_modify_with_staged_changes(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test modifying with staged changes."""
    _adopt(repo_with_feature)

    # Stage a new file
    new_file = tmp_path / "staged_file.txt"
    new_file.write_text("staged content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    result = _modify_amend(repo_with_feature, "feat: with staged changes")

    # Verify file is in the new commit
    commit = repo_with_feature[result.new_sha]
    tree = repo_with_feature[commit.tree]
    files = [entry.path for entry in tree.items()]
    assert b"staged_file.txt" in files


def test_modify_without_trailer(temp_repo: Repo) -> None:
    """Test modifying commit without trailer."""
    # Initial commit on main has no trailer
    result = _modify_amend(temp_repo, "Updated initial commit")

    # Should not add a trailer if there wasn't one
    new_message = git.get_commit_message(temp_repo, result.new_sha)
    trailers = Trailers.from_message(new_message)
    assert trailers.parent_branch is None


def test_modify_no_verify(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test _modify with no_verify flag skips hooks."""
    _adopt(repo_with_feature)

    # Create a failing pre-commit hook
    hooks_dir = Path(repo_with_feature.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to make the hook relevant
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    # With no_verify=True, should succeed despite failing hook
    result = _modify_amend(repo_with_feature, "feat: no verify test", no_verify=True)
    assert result.new_sha != result.old_sha


# _modify_with_new_commit tests


def test_modify_with_new_commit_creates_commit(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test _modify_with_new_commit creates a new commit on top of HEAD."""
    _adopt(repo_with_feature)

    old_sha = repo_with_feature.head()

    # Stage a new file
    new_file = tmp_path / "new_feature.txt"
    new_file.write_text("new feature content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    result = _modify_with_new_commit(repo_with_feature, "feat: new commit")

    # New commit should have old_sha as parent
    new_commit = repo_with_feature[result.new_sha]
    assert old_sha in new_commit.parents
    assert result.is_amend is False


def test_modify_with_new_commit_preserves_trailer(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test that _modify_with_new_commit preserves the Shortcake-Parent trailer."""
    _adopt(repo_with_feature)

    # Verify trailer exists before modify
    old_sha = repo_with_feature.head()
    old_message = git.get_commit_message(repo_with_feature, old_sha)
    old_trailers = Trailers.from_message(old_message)
    assert old_trailers.parent_branch == "main"

    # Stage a new file
    new_file = tmp_path / "another_file.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    result = _modify_with_new_commit(repo_with_feature, "feat: another commit")

    # Verify trailer is preserved in new commit
    new_message = git.get_commit_message(repo_with_feature, result.new_sha)
    new_trailers = Trailers.from_message(new_message)
    assert new_trailers.parent_branch == "main"
    assert "feat: another commit" in new_message


def test_modify_with_new_commit_without_trailer(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test _modify_with_new_commit on commit without trailer."""
    # Stage a new file
    new_file = tmp_path / "file.txt"
    new_file.write_text("content")
    porcelain.add(temp_repo, paths=[str(new_file)])

    result = _modify_with_new_commit(temp_repo, "New commit")

    # Should not add a trailer if there wasn't one
    new_message = git.get_commit_message(temp_repo, result.new_sha)
    trailers = Trailers.from_message(new_message)
    assert trailers.parent_branch is None


# CLI tests


def test_modify_cli_with_precommit_hook_success(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test modify CLI runs pre-commit hooks successfully."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Create a passing pre-commit hook
    hooks_dir = Path(repo_with_feature.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 0\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify", "-m", "feat: with hooks"])

    assert result.exit_code == 0
    assert "Running pre-commit hooks" in result.output

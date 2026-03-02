import stat
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers, strip_trailers
from shortcake.commands.adopt import _adopt
from shortcake.commands.modify import (
    ModifyError,
    _modify_amend,
    _modify_target,
    _modify_with_new_commit,
)


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


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


# _modify_target tests


def test_modify_target_basic(temp_repo: Repo, tmp_path: Path) -> None:
    """Fold staged changes into parent branch, verify file in target's commit."""
    repo = temp_repo

    # Create tracked branch_a from main
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # Create tracked branch_b from branch_a
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(repo, message=message_b.encode())

    # Now on branch_b, stage a new file to fold into branch_a
    new_file = tmp_path / "folded.txt"
    new_file.write_text("folded content")
    porcelain.add(repo, paths=[str(new_file)])

    result = _modify_target(repo, "branch_a")

    assert result.target_branch == "branch_a"
    assert result.is_amend is True

    # Verify we're back on branch_b
    assert git.get_current_branch(repo) == "branch_b"

    # Verify the file is in branch_a's commit
    switch_branch(repo, "branch_a")
    assert (tmp_path / "folded.txt").exists()
    assert (tmp_path / "folded.txt").read_text() == "folded content"


def test_modify_target_preserves_trailer(temp_repo: Repo, tmp_path: Path) -> None:
    """Verify target's commit retains Shortcake-Parent after amend."""
    repo = temp_repo

    # Create tracked branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # Create tracked branch_b
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(repo, message=message_b.encode())

    # Stage changes and fold into branch_a
    new_file = tmp_path / "extra.txt"
    new_file.write_text("extra")
    porcelain.add(repo, paths=[str(new_file)])

    _modify_target(repo, "branch_a")

    # Check branch_a's commit message still has the trailer
    branch_a_head = git.get_branch_head(repo, "branch_a")
    msg = git.get_commit_message(repo, branch_a_head)
    trailers = Trailers.from_message(msg)
    assert trailers.parent_branch == "main"


def test_modify_target_preserves_working_changes(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Unstaged changes survive the operation."""
    repo = temp_repo

    # Create tracked branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # Create tracked branch_b
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(repo, message=message_b.encode())

    # Stage one file, leave another as unstaged working change
    staged_file = tmp_path / "staged.txt"
    staged_file.write_text("staged content")
    porcelain.add(repo, paths=[str(staged_file)])

    unstaged_file = tmp_path / "unstaged.txt"
    unstaged_file.write_text("unstaged content")

    _modify_target(repo, "branch_a")

    # Verify we're back on branch_b
    assert git.get_current_branch(repo) == "branch_b"

    # Verify the unstaged file survived
    assert unstaged_file.exists()
    assert unstaged_file.read_text() == "unstaged content"


def test_modify_target_restacks_downstream(temp_repo: Repo, tmp_path: Path) -> None:
    """Stack a→b→c, fold into a, verify b+c restacked."""
    repo = temp_repo

    # Create branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # Create branch_b
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(repo, message=message_b.encode())
    branch_b_sha = repo.refs[b"refs/heads/branch_b"]

    # Create branch_c
    repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    file_c = tmp_path / "c.txt"
    file_c.write_text("branch c content")
    porcelain.add(repo, paths=[str(file_c)])
    trailers_c = Trailers(parent_branch="branch_b")
    message_c = trailers_c.apply_to("feat: branch c")
    porcelain.commit(repo, message=message_c.encode())

    # Stage a new file to fold into branch_a
    new_file = tmp_path / "folded.txt"
    new_file.write_text("folded")
    porcelain.add(repo, paths=[str(new_file)])

    result = _modify_target(repo, "branch_a")

    # Should have restacked branch_b and branch_c
    assert "branch_b" in result.restacked_branches
    assert "branch_c" in result.restacked_branches

    # Verify the folded file is accessible from all downstream branches
    switch_branch(repo, "branch_b")
    assert (tmp_path / "folded.txt").exists()
    switch_branch(repo, "branch_c")
    assert (tmp_path / "folded.txt").exists()


def test_modify_target_no_staged_changes(temp_repo: Repo, tmp_path: Path) -> None:
    """ModifyError when no staged changes."""
    repo = temp_repo

    # Create tracked branch
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())

    with pytest.raises(ModifyError, match="No staged changes"):
        _modify_target(repo, "main")


def test_modify_target_branch_not_exist(temp_repo: Repo, tmp_path: Path) -> None:
    """ModifyError when target branch doesn't exist."""
    repo = temp_repo

    # Stage something
    new_file = tmp_path / "file.txt"
    new_file.write_text("content")
    porcelain.add(repo, paths=[str(new_file)])

    with pytest.raises(ModifyError, match="does not exist"):
        _modify_target(repo, "nonexistent")


def test_modify_target_branch_not_tracked(temp_repo: Repo, tmp_path: Path) -> None:
    """ModifyError when target branch is not tracked by Shortcake."""
    repo = temp_repo

    # Create an untracked branch (no Shortcake-Parent trailer)
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/untracked"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/untracked")

    untracked_file = tmp_path / "untracked.txt"
    untracked_file.write_text("content")
    porcelain.add(repo, paths=[str(untracked_file)])
    porcelain.commit(repo, message=b"untracked commit")

    # Stage something
    new_file = tmp_path / "file.txt"
    new_file.write_text("content")
    porcelain.add(repo, paths=[str(new_file)])

    with pytest.raises(ModifyError, match="not tracked"):
        _modify_target(repo, "main")


def test_modify_target_rebase_in_progress(temp_repo: Repo, tmp_path: Path) -> None:
    """ModifyError when rebase is in progress."""
    repo = temp_repo

    # Create tracked branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # Create tracked branch_b
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(repo, message=message_b.encode())

    # Stage something
    new_file = tmp_path / "file.txt"
    new_file.write_text("content")
    porcelain.add(repo, paths=[str(new_file)])

    # Simulate rebase in progress
    rebase_dir = Path(repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir(exist_ok=True)

    try:
        with pytest.raises(ModifyError, match="rebase in progress"):
            _modify_target(repo, "branch_a")
    finally:
        rebase_dir.rmdir()


def test_modify_target_same_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """ModifyError when target is the current branch."""
    repo = temp_repo

    # Stage something
    new_file = tmp_path / "file.txt"
    new_file.write_text("content")
    porcelain.add(repo, paths=[str(new_file)])

    with pytest.raises(ModifyError, match="cannot be the current branch"):
        _modify_target(repo, "main")


def test_modify_target_rollback_on_restack_failure(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Refs are restored and staged changes re-applied on restack failure."""
    repo = temp_repo

    # Build: main → branch_a → branch_b (sibling of branch_c)
    #                         → branch_c (where we fold from)
    # branch_b modifies shared.txt, so when branch_a is amended to also modify
    # shared.txt, restacking branch_b will conflict.
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    shared = tmp_path / "shared.txt"
    shared.write_text("original line 1\noriginal line 2\n")
    porcelain.add(repo, paths=[str(shared)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # branch_b: child of branch_a, rewrites shared.txt
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    shared.write_text("COMPLETELY DIFFERENT\nFOR CONFLICT\n")
    porcelain.add(repo, paths=[str(shared)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b conflict")
    porcelain.commit(repo, message=message_b.encode())

    # branch_c: also child of branch_a (sibling of branch_b)
    # shared.txt on branch_c has branch_a's content ("original line 1\n...")
    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    switch_branch(repo, "branch_c")  # reset working tree to branch_a's content

    file_c = tmp_path / "c.txt"
    file_c.write_text("branch c content")
    porcelain.add(repo, paths=[str(file_c)])
    trailers_c = Trailers(parent_branch="branch_a")
    message_c = trailers_c.apply_to("feat: branch c")
    porcelain.commit(repo, message=message_c.encode())

    # Save original SHAs
    a_sha_before = git.get_branch_head(repo, "branch_a").decode()
    b_sha_before = git.get_branch_head(repo, "branch_b").decode()

    # From branch_c, modify shared.txt (same content as branch_a's version)
    # and stage it. The patch context matches branch_a, so it applies cleanly.
    # But restacking branch_b (which also rewrites shared.txt) will conflict.
    shared.write_text("MODIFIED BY FOLD\nNEW CONTENT\n")
    porcelain.add(repo, paths=[str(shared)])

    with pytest.raises(ModifyError, match="Restack failed"):
        _modify_target(repo, "branch_a")

    # Verify refs were rolled back
    a_sha_after = git.get_branch_head(repo, "branch_a").decode()
    b_sha_after = git.get_branch_head(repo, "branch_b").decode()
    assert a_sha_after == a_sha_before
    assert b_sha_after == b_sha_before

    # Verify staged changes were restored
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "shared.txt" in result.stdout


def test_modify_cli_target_incompatible_with_message(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """CLI exits with code 1 when -t and -m are both provided."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify", "-t", "main", "-m", "message"])

    assert result.exit_code == 1
    assert "Cannot use both -t and -m" in result.output


def test_modify_cli_target_incompatible_with_edit(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """CLI exits with code 1 when -t and -e are both provided."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify", "-t", "main", "-e"])

    assert result.exit_code == 1
    assert "Cannot use both -t and -e" in result.output


def test_modify_cli_message_and_edit_incompatible(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """CLI exits with code 1 when -m and -e are both provided."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify", "-m", "msg", "-e"])

    assert result.exit_code == 1
    assert "Cannot use both -m and -e" in result.output


def test_modify_cli_target_success(temp_repo: Repo, tmp_path: Path) -> None:
    """CLI --target succeeds and outputs confirmation message."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    repo = temp_repo

    # Create tracked branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # Create tracked branch_b
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(repo, message=message_b.encode())

    # Stage a new file to fold into branch_a
    new_file = tmp_path / "folded.txt"
    new_file.write_text("folded content")
    porcelain.add(repo, paths=[str(new_file)])

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify", "-t", "branch_a"])

    assert result.exit_code == 0
    assert "Folded staged changes into 'branch_a'" in result.output


def test_modify_cli_target_error(temp_repo: Repo, tmp_path: Path) -> None:
    """CLI --target prints error and exits 1 on ModifyError."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    runner = CliRunner()
    os.chdir(tmp_path)
    # No staged changes → should error
    result = runner.invoke(app, ["modify", "-t", "nonexistent"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_modify_cli_detached_head(temp_repo: Repo, tmp_path: Path) -> None:
    """CLI exits with code 1 when in detached HEAD state."""
    import os
    from unittest.mock import patch

    from typer.testing import CliRunner

    from shortcake.cli import app

    runner = CliRunner()
    os.chdir(tmp_path)

    # Mock get_current_branch to return None (detached HEAD)
    with patch(
        "shortcake.commands.modify.git.get_current_branch",
        return_value=None,
    ):
        result = runner.invoke(app, ["modify"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_modify_cli_message_no_staged(repo_with_feature: Repo, tmp_path: Path) -> None:
    """CLI -m exits with code 1 when no staged changes."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify", "-m", "new commit"])

    assert result.exit_code == 1
    assert "No staged changes" in result.output


def test_modify_cli_amend_no_staged(repo_with_feature: Repo, tmp_path: Path) -> None:
    """CLI default amend exits with code 1 when no staged changes."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify"])

    assert result.exit_code == 1
    assert "No staged changes" in result.output


def test_modify_cli_amend_success(repo_with_feature: Repo, tmp_path: Path) -> None:
    """CLI default amend succeeds with staged changes."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Stage a new file
    new_file = tmp_path / "staged.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify"])

    assert result.exit_code == 0
    assert "Amended commit on" in result.output


def test_modify_cli_message_success(repo_with_feature: Repo, tmp_path: Path) -> None:
    """CLI -m creates new commit with staged changes."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Stage a new file
    new_file = tmp_path / "staged.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify", "-m", "feat: new commit"])

    assert result.exit_code == 0
    assert "Created commit on" in result.output


def test_modify_cli_edit_success(repo_with_feature: Repo, tmp_path: Path) -> None:
    """CLI -e amends with edited message."""
    import os
    from unittest.mock import patch

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Stage a new file
    new_file = tmp_path / "staged.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    runner = CliRunner()
    os.chdir(tmp_path)
    with patch(
        "shortcake.commands.modify.open_editor",
        return_value="feat: edited message",
    ):
        result = runner.invoke(app, ["modify", "-e"])

    assert result.exit_code == 0
    assert "Amended commit on" in result.output


def test_modify_cli_edit_empty_message(repo_with_feature: Repo, tmp_path: Path) -> None:
    """CLI -e aborts when editor returns empty message."""
    import os
    from unittest.mock import patch

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Stage a new file
    new_file = tmp_path / "staged.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    runner = CliRunner()
    os.chdir(tmp_path)
    with patch(
        "shortcake.commands.modify.open_editor",
        return_value="",
    ):
        result = runner.invoke(app, ["modify", "-e"])

    assert result.exit_code == 1
    assert "Aborted" in result.output


def test_modify_cli_precommit_hook_failure(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """CLI prints error when pre-commit hook fails."""
    import os
    import stat as stat_mod

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Create a failing pre-commit hook
    hooks_dir = Path(repo_with_feature.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'hook failed' >&2\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat_mod.S_IXUSR)

    # Stage a file to trigger hook
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(app, ["modify"])

    assert result.exit_code == 1
    assert "Pre-commit hook failed" in result.output

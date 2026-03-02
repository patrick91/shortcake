import os
import stat
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.fold import FoldError, _fold


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


runner = CliRunner()


# --- Precondition tests ---


def test_fold_detached_head(repo_with_stack: Repo) -> None:
    """FoldError when HEAD is detached."""
    main_sha = repo_with_stack.refs[b"refs/heads/branch_b"]
    del repo_with_stack.refs[b"HEAD"]
    repo_with_stack.refs[b"HEAD"] = main_sha
    with pytest.raises(FoldError, match="detached HEAD"):
        _fold(repo_with_stack)


def test_fold_uncommitted_changes(repo_with_stack: Repo, tmp_path: Path) -> None:
    """FoldError when there are uncommitted changes."""
    switch_branch(repo_with_stack, "branch_b")
    (tmp_path / "dirty.txt").write_text("dirty")
    porcelain.add(repo_with_stack, paths=[str(tmp_path / "dirty.txt")])
    with pytest.raises(FoldError, match="uncommitted changes"):
        _fold(repo_with_stack)


def test_fold_rebase_in_progress(repo_with_stack: Repo, tmp_path: Path) -> None:
    """FoldError when rebase is in progress."""
    switch_branch(repo_with_stack, "branch_b")
    # Simulate rebase in progress
    rebase_dir = tmp_path / ".git" / "rebase-merge"
    rebase_dir.mkdir(parents=True)
    (rebase_dir / "head-name").write_text("refs/heads/branch_b")
    with pytest.raises(FoldError, match="rebase in progress"):
        _fold(repo_with_stack)


def test_fold_untracked_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """FoldError when current branch is not tracked."""
    # Create untracked feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    file = tmp_path / "feature.txt"
    file.write_text("feature")
    porcelain.add(temp_repo, paths=[str(file)])
    porcelain.commit(temp_repo, message=b"Add feature")

    with pytest.raises(FoldError, match="not tracked"):
        _fold(temp_repo)


def test_fold_nonexistent_target(repo_with_stack: Repo) -> None:
    """FoldError when --into target doesn't exist."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(FoldError, match="does not exist"):
        _fold(repo_with_stack, into="nonexistent")


def test_fold_self_target(repo_with_stack: Repo) -> None:
    """FoldError when trying to fold into self."""
    switch_branch(repo_with_stack, "branch_b")
    with pytest.raises(FoldError, match="Cannot fold a branch into itself"):
        _fold(repo_with_stack, into="branch_b")


# --- Basic fold tests ---


def test_fold_into_parent(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Fold branch_b into branch_a (parent): changes land in parent, source deleted."""
    switch_branch(repo_with_stack, "branch_b")

    result = _fold(repo_with_stack)

    assert result.source_branch == "branch_b"
    assert result.target_branch == "branch_a"
    assert result.reparented_children == []

    # Source branch should be deleted
    assert not git.branch_exists(repo_with_stack, "branch_b")

    # Should now be on target branch
    assert git.get_current_branch(repo_with_stack) == "branch_a"

    # b.txt should exist in branch_a's tree
    assert (tmp_path / "b.txt").exists()
    assert (tmp_path / "b.txt").read_text() == "branch b content"

    # branch_a's trailer should still point to main
    head = git.get_branch_head(repo_with_stack, "branch_a")
    message = git.get_commit_message(repo_with_stack, head)
    assert Trailers.from_message(message).parent_branch == "main"


def test_fold_preserves_target_trailer(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Target branch's Shortcake-Parent trailer is preserved after fold."""
    switch_branch(repo_with_stack, "branch_b")
    _fold(repo_with_stack)

    head = git.get_branch_head(repo_with_stack, "branch_a")
    message = git.get_commit_message(repo_with_stack, head)
    trailers = Trailers.from_message(message)
    assert trailers.parent_branch == "main"


def test_fold_with_into_flag(repo_with_stack: Repo, tmp_path: Path) -> None:
    """Fold branch_b into branch_a using --into flag explicitly."""
    switch_branch(repo_with_stack, "branch_b")

    result = _fold(repo_with_stack, into="branch_a")

    assert result.source_branch == "branch_b"
    assert result.target_branch == "branch_a"
    assert not git.branch_exists(repo_with_stack, "branch_b")


# --- Re-parenting tests ---


def test_fold_reparents_single_child(temp_repo: Repo, tmp_path: Path) -> None:
    """Stack A→B→C, fold B: C should be re-parented to A."""
    # Create A → B → C stack
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # branch_c
    temp_repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    switch_branch(temp_repo, "branch_c")
    (tmp_path / "c.txt").write_text("c content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "c.txt")])
    trailers_c = Trailers(parent_branch="branch_b")
    porcelain.commit(temp_repo, message=trailers_c.apply_to("feat: branch c").encode())

    # Now fold B (current = B)
    switch_branch(temp_repo, "branch_b")
    result = _fold(temp_repo)

    assert result.source_branch == "branch_b"
    assert result.target_branch == "branch_a"
    assert result.reparented_children == ["branch_c"]

    # branch_b deleted
    assert not git.branch_exists(temp_repo, "branch_b")

    # branch_c should now point to branch_a
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_c", all_branches) == "branch_a"

    # b.txt should be in branch_a
    switch_branch(temp_repo, "branch_a")
    assert (tmp_path / "b.txt").exists()


def test_fold_reparents_multiple_children(repo_with_fork: Repo, tmp_path: Path) -> None:
    """Fork A→{B,C}, fold A: B and C should be re-parented to main."""
    switch_branch(repo_with_fork, "branch_a")
    result = _fold(repo_with_fork)

    assert result.source_branch == "branch_a"
    assert result.target_branch == "main"
    assert sorted(result.reparented_children) == ["branch_b", "branch_c"]

    # branch_a deleted
    assert not git.branch_exists(repo_with_fork, "branch_a")

    # Both children point to main
    all_branches = set(git.get_all_local_branches(repo_with_fork))
    assert git.get_branch_parent(repo_with_fork, "branch_b", all_branches) == "main"
    assert git.get_branch_parent(repo_with_fork, "branch_c", all_branches) == "main"


def test_fold_restacks_after_reparent(temp_repo: Repo, tmp_path: Path) -> None:
    """Stack A→B→C, fold B: after reparent, C is restacked onto A."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # branch_c
    temp_repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    switch_branch(temp_repo, "branch_c")
    (tmp_path / "c.txt").write_text("c content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "c.txt")])
    trailers_c = Trailers(parent_branch="branch_b")
    porcelain.commit(temp_repo, message=trailers_c.apply_to("feat: branch c").encode())

    # Fold B
    switch_branch(temp_repo, "branch_b")
    result = _fold(temp_repo)

    # C should have been restacked
    assert "branch_c" in result.restacked_branches

    # Verify c.txt accessible from branch_c
    switch_branch(temp_repo, "branch_c")
    assert (tmp_path / "c.txt").exists()
    assert (tmp_path / "c.txt").read_text() == "c content"
    # b.txt should also be visible (it's in branch_a now, which is parent of branch_c)
    assert (tmp_path / "b.txt").exists()


def test_fold_reparents_child_with_multiple_commits(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Stack A→B→C where C has 2 commits: fold B, C is re-parented with replay."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # branch_c with TWO commits
    temp_repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    switch_branch(temp_repo, "branch_c")
    (tmp_path / "c1.txt").write_text("c1 content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "c1.txt")])
    trailers_c = Trailers(parent_branch="branch_b")
    msg_c = trailers_c.apply_to("feat: branch c first")
    porcelain.commit(temp_repo, message=msg_c.encode())

    (tmp_path / "c2.txt").write_text("c2 content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "c2.txt")])
    porcelain.commit(temp_repo, message=b"feat: branch c second")

    # Fold B
    switch_branch(temp_repo, "branch_b")
    result = _fold(temp_repo)

    assert result.reparented_children == ["branch_c"]

    # branch_c now points to branch_a
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_c", all_branches) == "branch_a"

    # Both commits from branch_c should be preserved
    branch_c_head = git.get_branch_head(temp_repo, "branch_c")
    branch_a_head = git.get_branch_head(temp_repo, "branch_a")
    commits = git.get_commits_between(temp_repo, branch_c_head, branch_a_head)
    assert len(commits) == 2


def test_fold_empty_diff(temp_repo: Repo, tmp_path: Path) -> None:
    """Fold a branch with no unique diff: just delete + reparent."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: tracked but same tree as main (empty diff)
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    trailers_a = Trailers(parent_branch="main")
    # Create a commit with trailer but no file changes - use amend_commit_message
    # Actually we need a commit. Let's create one that adds a file, then we'll test
    # a branch that has the same content.

    # Instead: create branch_a with a commit that has a file
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: has the same file content as branch_a (empty diff relative to parent)
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    # Make a commit with trailer but no file changes - we need at least a commit
    # We create a trivial commit
    (tmp_path / "b_marker.txt").write_text("")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b_marker.txt")])
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())

    # Now fold branch_b into its parent (branch_a)
    result = _fold(temp_repo)

    assert result.source_branch == "branch_b"
    assert result.target_branch == "branch_a"
    assert not git.branch_exists(temp_repo, "branch_b")


def test_fold_rollback_on_patch_failure(temp_repo: Repo, tmp_path: Path) -> None:
    """Rollback when patch can't be applied to target."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: create a file
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "shared.txt").write_text("original from a")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: modifies shared.txt
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "shared.txt").write_text("modified by b")
    porcelain.add(temp_repo, paths=[str(tmp_path / "shared.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())

    # Save original refs
    original_a = git.get_branch_head(temp_repo, "branch_a").decode()
    original_b = git.get_branch_head(temp_repo, "branch_b").decode()

    # Try to fold branch_b into main (which doesn't have shared.txt — patch will fail)
    with pytest.raises(FoldError):
        _fold(temp_repo, into="main")

    # Rollback: both branches should be restored
    assert git.branch_exists(temp_repo, "branch_b")
    assert git.get_branch_head(temp_repo, "branch_b").decode() == original_b
    assert git.get_branch_head(temp_repo, "branch_a").decode() == original_a
    assert git.get_current_branch(temp_repo) == "branch_b"


def test_fold_after_parent_rebased(temp_repo: Repo, tmp_path: Path) -> None:
    """Fold works when parent branch was rebased (stale merge base)."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: create a file
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: child of branch_a, adds its own file
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())

    # Now simulate parent (branch_a) being rebased: amend its commit so HEAD changes.
    # This makes git merge-base(branch_b, branch_a) point to 'main' instead of
    # the old branch_a commit, causing the diff to include branch_a's changes.
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content amended")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    msg = trailers_a.apply_to("feat: branch a amended")
    porcelain.commit(temp_repo, message=msg.encode())

    # branch_b still points to old branch_a commit — merge base is stale
    switch_branch(temp_repo, "branch_b")
    result = _fold(temp_repo)

    assert result.source_branch == "branch_b"
    assert result.target_branch == "branch_a"
    # branch_a should have b.txt folded in
    switch_branch(temp_repo, "branch_a")
    assert (tmp_path / "b.txt").read_text() == "b content"


def test_fold_ignores_untracked_files(temp_repo: Repo, tmp_path: Path) -> None:
    """Fold should not stage or commit untracked files."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a: create a.txt
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b: create b.txt
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())

    # Create an untracked file (should not be touched by fold)
    (tmp_path / "untracked.txt").write_text("should not be committed")

    switch_branch(temp_repo, "branch_b")
    _fold(temp_repo)

    # The untracked file should still exist and not be committed
    assert (tmp_path / "untracked.txt").exists()
    assert (tmp_path / "untracked.txt").read_text() == "should not be committed"

    # Verify it's still untracked (not in the commit tree)
    branch_a_head = git.get_branch_head(temp_repo, "branch_a")
    tree = temp_repo[temp_repo[branch_a_head].tree]
    tree_files = [entry.path.decode() for entry in tree.items()]
    assert "untracked.txt" not in tree_files


# --- no_verify tests ---


def test_fold_no_verify(temp_repo: Repo, tmp_path: Path) -> None:
    """_fold() with no_verify=True succeeds despite failing pre-commit hook."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())

    # Create a failing pre-commit hook
    hooks_dir = Path(temp_repo.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # With no_verify=True, should succeed despite failing hook
    switch_branch(temp_repo, "branch_b")
    result = _fold(temp_repo, no_verify=True)

    assert result.source_branch == "branch_b"
    assert result.target_branch == "branch_a"
    assert not git.branch_exists(temp_repo, "branch_b")
    # b.txt should be in branch_a
    assert (tmp_path / "b.txt").exists()


# --- CLI tests ---


def test_fold_cli_basic(repo_with_stack: Repo, tmp_path: Path) -> None:
    """CLI: sc fold folds current branch into parent."""
    switch_branch(repo_with_stack, "branch_b")
    os.chdir(tmp_path)

    result = runner.invoke(app, ["fold"])

    assert result.exit_code == 0
    assert "Folded 'branch_b' into 'branch_a'" in result.output


def test_fold_cli_into(repo_with_stack: Repo, tmp_path: Path) -> None:
    """CLI: sc fold --into works."""
    switch_branch(repo_with_stack, "branch_b")
    os.chdir(tmp_path)

    result = runner.invoke(app, ["fold", "--into", "branch_a"])

    assert result.exit_code == 0
    assert "Folded 'branch_b' into 'branch_a'" in result.output


def test_fold_cli_into_short(repo_with_stack: Repo, tmp_path: Path) -> None:
    """CLI: sc fold -i works."""
    switch_branch(repo_with_stack, "branch_b")
    os.chdir(tmp_path)

    result = runner.invoke(app, ["fold", "-i", "branch_a"])

    assert result.exit_code == 0
    assert "Folded 'branch_b' into 'branch_a'" in result.output


def test_fold_cli_error(repo_with_stack: Repo, tmp_path: Path) -> None:
    """CLI: error output and exit code on failure."""
    switch_branch(repo_with_stack, "branch_b")
    os.chdir(tmp_path)

    result = runner.invoke(app, ["fold", "--into", "nonexistent"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_fold_cli_with_reparent(temp_repo: Repo, tmp_path: Path) -> None:
    """CLI: shows re-parenting message."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # branch_a
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(temp_repo, "branch_a")
    (tmp_path / "a.txt").write_text("a content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "a.txt")])
    trailers_a = Trailers(parent_branch="main")
    porcelain.commit(temp_repo, message=trailers_a.apply_to("feat: branch a").encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # branch_b
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    switch_branch(temp_repo, "branch_b")
    (tmp_path / "b.txt").write_text("b content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "b.txt")])
    trailers_b = Trailers(parent_branch="branch_a")
    porcelain.commit(temp_repo, message=trailers_b.apply_to("feat: branch b").encode())
    branch_b_sha = temp_repo.refs[b"refs/heads/branch_b"]

    # branch_c
    temp_repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    switch_branch(temp_repo, "branch_c")
    (tmp_path / "c.txt").write_text("c content")
    porcelain.add(temp_repo, paths=[str(tmp_path / "c.txt")])
    trailers_c = Trailers(parent_branch="branch_b")
    porcelain.commit(temp_repo, message=trailers_c.apply_to("feat: branch c").encode())

    switch_branch(temp_repo, "branch_b")
    os.chdir(tmp_path)

    result = runner.invoke(app, ["fold"])

    assert result.exit_code == 0
    assert "Folded 'branch_b' into 'branch_a'" in result.output
    assert "Re-parented 'branch_c' to 'branch_a'" in result.output


def test_fold_cli_no_verify(repo_with_stack: Repo, tmp_path: Path) -> None:
    """CLI: sc fold --no-verify works."""
    switch_branch(repo_with_stack, "branch_b")
    os.chdir(tmp_path)

    # Create a failing pre-commit hook
    hooks_dir = Path(repo_with_stack.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    result = runner.invoke(app, ["fold", "--no-verify"])

    assert result.exit_code == 0
    assert "Folded 'branch_b' into 'branch_a'" in result.output


def test_fold_cli_no_verify_short(repo_with_stack: Repo, tmp_path: Path) -> None:
    """CLI: sc fold -n works."""
    switch_branch(repo_with_stack, "branch_b")
    os.chdir(tmp_path)

    # Create a failing pre-commit hook
    hooks_dir = Path(repo_with_stack.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    result = runner.invoke(app, ["fold", "-n"])

    assert result.exit_code == 0
    assert "Folded 'branch_b' into 'branch_a'" in result.output

"""Rebase operations."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pygit2

from shortcake._git._core import (
    DULWICH_ERRORS,
    Repo,
    _git_dir,
    _oid,
    _repo_workdir,
    branch_exists,
    format_worktree_path,
    get_branch_head,
    get_current_branch,
    get_head_sha,
    get_other_worktrees_for_branch,
    has_uncommitted_changes,
    open_repo,
    switch_branch,
    update_branch,
)

DULWICH_REBASE_ERRORS = (*DULWICH_ERRORS, OSError, ValueError, KeyError)


@dataclass
class RebaseResult:
    """Result of a rebase operation."""

    success: bool
    conflict: bool = False
    skipped_empty: bool = False
    error_output: str = ""


class RebaseFailure(RuntimeError):
    """Raised when a rebase operation fails."""


def get_merge_base(repo: Repo, commit1: bytes, commit2: bytes) -> bytes | None:
    """Get merge base of two commits.

    Returns the common ancestor of two commits, or None if no common ancestor.
    """
    oid1 = pygit2.Oid(hex=_oid(commit1))
    oid2 = pygit2.Oid(hex=_oid(commit2))
    try:
        result = repo.merge_base(oid1, oid2)
    except pygit2.GitError:
        return None
    if result is None:
        return None
    return str(result).encode()


def is_ancestor(repo: Repo, maybe_ancestor: bytes, descendant: bytes) -> bool:
    """Check if commit is ancestor of another.

    Returns True if maybe_ancestor is reachable from descendant.
    """
    if maybe_ancestor == descendant:
        return True

    merge_base = get_merge_base(repo, maybe_ancestor, descendant)
    return merge_base == maybe_ancestor


def get_rebase_commits(
    repo: Repo, head: bytes | str, merge_base: bytes | str
) -> list[bytes]:
    """Get commits to rebase in chronological order (oldest first).

    Shortcake restack supports linear history only. If a merge commit is
    encountered on the first-parent chain, or the merge base is not on that
    chain, this raises a ValueError.
    """
    head_hex = _oid(head)
    merge_base_hex = _oid(merge_base)

    if head_hex == merge_base_hex:
        return []

    head_oid = pygit2.Oid(hex=head_hex)
    merge_base_oid = pygit2.Oid(hex=merge_base_hex)

    commits: list[bytes] = []
    current = repo.get(head_oid)
    while True:
        if current.id == merge_base_oid:
            return list(reversed(commits))
        if len(current.parent_ids) > 1:
            raise ValueError(
                "Non-linear history detected (merge commit). "
                "Shortcake restack supports linear stacks only."
            )
        commits.append(str(current.id).encode())
        if not current.parent_ids:
            break
        current = repo.get(current.parent_ids[0])

    raise ValueError(
        "Merge base not found on first-parent chain. "
        "History may be non-linear or unrelated."
    )


def is_rebase_in_progress(repo: Repo) -> bool:
    """Check if git rebase is in progress."""
    git_dir = _git_dir(repo)
    return (
        (git_dir / "rebase-merge").exists()
        or (git_dir / "rebase-apply").exists()
        or (git_dir / "CHERRY_PICK_HEAD").exists()
    )


def get_cherry_pick_head(repo: Repo) -> bytes | None:
    """Return current CHERRY_PICK_HEAD, if any."""
    head_path = _git_dir(repo) / "CHERRY_PICK_HEAD"
    if not head_path.exists():
        return None
    data = head_path.read_bytes().strip()
    return data or None


def rebase_branch(repo: Repo, branch: str, onto: str, upstream: str) -> RebaseResult:
    """Rebase branch onto target using git rebase --onto.

    Uses native git rebase with --empty=drop to properly handle empty commits.

    Args:
        repo: The git repository
        branch: Branch to rebase
        onto: Target to rebase onto
        upstream: The upstream reference (commits after this are rebased)

    Returns:
        RebaseResult indicating success, conflict, or skipped empty commits
    """
    worktrees = get_other_worktrees_for_branch(repo, branch)
    if worktrees and get_current_branch(repo) != branch:
        return _rebase_branch_checked_out_elsewhere(
            repo, branch, onto, upstream, worktrees
        )

    try:
        switch_branch(repo, branch)
    except ValueError as error:
        # e.g. another worktree is mid-rebase on it, so lists it as detached
        return RebaseResult(success=False, error_output=str(error))
    return _run_rebase(
        repo, ["git", "rebase", "--onto", onto, upstream, branch, "--empty=drop"]
    )


def _rebase_branch_checked_out_elsewhere(
    repo: Repo, branch: str, onto: str, upstream: str, worktrees: list[Path]
) -> RebaseResult:
    """Rebase a branch that another worktree has checked out.

    git won't check a branch out in two worktrees, so replay its commits on a
    detached HEAD here, then move the branch from inside the worktree that has
    it, so that worktree's index and files follow. On conflict the rebase stops
    here, and finish_detached_rebase() moves the branch once it's resumed.
    """
    try:
        _check_worktree_can_follow(branch, worktrees)
        switch_branch(repo, branch, detach=True)
    except ValueError as error:
        return RebaseResult(success=False, error_output=str(error))

    result = _run_rebase(
        repo, ["git", "rebase", "--onto", onto, upstream, "--empty=drop"]
    )
    if not result.success:
        return result

    try:
        update_branch_and_worktree(repo, branch, get_head_sha(repo).decode())
    except ValueError as error:
        return RebaseResult(success=False, error_output=str(error))
    return result


def _check_worktree_can_follow(branch: str, worktrees: list[Path]) -> None:
    """Raise ValueError unless branch's worktree can safely move to new commits."""
    if len(worktrees) > 1:
        paths = ", ".join(format_worktree_path(path) for path in worktrees)
        raise ValueError(
            f"'{branch}' is checked out in multiple worktrees ({paths}). "
            "Leave it checked out in only one worktree."
        )

    path = worktrees[0]
    display_path = format_worktree_path(path)
    if not path.exists():
        raise ValueError(
            f"'{branch}' is checked out in missing worktree '{display_path}'. "
            "Run 'git worktree prune' first."
        )

    worktree = open_repo(path)
    if is_rebase_in_progress(worktree):
        raise ValueError(
            f"'{branch}' is checked out in '{display_path}', where a rebase or "
            "cherry-pick is in progress. Complete or abort it first."
        )
    if has_uncommitted_changes(worktree):
        raise ValueError(
            f"'{branch}' is checked out in '{display_path}', which has "
            "uncommitted changes. Commit or stash them first."
        )


def update_branch_and_worktree(repo: Repo, branch: str, sha_hex: str) -> None:
    """Point branch at sha_hex, moving any other worktree that has it checked out.

    A plain ref update would leave that worktree's index and files on the old
    commit, so move the branch from inside it with `git reset --keep`, which
    refuses rather than overwrite local changes. Raises ValueError on failure.
    """
    worktrees = get_other_worktrees_for_branch(repo, branch)
    if not worktrees:
        update_branch(repo, branch, sha_hex)
        return
    if get_branch_head(repo, branch).decode() == sha_hex:
        return

    _check_worktree_can_follow(branch, worktrees)
    result = subprocess.run(
        ["git", "reset", "--keep", sha_hex],
        cwd=worktrees[0],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"Could not move '{branch}' in worktree "
            f"'{format_worktree_path(worktrees[0])}': {result.stderr.strip()}"
        )


def finish_detached_rebase(repo: Repo, branch: str, onto: str) -> None:
    """Move branch to a resumed rebase's result left on a detached HEAD.

    See _rebase_branch_checked_out_elsewhere(). Does nothing unless HEAD is
    detached on a commit that sits on top of onto.
    """
    if get_current_branch(repo) is not None or not branch_exists(repo, onto):
        return
    head = get_head_sha(repo)
    if is_ancestor(repo, get_branch_head(repo, onto), head):
        update_branch_and_worktree(repo, branch, head.decode())


def _run_rebase(repo: Repo, cmd: list[str]) -> RebaseResult:
    """Run a git rebase command and classify how it ended."""
    result = subprocess.run(
        cmd,
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # Check if any commits were dropped due to being empty
        skipped = "dropping" in result.stderr.lower()
        return RebaseResult(success=True, skipped_empty=skipped)

    if is_rebase_in_progress(repo):
        return RebaseResult(success=False, conflict=True, error_output=result.stderr)

    return RebaseResult(success=False, error_output=result.stderr)


def rebase_continue(repo: Repo) -> RebaseResult:
    """Continue an in-progress git rebase.

    Handles the case where conflict resolution results in no changes
    (empty commit) by automatically skipping.

    Returns:
        RebaseResult indicating success, conflict, or skipped empty commits
    """
    result = subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )

    if result.returncode == 0:
        return RebaseResult(success=True)

    # Empty commit after conflict resolution - auto skip
    combined_output = result.stderr + result.stdout
    if "nothing to commit" in combined_output:
        skip_result = subprocess.run(
            ["git", "rebase", "--skip"],
            cwd=_repo_workdir(repo),
            capture_output=True,
            text=True,
        )
        if skip_result.returncode == 0:
            return RebaseResult(success=True, skipped_empty=True)
        # Skip failed, check if still in rebase
        if is_rebase_in_progress(repo):
            return RebaseResult(
                success=False, conflict=True, error_output=skip_result.stderr
            )
        return RebaseResult(success=False, error_output=skip_result.stderr)

    if is_rebase_in_progress(repo):
        return RebaseResult(success=False, conflict=True, error_output=result.stderr)

    return RebaseResult(success=False, error_output=result.stderr)


def rebase_abort(repo: Repo) -> None:
    """Abort an in-progress rebase or cherry-pick."""
    import shutil

    git_dir = _git_dir(repo)
    rebase_merge = git_dir / "rebase-merge"
    rebase_apply = git_dir / "rebase-apply"
    cherry_pick_head = git_dir / "CHERRY_PICK_HEAD"

    # Check for git's native rebase state first
    if rebase_merge.exists() or rebase_apply.exists():
        result = subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=_repo_workdir(repo),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # git rebase --abort failed, likely corrupted state
            # Clean up the rebase directories manually
            if rebase_merge.exists():
                shutil.rmtree(rebase_merge)
            if rebase_apply.exists():  # pragma: no cover
                shutil.rmtree(rebase_apply)
        return

    # Fall back to cherry-pick abort via git CLI
    if cherry_pick_head.exists():
        result = subprocess.run(
            ["git", "cherry-pick", "--abort"],
            cwd=_repo_workdir(repo),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RebaseFailure(result.stderr or "Cherry-pick abort failed")
    else:  # pragma: no cover
        raise RebaseFailure("No rebase in progress.")


def cherry_pick(repo: Repo, commit: bytes) -> None:
    """Cherry-pick a commit onto the current branch."""
    result = subprocess.run(
        ["git", "cherry-pick", _oid(commit)],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RebaseFailure(result.stderr or "Cherry-pick failed")

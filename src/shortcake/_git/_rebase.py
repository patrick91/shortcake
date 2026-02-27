"""Rebase operations."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dulwich import porcelain
from dulwich.graph import find_merge_base
from dulwich.repo import Repo

from shortcake._git._core import (
    DULWICH_ERRORS,
    switch_branch,
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
    """Raised when a dulwich rebase operation fails."""


def get_merge_base(repo: Repo, commit1: bytes, commit2: bytes) -> bytes | None:
    """Get merge base of two commits using dulwich.

    Returns the common ancestor of two commits, or None if no common ancestor.
    """
    bases = find_merge_base(repo, [commit1, commit2])
    return bases[0] if bases else None


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
    head_bytes = head.encode() if isinstance(head, str) else head
    merge_base_bytes = (
        merge_base.encode() if isinstance(merge_base, str) else merge_base
    )

    if head_bytes == merge_base_bytes:
        return []

    commits: list[bytes] = []
    current = repo[head_bytes]
    while True:
        if current.id == merge_base_bytes:
            return list(reversed(commits))
        if len(current.parents) > 1:
            raise ValueError(
                "Non-linear history detected (merge commit). "
                "Shortcake restack supports linear stacks only."
            )
        commits.append(current.id)
        if not current.parents:
            break
        current = repo[current.parents[0]]

    raise ValueError(
        "Merge base not found on first-parent chain. "
        "History may be non-linear or unrelated."
    )


def is_rebase_in_progress(repo: Repo) -> bool:
    """Check if git rebase is in progress."""
    git_dir = Path(repo.controldir())
    return (
        (git_dir / "rebase-merge").exists()
        or (git_dir / "rebase-apply").exists()
        or (git_dir / "CHERRY_PICK_HEAD").exists()
    )


def get_cherry_pick_head(repo: Repo) -> bytes | None:
    """Return current CHERRY_PICK_HEAD, if any."""
    head_path = Path(repo.controldir()) / "CHERRY_PICK_HEAD"
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
    switch_branch(repo, branch)

    result = subprocess.run(
        ["git", "rebase", "--onto", onto, upstream, branch, "--empty=drop"],
        cwd=repo.path,
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
        cwd=repo.path,
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
            cwd=repo.path,
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

    git_dir = Path(repo.controldir())
    rebase_merge = git_dir / "rebase-merge"
    rebase_apply = git_dir / "rebase-apply"
    cherry_pick_head = git_dir / "CHERRY_PICK_HEAD"

    # Check for git's native rebase state first
    if rebase_merge.exists() or rebase_apply.exists():
        result = subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=repo.path,
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
            cwd=repo.path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RebaseFailure(result.stderr or "Cherry-pick abort failed")
    else:  # pragma: no cover
        raise RebaseFailure("No rebase in progress.")


def cherry_pick(repo: Repo, commit: bytes) -> None:
    """Cherry-pick a commit onto the current branch."""
    try:
        porcelain.cherry_pick(repo, commit)
    except DULWICH_REBASE_ERRORS as e:
        raise RebaseFailure(str(e) or "Cherry-pick failed") from e

"""Rebase operations."""

import subprocess
from pathlib import Path

from dulwich import porcelain
from dulwich.graph import find_merge_base
from dulwich.repo import Repo

from shortcake._git._core import (
    DULWICH_ERRORS,
    get_branch_head,
    switch_branch,
)

DULWICH_REBASE_ERRORS = (*DULWICH_ERRORS, OSError, ValueError, KeyError)


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


def rebase_branch(repo: Repo, branch: str, onto: str, upstream: str) -> None:
    """Rebase branch onto target using dulwich cherry-pick."""
    try:
        head = get_branch_head(repo, branch)
        commits = get_rebase_commits(repo, head, upstream)
        switch_branch(repo, branch)
        porcelain.reset(repo, mode="hard", treeish=onto)
        for commit in commits:
            porcelain.cherry_pick(repo, commit)
    except DULWICH_REBASE_ERRORS as e:
        raise RebaseFailure(str(e) or "Dulwich rebase failed") from e


def rebase_continue(repo: Repo) -> None:
    """Continue an in-progress cherry-pick rebase."""
    try:
        if get_cherry_pick_head(repo) is not None:
            porcelain.cherry_pick(repo, None, continue_=True)
        else:
            raise RebaseFailure("No cherry-pick in progress.")
    except DULWICH_REBASE_ERRORS as e:
        raise RebaseFailure(str(e) or "Rebase continue failed") from e


def rebase_abort(repo: Repo) -> None:
    """Abort an in-progress rebase (either git native or dulwich cherry-pick)."""
    import shutil

    git_dir = Path(repo.controldir())
    rebase_merge = git_dir / "rebase-merge"
    rebase_apply = git_dir / "rebase-apply"

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

    # Fall back to dulwich cherry-pick abort
    try:
        if get_cherry_pick_head(repo) is not None:
            porcelain.cherry_pick(repo, None, abort=True)
        else:  # pragma: no cover
            raise RebaseFailure("No rebase in progress.")
    except DULWICH_REBASE_ERRORS as e:
        raise RebaseFailure(str(e) or "Rebase abort failed") from e


def cherry_pick(repo: Repo, commit: bytes) -> None:
    """Cherry-pick a commit onto the current branch."""
    try:
        porcelain.cherry_pick(repo, commit)
    except DULWICH_REBASE_ERRORS as e:
        raise RebaseFailure(str(e) or "Cherry-pick failed") from e

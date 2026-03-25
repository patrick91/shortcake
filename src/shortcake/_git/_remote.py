"""Remote operations."""

import subprocess

import pygit2

from shortcake._git._core import Repo, _oid, _repo_workdir, get_current_branch
from shortcake._git._pygit2 import fetch_remote, get_remote_url
from shortcake._git._rebase import is_ancestor


def get_remote_ref(repo: Repo, remote_branch: str) -> bytes | None:
    """Get SHA of remote ref like origin/branch_a."""
    ref = repo.references.get(f"refs/remotes/{remote_branch}")
    if ref is None:
        return None
    return str(ref.target).encode()


def has_remote(repo: Repo, remote_name: str = "origin") -> bool:
    """Check if a remote is configured."""
    return get_remote_url(repo, remote_name) is not None


def _fast_forward_checked_out_branch(repo: Repo, branch: str) -> bool:
    """Fast-forward the currently checked-out branch via git CLI.

    Updating a checked-out branch ref directly leaves the index and worktree at
    the old tree. Git then sees the old tree as staged changes against the new
    HEAD, which breaks later branch switches during sync.
    """
    result = subprocess.run(
        ["git", "merge", "--ff-only", f"origin/{branch}"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def fetch_and_fast_forward_trunk(repo: Repo, trunk: str) -> tuple[bool, str | None]:
    """Fetch from origin and fast-forward trunk.

    Returns (success, new_sha_short) where new_sha_short is the short SHA
    if trunk was fast-forwarded, or None if already up to date or failed.
    """
    # Check if origin remote exists before trying to fetch
    if not has_remote(repo, "origin"):
        return True, None  # No remote configured, nothing to do

    if not fetch_remote(repo, "origin"):  # pragma: no cover
        return False, None

    remote_ref_name = f"refs/remotes/origin/{trunk}"  # pragma: no cover
    local_ref_name = f"refs/heads/{trunk}"  # pragma: no cover

    remote_ref = repo.references.get(remote_ref_name)  # pragma: no cover
    if remote_ref is None:  # pragma: no cover
        return True, None  # No remote ref, nothing to do

    remote_sha = str(remote_ref.target).encode()  # pragma: no cover
    local_ref = repo.references.get(local_ref_name)  # pragma: no cover
    local_sha = (
        str(local_ref.target).encode() if local_ref else None
    )  # pragma: no cover

    if local_sha == remote_sha:  # pragma: no cover
        return True, None  # Already up to date

    # Check if we can fast-forward (local is ancestor of remote)
    if local_sha and not is_ancestor(repo, local_sha, remote_sha):  # pragma: no cover
        return False, None  # Diverged, can't fast-forward

    if get_current_branch(repo) == trunk:
        if not _fast_forward_checked_out_branch(repo, trunk):
            return False, None
    else:
        # Fast-forward: update local ref without touching the current worktree.
        remote_oid = pygit2.Oid(hex=_oid(remote_sha))  # pragma: no cover
        if local_ref:  # pragma: no cover
            local_ref.set_target(remote_oid)
        else:  # pragma: no cover
            repo.references.create(local_ref_name, remote_oid)

    return True, _oid(remote_sha)[:7]  # pragma: no cover

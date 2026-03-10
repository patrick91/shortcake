"""Remote operations."""

from dulwich.repo import Repo

from shortcake._git._pygit2 import fetch_remote, get_remote_url
from shortcake._git._rebase import is_ancestor


def get_remote_ref(repo: Repo, remote_branch: str) -> bytes | None:
    """Get SHA of remote ref like origin/branch_a."""
    full_ref = f"refs/remotes/{remote_branch}".encode()
    try:
        return repo.refs[full_ref]
    except KeyError:
        return None


def has_remote(repo: Repo, remote_name: str = "origin") -> bool:
    """Check if a remote is configured."""
    return get_remote_url(repo, remote_name) is not None


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

    remote_ref = f"refs/remotes/origin/{trunk}".encode()  # pragma: no cover
    local_ref = f"refs/heads/{trunk}".encode()  # pragma: no cover

    if remote_ref not in repo.refs:  # pragma: no cover
        return True, None  # No remote ref, nothing to do

    remote_sha = repo.refs[remote_ref]  # pragma: no cover
    local_sha = repo.refs[local_ref]  # pragma: no cover

    if local_sha == remote_sha:  # pragma: no cover
        return True, None  # Already up to date

    # Check if we can fast-forward (local is ancestor of remote)
    if not is_ancestor(repo, local_sha, remote_sha):  # pragma: no cover
        return False, None  # Diverged, can't fast-forward

    repo.refs[local_ref] = remote_sha  # pragma: no cover
    return True, remote_sha[:7].decode()  # pragma: no cover

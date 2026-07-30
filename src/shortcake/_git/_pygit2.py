"""Shared pygit2 helpers."""

import subprocess
from typing import Any

import pygit2

type Repo = Any


def get_remote_url(repo: Repo, remote_name: str = "origin") -> str | None:
    """Return a configured remote URL, or None when the remote is missing.

    pygit2 resolves url.<base>.insteadOf rewrites, so this is the effective
    transport URL, not necessarily the URL written in the config.
    """
    try:
        return repo.remotes[remote_name].url
    except KeyError:
        return None


def get_remote_raw_url(repo: Repo, remote_name: str = "origin") -> str | None:
    """Return the remote URL exactly as written in the git config.

    Unlike get_remote_url, url.<base>.insteadOf rewrites are not applied.
    """
    try:
        return repo.config[f"remote.{remote_name}.url"]
    except KeyError:
        return None


def fetch_remote(repo: Repo, remote_name: str = "origin") -> bool:
    """Fetch a remote and report whether it succeeded.

    Prunes: a plain fetch leaves ``refs/remotes/<remote>/<branch>`` in place
    after the branch is deleted on the remote, so the ref lingers indefinitely
    and anything reading it — ``get_remote_ref``, merged-branch detection —
    believes a branch still exists upstream when it does not. Pruning only
    removes remote-tracking refs whose branch is gone; local branches are
    untouched.

    Tries pygit2 first, falls back to git CLI if pygit2 fails (e.g. SSH
    auth not available to libgit2).
    """
    try:
        repo.remotes[remote_name].fetch(prune=pygit2.enums.FetchPrune.PRUNE)
        return True
    except (KeyError, pygit2.GitError):
        pass

    # Fallback: use git CLI which handles SSH agent, credential helpers, etc.
    repo_path = repo.workdir or repo.path
    try:
        subprocess.run(
            ["git", "fetch", "--prune", remote_name],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True

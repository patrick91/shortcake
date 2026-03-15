"""Shared pygit2 helpers."""

import subprocess
from typing import Any

import pygit2

type Repo = Any


def get_remote_url(repo: Repo, remote_name: str = "origin") -> str | None:
    """Return a configured remote URL, or None when the remote is missing."""
    try:
        return repo.remotes[remote_name].url
    except KeyError:
        return None


def fetch_remote(repo: Repo, remote_name: str = "origin") -> bool:
    """Fetch a remote and report whether it succeeded.

    Tries pygit2 first, falls back to git CLI if pygit2 fails (e.g. SSH
    auth not available to libgit2).
    """
    try:
        repo.remotes[remote_name].fetch()
        return True
    except (KeyError, pygit2.GitError):
        pass

    # Fallback: use git CLI which handles SSH agent, credential helpers, etc.
    repo_path = repo.workdir or repo.path
    try:
        subprocess.run(
            ["git", "fetch", remote_name],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True

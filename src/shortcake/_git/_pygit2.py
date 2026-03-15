"""Shared pygit2 helpers."""

import subprocess
from pathlib import Path
from typing import Any

import pygit2

type Repo = Any


def _pygit2_repo(repo: Repo) -> pygit2.Repository:
    """Ensure we have a pygit2.Repository (passthrough if already one)."""
    if isinstance(repo, pygit2.Repository):
        return repo
    # Fallback for any legacy callers
    repo_path = repo if isinstance(repo, Path) else Path(repo.path)
    git_dir = pygit2.discover_repository(str(repo_path))
    assert git_dir is not None
    return pygit2.Repository(git_dir)


def get_remote_url(repo: Repo | Path, remote_name: str = "origin") -> str | None:
    """Return a configured remote URL, or None when the remote is missing."""
    try:
        r = _pygit2_repo(repo) if not isinstance(repo, Path) else None
        if r is None:
            git_dir = pygit2.discover_repository(str(repo))
            assert git_dir is not None
            r = pygit2.Repository(git_dir)
        return r.remotes[remote_name].url
    except KeyError:
        return None


def fetch_remote(repo: Repo | Path, remote_name: str = "origin") -> bool:
    """Fetch a remote and report whether it succeeded.

    Tries pygit2 first, falls back to git CLI if pygit2 fails (e.g. SSH
    auth not available to libgit2).
    """
    r = _pygit2_repo(repo) if not isinstance(repo, Path) else None
    if r is None:
        git_dir = pygit2.discover_repository(str(repo))
        assert git_dir is not None
        r = pygit2.Repository(git_dir)

    try:
        r.remotes[remote_name].fetch()
        return True
    except (KeyError, pygit2.GitError):
        pass

    # Fallback: use git CLI which handles SSH agent, credential helpers, etc.
    repo_path = r.workdir or r.path
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

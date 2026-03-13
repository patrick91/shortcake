"""Shared pygit2 helpers for incremental git backend migration."""

from pathlib import Path
from typing import Any

import pygit2

type Repo = Any


def open_pygit2_repo(repo: Repo | Path) -> pygit2.Repository:
    """Open a repository with pygit2 from a dulwich repo or filesystem path."""
    repo_path = repo if isinstance(repo, Path) else Path(repo.path)
    git_dir = pygit2.discover_repository(str(repo_path))
    assert git_dir is not None
    return pygit2.Repository(git_dir)


def get_remote_url(repo: Repo | Path, remote_name: str = "origin") -> str | None:
    """Return a configured remote URL, or None when the remote is missing."""
    try:
        return open_pygit2_repo(repo).remotes[remote_name].url
    except KeyError:
        return None


def fetch_remote(repo: Repo | Path, remote_name: str = "origin") -> bool:
    """Fetch a remote and report whether it succeeded."""
    try:
        open_pygit2_repo(repo).remotes[remote_name].fetch()
    except (KeyError, pygit2.GitError):
        return False
    return True

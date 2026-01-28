"""PR cache for storing GitHub PR info locally."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from dulwich.repo import Repo


@dataclass
class CachedPRInfo:
    """Cached PR information for a branch."""

    number: int
    is_draft: bool = False
    is_merged: bool = False
    url: str | None = None


def _get_cache_path(repo: Repo) -> Path:
    """Get path to PR cache file."""
    git_dir = Path(repo.controldir())
    cache_dir = git_dir / "shortcake"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / "pr-cache.json"


def load_pr_cache(repo: Repo) -> dict[str, CachedPRInfo]:
    """Load PR cache from disk.

    Returns:
        Dict mapping branch name to CachedPRInfo.
    """
    cache_path = _get_cache_path(repo)
    if not cache_path.exists():
        return {}

    try:
        with open(cache_path) as f:
            data = json.load(f)
        return {branch: CachedPRInfo(**info) for branch, info in data.items()}
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        return {}


def save_pr_cache(repo: Repo, cache: dict[str, CachedPRInfo]) -> None:
    """Save PR cache to disk.

    Args:
        repo: The repository.
        cache: Dict mapping branch name to CachedPRInfo.
    """
    cache_path = _get_cache_path(repo)
    try:
        with open(cache_path, "w") as f:
            json.dump(
                {branch: asdict(info) for branch, info in cache.items()},
                f,
                indent=2,
            )
    except OSError:
        pass  # Cache write failure is non-fatal


def update_pr_cache(
    repo: Repo,
    branch: str,
    pr_number: int,
    is_draft: bool = False,
    is_merged: bool = False,
    url: str | None = None,
) -> None:
    """Update cache for a single branch.

    Args:
        repo: The repository.
        branch: Branch name.
        pr_number: PR number.
        is_draft: Whether the PR is a draft.
        is_merged: Whether the PR is merged.
        url: PR URL for clickable links.
    """
    cache = load_pr_cache(repo)
    cache[branch] = CachedPRInfo(
        number=pr_number,
        is_draft=is_draft,
        is_merged=is_merged,
        url=url,
    )
    save_pr_cache(repo, cache)


def remove_from_pr_cache(repo: Repo, branch: str) -> None:
    """Remove a branch from the cache.

    Args:
        repo: The repository.
        branch: Branch name to remove.
    """
    cache = load_pr_cache(repo)
    if branch in cache:
        del cache[branch]
        save_pr_cache(repo, cache)

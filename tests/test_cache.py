"""Tests for PR cache module."""

from pathlib import Path
from typing import TYPE_CHECKING

from dulwich.repo import Repo

if TYPE_CHECKING:
    import pytest

from shortcake._cache import (
    CachedPRInfo,
    load_pr_cache,
    remove_from_pr_cache,
    save_pr_cache,
    update_pr_cache,
)


def test_load_cache_empty(temp_repo: Repo) -> None:
    """Test loading cache when no cache file exists."""
    cache = load_pr_cache(temp_repo)
    assert cache == {}


def test_save_and_load_cache(temp_repo: Repo) -> None:
    """Test saving and loading cache."""
    cache = {
        "feature-a": CachedPRInfo(number=123, is_draft=False, is_merged=False),
        "feature-b": CachedPRInfo(number=456, is_draft=True, is_merged=False),
    }

    save_pr_cache(temp_repo, cache)
    loaded = load_pr_cache(temp_repo)

    assert loaded["feature-a"].number == 123
    assert loaded["feature-a"].is_draft is False
    assert loaded["feature-b"].number == 456
    assert loaded["feature-b"].is_draft is True


def test_update_pr_cache(temp_repo: Repo) -> None:
    """Test updating cache for a single branch."""
    update_pr_cache(temp_repo, "feature", 789, is_draft=True)

    cache = load_pr_cache(temp_repo)
    assert "feature" in cache
    assert cache["feature"].number == 789
    assert cache["feature"].is_draft is True
    assert cache["feature"].is_merged is False


def test_update_pr_cache_merged(temp_repo: Repo) -> None:
    """Test updating cache with merged PR."""
    update_pr_cache(temp_repo, "feature", 100, is_merged=True)

    cache = load_pr_cache(temp_repo)
    assert cache["feature"].number == 100
    assert cache["feature"].is_merged is True


def test_update_pr_cache_overwrites(temp_repo: Repo) -> None:
    """Test that update overwrites existing entry."""
    update_pr_cache(temp_repo, "feature", 123, is_draft=False)
    update_pr_cache(temp_repo, "feature", 456, is_draft=True)

    cache = load_pr_cache(temp_repo)
    assert cache["feature"].number == 456
    assert cache["feature"].is_draft is True


def test_remove_from_pr_cache(temp_repo: Repo) -> None:
    """Test removing a branch from cache."""
    update_pr_cache(temp_repo, "feature-a", 123)
    update_pr_cache(temp_repo, "feature-b", 456)

    remove_from_pr_cache(temp_repo, "feature-a")

    cache = load_pr_cache(temp_repo)
    assert "feature-a" not in cache
    assert "feature-b" in cache


def test_remove_from_pr_cache_nonexistent(temp_repo: Repo) -> None:
    """Test removing nonexistent branch from cache doesn't error."""
    remove_from_pr_cache(temp_repo, "nonexistent")
    # Should not raise


def test_cache_file_location(temp_repo: Repo) -> None:
    """Test cache file is created in .git/shortcake/."""
    update_pr_cache(temp_repo, "feature", 123)

    # repo.path is the .git directory
    cache_path = Path(temp_repo.path) / "shortcake" / "pr-cache.json"
    assert cache_path.exists()


def test_load_cache_invalid_json(temp_repo: Repo) -> None:
    """Test loading cache handles invalid JSON gracefully."""
    cache_dir = Path(temp_repo.path) / "shortcake"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "pr-cache.json"
    cache_file.write_text("not valid json")

    cache = load_pr_cache(temp_repo)
    assert cache == {}


def test_load_cache_invalid_structure(temp_repo: Repo) -> None:
    """Test loading cache handles invalid structure gracefully."""
    cache_dir = Path(temp_repo.path) / "shortcake"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "pr-cache.json"
    cache_file.write_text('{"feature": "not a dict"}')

    cache = load_pr_cache(temp_repo)
    assert cache == {}


def test_cached_pr_info_defaults() -> None:
    """Test CachedPRInfo default values."""
    info = CachedPRInfo(number=123)
    assert info.number == 123
    assert info.is_draft is False
    assert info.is_merged is False


def test_save_cache_oserror(temp_repo: Repo, monkeypatch: "pytest.MonkeyPatch") -> None:
    """Test save_pr_cache handles OSError gracefully."""
    import builtins

    original_open = builtins.open

    def mock_open(*args, **kwargs):
        if "w" in args[1] if len(args) > 1 else kwargs.get("mode", ""):
            raise OSError("Permission denied")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", mock_open)

    # Should not raise, just silently fail
    cache = {"feature": CachedPRInfo(number=123)}
    save_pr_cache(temp_repo, cache)
    # No assertion needed - we just verify it doesn't raise

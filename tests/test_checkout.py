"""Tests for checkout command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.checkout import (
    CheckoutError,
    _checkout,
    _create_branch_from_remote,
    _fetch_branch,
)


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


# Tests for _checkout with local branches


def test_checkout_local_branch_exists(repo_with_feature: Repo) -> None:
    """Test checking out an existing local branch."""
    # Switch to main first
    switch_branch(repo_with_feature, "main")

    result = _checkout(repo_with_feature, "feature", adopt=False)

    assert result.branch == "feature"
    assert result.from_remote is False
    assert result.adopted is False
    assert result.pr_number is None

    # Verify we're on feature branch (HEAD points to feature)
    assert repo_with_feature.refs.read_ref(b"HEAD") == b"ref: refs/heads/feature"


def test_checkout_local_branch_adopts_untracked(repo_with_feature: Repo) -> None:
    """Test checkout adopts untracked branches by default."""
    # Switch to main first
    switch_branch(repo_with_feature, "main")

    result = _checkout(repo_with_feature, "feature", adopt=True)

    assert result.branch == "feature"
    assert result.adopted is True

    # Verify trailer was added
    head = git.get_branch_head(repo_with_feature, "feature")
    message = git.get_commit_message(repo_with_feature, head)
    trailers = Trailers.from_message(message)
    assert trailers.parent_branch == "main"


def test_checkout_local_branch_already_tracked(
    repo_with_tracked_feature: Repo,
) -> None:
    """Test checkout doesn't re-adopt already tracked branches."""
    # Switch to main first
    switch_branch(repo_with_tracked_feature, "main")

    result = _checkout(repo_with_tracked_feature, "feature", adopt=True)

    assert result.branch == "feature"
    assert result.adopted is False  # Already tracked, no adoption needed


def test_checkout_local_branch_no_adopt_flag(repo_with_feature: Repo) -> None:
    """Test checkout with adopt=False skips adoption."""
    # Switch to main first
    switch_branch(repo_with_feature, "main")

    result = _checkout(repo_with_feature, "feature", adopt=False)

    assert result.branch == "feature"
    assert result.adopted is False


def test_checkout_default_branch_not_adopted(temp_repo: Repo) -> None:
    """Test checkout of default branch doesn't try to adopt it."""
    # Create feature and switch to it
    temp_repo.refs[b"refs/heads/feature"] = temp_repo.head()
    switch_branch(temp_repo, "feature")

    # Checkout main - should not try to adopt
    result = _checkout(temp_repo, "main", adopt=True)

    assert result.branch == "main"
    assert result.adopted is False


# Tests for _checkout with remote branches


def test_checkout_remote_branch_not_found(temp_repo: Repo) -> None:
    """Test error when branch doesn't exist locally or on remote."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Mock fetch to fail
    with (
        patch("shortcake.commands.checkout._fetch_branch", return_value=False),
        pytest.raises(CheckoutError, match="not found locally or on remote"),
    ):
        _checkout(temp_repo, "nonexistent-branch")


def test_checkout_branch_no_remote(temp_repo: Repo) -> None:
    """Test error when branch doesn't exist and no remote configured."""
    with pytest.raises(CheckoutError, match="no remote configured"):
        _checkout(temp_repo, "nonexistent-branch")


def test_checkout_from_remote_creates_local(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout creates local branch from remote."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Simulate remote ref exists
    remote_sha = temp_repo.head()
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = remote_sha

    with patch("shortcake.commands.checkout._fetch_branch", return_value=True):
        result = _checkout(temp_repo, "remote-feature", adopt=False)

    assert result.branch == "remote-feature"
    assert result.from_remote is True
    assert result.adopted is False

    # Verify local branch was created
    assert b"refs/heads/remote-feature" in temp_repo.refs


# Tests for _checkout with PR numbers


@respx.mock
def test_checkout_by_pr_number(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test checkout by PR number resolves branch name."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Set up GitHub token
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Create the branch locally
    temp_repo.refs[b"refs/heads/pr-feature"] = temp_repo.head()

    # Mock GitHub API
    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 42,
                "html_url": "https://github.com/owner/repo/pull/42",
                "base": {"ref": "main"},
                "head": {"ref": "pr-feature"},
                "title": "Test PR",
                "body": "",
                "state": "open",
                "draft": False,
            },
        )
    )

    result = _checkout(temp_repo, "42", adopt=False)

    assert result.branch == "pr-feature"
    assert result.pr_number == 42


@respx.mock
def test_checkout_by_pr_number_not_found(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when PR number doesn't exist."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    respx.get("https://api.github.com/repos/owner/repo/pulls/999").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(CheckoutError, match="PR #999 not found"):
        _checkout(temp_repo, "999")


def test_checkout_by_pr_number_no_token(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when checking out by PR number without GitHub token."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Mock gh auth to return nothing
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with (
        patch.object(Path, "home", return_value=Path("/nonexistent")),
        patch("subprocess.run", return_value=mock_result),
        pytest.raises(CheckoutError, match="without GitHub token"),
    ):
        _checkout(temp_repo, "42")


def test_checkout_by_pr_number_no_remote(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when checking out by PR number without origin remote."""
    monkeypatch.setenv("GH_TOKEN", "test-token")

    with pytest.raises(CheckoutError, match="Cannot determine GitHub repo"):
        _checkout(temp_repo, "42")


@respx.mock
def test_checkout_by_pr_number_no_head_ref(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when PR has no head branch (fork)."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 42,
                "html_url": "https://github.com/owner/repo/pull/42",
                "base": {"ref": "main"},
                "head": {"ref": None},  # No head ref (fork)
                "title": "Test PR",
                "body": "",
                "state": "open",
                "draft": False,
            },
        )
    )

    with pytest.raises(CheckoutError, match="no head branch"):
        _checkout(temp_repo, "42")


@respx.mock
def test_checkout_by_pr_number_api_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error handling for GitHub API errors."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )

    with pytest.raises(CheckoutError, match="GitHub API error: 500"):
        _checkout(temp_repo, "42")


# Tests for helper functions


def test_fetch_branch_success(temp_repo: Repo) -> None:
    """Test _fetch_branch returns True on success."""
    with patch("shortcake.commands.checkout.porcelain.fetch") as mock_fetch:
        result = _fetch_branch(temp_repo, "feature")

    assert result is True
    mock_fetch.assert_called_once()


def test_fetch_branch_failure(temp_repo: Repo) -> None:
    """Test _fetch_branch returns False on failure."""
    with patch(
        "shortcake.commands.checkout.porcelain.fetch",
        side_effect=Exception("fetch failed"),
    ):
        result = _fetch_branch(temp_repo, "feature")

    assert result is False


def test_create_branch_from_remote_success(temp_repo: Repo) -> None:
    """Test _create_branch_from_remote creates local branch."""
    # Create remote tracking ref
    temp_repo.refs[b"refs/remotes/origin/feature"] = temp_repo.head()

    result = _create_branch_from_remote(temp_repo, "feature")

    assert result is True
    assert b"refs/heads/feature" in temp_repo.refs


def test_create_branch_from_remote_no_remote_ref(temp_repo: Repo) -> None:
    """Test _create_branch_from_remote returns False when no remote ref."""
    result = _create_branch_from_remote(temp_repo, "nonexistent")

    assert result is False

"""Tests for checkout command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.commands.checkout import (
    CheckoutError,
    _checkout,
    _create_branch_from_remote,
    _fetch_branch,
    checkout,  # noqa: F401 - imported for coverage
    co,  # noqa: F401 - imported for coverage
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

    result = _checkout(repo_with_feature, "feature")

    assert result.branch == "feature"
    assert result.from_remote is False
    assert result.pr_number is None

    # Verify we're on feature branch (HEAD points to feature)
    assert repo_with_feature.refs.read_ref(b"HEAD") == b"ref: refs/heads/feature"


# Tests for _checkout with remote branches


def test_checkout_remote_branch_fetch_fails(temp_repo: Repo) -> None:
    """Test error when fetch fails."""
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


def test_checkout_remote_branch_not_on_remote(temp_repo: Repo) -> None:
    """Test error when fetch succeeds but branch not found on remote."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Mock fetch to succeed, but don't add the remote ref
    # so get_remote_ref returns None
    with (
        patch("shortcake.commands.checkout._fetch_branch", return_value=True),
        pytest.raises(CheckoutError, match="not found on remote"),
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
        result = _checkout(temp_repo, "remote-feature")

    assert result.branch == "remote-feature"
    assert result.from_remote is True

    # Verify local branch was created
    assert b"refs/heads/remote-feature" in temp_repo.refs


def test_checkout_from_remote_create_branch_fails(temp_repo: Repo) -> None:
    """Test error when _create_branch_from_remote fails."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Simulate remote ref exists
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = temp_repo.head()

    with (
        patch("shortcake.commands.checkout._fetch_branch", return_value=True),
        patch(
            "shortcake.commands.checkout._create_branch_from_remote", return_value=False
        ),
        pytest.raises(CheckoutError, match="Failed to create local branch"),
    ):
        _checkout(temp_repo, "remote-feature")


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

    result = _checkout(temp_repo, "42")

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
    # Mock at the transport level to let fetch() run but not actually connect
    with patch("shortcake.commands.checkout.porcelain.fetch") as mock_fetch:
        mock_fetch.return_value = {}
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


# CLI tests

runner = CliRunner()


def test_checkout_cli_local_branch(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test checkout CLI with local branch."""
    import os

    switch_branch(repo_with_feature, "main")

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "feature"])

    assert result.exit_code == 0
    assert "Switched to 'feature'" in result.output


def test_checkout_cli_error(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout CLI error handling."""
    import os

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "nonexistent"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_checkout_cli_uncommitted_changes(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test checkout CLI warns about uncommitted changes."""
    import os

    switch_branch(repo_with_feature, "main")

    # Create uncommitted changes
    (tmp_path / "uncommitted.txt").write_text("uncommitted")
    porcelain.add(repo_with_feature, paths=[str(tmp_path / "uncommitted.txt")])

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "feature"])

    assert result.exit_code == 0
    assert "Warning: You have uncommitted changes." in result.output


def test_co_alias(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test co alias works same as checkout."""
    import os

    switch_branch(repo_with_feature, "main")

    os.chdir(tmp_path)
    result = runner.invoke(app, ["co", "feature"])

    assert result.exit_code == 0
    assert "Switched to 'feature'" in result.output


@respx.mock
def test_checkout_cli_pr_output(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test checkout CLI output for PR checkout."""
    import os

    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Create the branch locally
    temp_repo.refs[b"refs/heads/pr-feature"] = temp_repo.head()

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

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "42"])

    assert result.exit_code == 0
    assert "Checked out PR #42 (pr-feature)" in result.output


def test_checkout_cli_remote_output(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout CLI output for remote checkout."""
    import os

    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Simulate remote ref exists
    remote_sha = temp_repo.head()
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = remote_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.checkout._fetch_branch", return_value=True):
        result = runner.invoke(app, ["checkout", "remote-feature"])

    assert result.exit_code == 0
    assert "Checked out 'remote-feature' from remote" in result.output

"""Tests for GitHub API client."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from dulwich.repo import Repo

from shortcake._github import (
    GitHubClient,
    get_github_token,
    get_repo_info,
)

# Tests for get_github_token


def test_get_github_token_from_gh_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test token retrieval from GH_TOKEN env var."""
    monkeypatch.setenv("GH_TOKEN", "test-token-gh")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    token = get_github_token()

    assert token == "test-token-gh"


def test_get_github_token_from_github_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test token retrieval from GITHUB_TOKEN env var."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-github")

    token = get_github_token()

    assert token == "test-token-github"


def test_get_github_token_gh_token_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test GH_TOKEN takes precedence over GITHUB_TOKEN."""
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")

    token = get_github_token()

    assert token == "gh-token"


def test_get_github_token_from_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test token retrieval from gh config file."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Create mock config file
    config_dir = tmp_path / ".config" / "gh"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "hosts.yml"
    config_file.write_text("github.com:\n  oauth_token: config-file-token\n")

    with patch.object(Path, "home", return_value=tmp_path):
        token = get_github_token()

    assert token == "config-file-token"


def test_get_github_token_from_gh_auth_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test token retrieval from gh auth token command."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "gh-auth-token\n"

    with (
        patch.object(Path, "home", return_value=Path("/nonexistent")),
        patch("subprocess.run", return_value=mock_result),
    ):
        token = get_github_token()

    assert token == "gh-auth-token"


def test_get_github_token_none_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test returns None when no token found."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with (
        patch.object(Path, "home", return_value=Path("/nonexistent")),
        patch("subprocess.run", return_value=mock_result),
    ):
        token = get_github_token()

    assert token is None


def test_get_github_token_config_file_invalid_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test handles invalid YAML in config file."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    config_dir = tmp_path / ".config" / "gh"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "hosts.yml"
    config_file.write_text("invalid: yaml: content:")

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with (
        patch.object(Path, "home", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result),
    ):
        token = get_github_token()

    assert token is None


def test_get_github_token_config_file_missing_github_com(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test handles config file without github.com entry."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    config_dir = tmp_path / ".config" / "gh"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "hosts.yml"
    config_file.write_text("gitlab.com:\n  token: gitlab-token\n")

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with (
        patch.object(Path, "home", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result),
    ):
        token = get_github_token()

    assert token is None


def test_get_github_token_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test handles subprocess timeout."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    import subprocess

    with (
        patch.object(Path, "home", return_value=Path("/nonexistent")),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 10)),
    ):
        token = get_github_token()

    assert token is None


def test_get_github_token_gh_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test handles gh CLI not installed."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with (
        patch.object(Path, "home", return_value=Path("/nonexistent")),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        token = get_github_token()

    assert token is None


# Tests for get_repo_info


def test_get_repo_info_ssh_format(temp_repo: Repo) -> None:
    """Test parsing SSH remote URL."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    result = get_repo_info(temp_repo)

    assert result == ("owner", "repo")


def test_get_repo_info_https_format(temp_repo: Repo) -> None:
    """Test parsing HTTPS remote URL."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"https://github.com/owner/repo.git")
    config.write_to_path()

    result = get_repo_info(temp_repo)

    assert result == ("owner", "repo")


def test_get_repo_info_https_no_git_extension(temp_repo: Repo) -> None:
    """Test parsing HTTPS URL without .git extension."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"https://github.com/owner/repo")
    config.write_to_path()

    result = get_repo_info(temp_repo)

    assert result == ("owner", "repo")


def test_get_repo_info_no_origin(temp_repo: Repo) -> None:
    """Test returns None when no origin remote."""
    result = get_repo_info(temp_repo)

    assert result is None


def test_get_repo_info_non_github_url(temp_repo: Repo) -> None:
    """Test returns None for non-GitHub URLs."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@gitlab.com:owner/repo.git")
    config.write_to_path()

    result = get_repo_info(temp_repo)

    assert result is None


# Tests for GitHubClient


def test_github_client_context_manager() -> None:
    """Test GitHubClient context manager."""
    with patch.object(httpx.Client, "close") as mock_close:
        with GitHubClient("token", "owner", "repo"):
            pass
        mock_close.assert_called_once()


def test_github_client_get_pr_for_branch_found() -> None:
    """Test finding existing PR for branch."""
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "number": 123,
            "html_url": "https://github.com/owner/repo/pull/123",
            "base": {"ref": "main"},
            "title": "Test PR",
            "body": "PR body",
            "state": "open",
            "draft": False,
        }
    ]
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "get", return_value=mock_response):
        client = GitHubClient("token", "owner", "repo")
        result = client.get_pr_for_branch("feature")

    assert result is not None
    assert result.number == 123
    assert result.url == "https://github.com/owner/repo/pull/123"
    assert result.base == "main"
    assert result.title == "Test PR"
    assert result.state == "open"
    assert result.is_draft is False


def test_github_client_get_pr_for_branch_not_found() -> None:
    """Test no PR found for branch."""
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "get", return_value=mock_response):
        client = GitHubClient("token", "owner", "repo")
        result = client.get_pr_for_branch("feature")

    assert result is None


def test_github_client_get_pr_for_branch_draft() -> None:
    """Test finding draft PR."""
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "number": 123,
            "html_url": "https://github.com/owner/repo/pull/123",
            "base": {"ref": "main"},
            "title": "Draft PR",
            "body": None,  # Test null body
            "state": "open",
            "draft": True,
        }
    ]
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "get", return_value=mock_response):
        client = GitHubClient("token", "owner", "repo")
        result = client.get_pr_for_branch("feature")

    assert result is not None
    assert result.is_draft is True
    assert result.body == ""


def test_github_client_create_pr() -> None:
    """Test creating a new PR."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "number": 456,
        "html_url": "https://github.com/owner/repo/pull/456",
        "base": {"ref": "main"},
        "title": "New Feature",
        "body": "Description",
        "state": "open",
        "draft": False,
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "post", return_value=mock_response):
        client = GitHubClient("token", "owner", "repo")
        result = client.create_pr(
            head="feature",
            base="main",
            title="New Feature",
            body="Description",
            draft=False,
        )

    assert result.number == 456
    assert result.url == "https://github.com/owner/repo/pull/456"


def test_github_client_create_pr_draft() -> None:
    """Test creating a draft PR."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "number": 789,
        "html_url": "https://github.com/owner/repo/pull/789",
        "base": {"ref": "main"},
        "title": "Draft Feature",
        "body": "",
        "state": "open",
        "draft": True,
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "post", return_value=mock_response) as mock_post:
        client = GitHubClient("token", "owner", "repo")
        result = client.create_pr(
            head="feature",
            base="main",
            title="Draft Feature",
            body="",
            draft=True,
        )

    assert result.is_draft is True
    # Verify draft=True was passed
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["json"]["draft"] is True


def test_github_client_update_pr() -> None:
    """Test updating a PR."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "patch", return_value=mock_response) as mock_patch:
        client = GitHubClient("token", "owner", "repo")
        client.update_pr(123, base="develop", body="Updated body")

    mock_patch.assert_called_once()
    call_kwargs = mock_patch.call_args[1]
    assert call_kwargs["json"]["base"] == "develop"
    assert call_kwargs["json"]["body"] == "Updated body"


def test_github_client_update_pr_partial() -> None:
    """Test updating only some PR fields."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "patch", return_value=mock_response) as mock_patch:
        client = GitHubClient("token", "owner", "repo")
        client.update_pr(123, base="develop")

    call_kwargs = mock_patch.call_args[1]
    assert "base" in call_kwargs["json"]
    assert "body" not in call_kwargs["json"]


def test_github_client_update_pr_no_changes() -> None:
    """Test update_pr with no changes doesn't call API."""
    with patch.object(httpx.Client, "patch") as mock_patch:
        client = GitHubClient("token", "owner", "repo")
        client.update_pr(123)

    mock_patch.assert_not_called()


def test_github_client_update_pr_title() -> None:
    """Test updating PR title."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.Client, "patch", return_value=mock_response) as mock_patch:
        client = GitHubClient("token", "owner", "repo")
        client.update_pr(123, title="New Title")

    call_kwargs = mock_patch.call_args[1]
    assert call_kwargs["json"]["title"] == "New Title"

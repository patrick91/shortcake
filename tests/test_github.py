"""Tests for GitHub API client."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from dulwich.repo import Repo

from shortcake._github import (
    BranchGitHubInfo,
    GitHubClient,
    get_github_token,
    get_repo_info,
    push_branch,
)

# Save reference to real method before conftest autouse fixture patches it out
_real_resolve_repo_identity = GitHubClient._resolve_repo_identity


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


def test_get_repo_info_ssh_url_format(temp_repo: Repo) -> None:
    """Test parsing ssh:// URL format."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"ssh://git@github.com/owner/repo.git")
    config.write_to_path()

    result = get_repo_info(temp_repo)

    assert result == ("owner", "repo")


def test_get_repo_info_ssh_url_format_no_extension(temp_repo: Repo) -> None:
    """Test parsing ssh:// URL without .git extension."""
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"ssh://git@github.com/owner/repo")
    config.write_to_path()

    result = get_repo_info(temp_repo)

    assert result == ("owner", "repo")


# Tests for GitHubClient


def test_github_client_context_manager() -> None:
    """Test GitHubClient context manager closes the client."""
    with GitHubClient("token", "owner", "repo") as client:
        assert client is not None
    # Client should be closed after exiting context


def test_github_client_default_base_url() -> None:
    """Test GitHubClient uses default GitHub API URL."""
    with GitHubClient("token", "owner", "repo") as client:
        assert client.client.base_url == "https://api.github.com"


def test_github_client_custom_base_url() -> None:
    """Test GitHubClient accepts custom base URL."""
    with GitHubClient(
        "token", "owner", "repo", base_url="http://localhost:8080"
    ) as client:
        assert client.client.base_url == "http://localhost:8080"


def test_github_client_base_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test GitHubClient uses GITHUB_API_URL env var."""
    monkeypatch.setenv("GITHUB_API_URL", "http://mock-server:9000")

    with GitHubClient("token", "owner", "repo") as client:
        assert client.client.base_url == "http://mock-server:9000"


def test_github_client_explicit_base_url_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test explicit base_url parameter overrides GITHUB_API_URL env var."""
    monkeypatch.setenv("GITHUB_API_URL", "http://env-server:9000")

    with GitHubClient(
        "token", "owner", "repo", base_url="http://explicit:8080"
    ) as client:
        assert client.client.base_url == "http://explicit:8080"


@respx.mock
def test_github_client_get_pr_for_branch_found() -> None:
    """Test finding existing PR for branch."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "html_url": "https://github.com/owner/repo/pull/123",
                    "base": {"ref": "main"},
                    "title": "Test PR",
                    "body": "PR body",
                    "state": "open",
                    "draft": False,
                }
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_pr_for_branch("feature")

    assert result is not None
    assert result.number == 123
    assert result.url == "https://github.com/owner/repo/pull/123"
    assert result.base == "main"
    assert result.title == "Test PR"
    assert result.state == "open"
    assert result.is_draft is False


@respx.mock
def test_github_client_get_pr_for_branch_not_found() -> None:
    """Test no PR found for branch."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_pr_for_branch("feature")

    assert result is None


@respx.mock
def test_github_client_get_pr_for_branch_draft() -> None:
    """Test finding draft PR."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "html_url": "https://github.com/owner/repo/pull/123",
                    "base": {"ref": "main"},
                    "title": "Draft PR",
                    "body": None,  # Test null body
                    "state": "open",
                    "draft": True,
                }
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_pr_for_branch("feature")

    assert result is not None
    assert result.is_draft is True
    assert result.body == ""


@respx.mock
def test_github_client_create_pr() -> None:
    """Test creating a new PR."""
    route = respx.post("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            201,
            json={
                "number": 456,
                "html_url": "https://github.com/owner/repo/pull/456",
                "base": {"ref": "main"},
                "title": "New Feature",
                "body": "Description",
                "state": "open",
                "draft": False,
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.create_pr(
            head="feature",
            base="main",
            title="New Feature",
            body="Description",
            draft=False,
        )

    assert result.number == 456
    assert result.url == "https://github.com/owner/repo/pull/456"
    assert route.called


@respx.mock
def test_github_client_create_pr_draft() -> None:
    """Test creating a draft PR."""
    route = respx.post("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            201,
            json={
                "number": 789,
                "html_url": "https://github.com/owner/repo/pull/789",
                "base": {"ref": "main"},
                "title": "Draft Feature",
                "body": "",
                "state": "open",
                "draft": True,
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.create_pr(
            head="feature",
            base="main",
            title="Draft Feature",
            body="",
            draft=True,
        )

    assert result.is_draft is True
    # Verify draft=True was passed in request body
    assert route.calls.last.request.content is not None
    import json

    request_body = json.loads(route.calls.last.request.content)
    assert request_body["draft"] is True


@respx.mock
def test_github_client_update_pr() -> None:
    """Test updating a PR."""
    route = respx.patch("https://api.github.com/repos/owner/repo/pulls/123").mock(
        return_value=httpx.Response(200, json={})
    )

    with GitHubClient("token", "owner", "repo") as client:
        client.update_pr(123, base="develop", body="Updated body")

    assert route.called
    import json

    request_body = json.loads(route.calls.last.request.content)
    assert request_body["base"] == "develop"
    assert request_body["body"] == "Updated body"


@respx.mock
def test_github_client_update_pr_partial() -> None:
    """Test updating only some PR fields."""
    route = respx.patch("https://api.github.com/repos/owner/repo/pulls/123").mock(
        return_value=httpx.Response(200, json={})
    )

    with GitHubClient("token", "owner", "repo") as client:
        client.update_pr(123, base="develop")

    import json

    request_body = json.loads(route.calls.last.request.content)
    assert "base" in request_body
    assert "body" not in request_body


@respx.mock
def test_github_client_update_pr_no_changes() -> None:
    """Test update_pr with no changes doesn't call API."""
    route = respx.patch("https://api.github.com/repos/owner/repo/pulls/123")

    with GitHubClient("token", "owner", "repo") as client:
        client.update_pr(123)

    assert not route.called


@respx.mock
def test_github_client_update_pr_title() -> None:
    """Test updating PR title."""
    route = respx.patch("https://api.github.com/repos/owner/repo/pulls/123").mock(
        return_value=httpx.Response(200, json={})
    )

    with GitHubClient("token", "owner", "repo") as client:
        client.update_pr(123, title="New Title")

    import json

    request_body = json.loads(route.calls.last.request.content)
    assert request_body["title"] == "New Title"


@respx.mock
def test_github_client_get_pr_for_branch_only_queries_open() -> None:
    """Test that only open PRs are queried."""
    route = respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 200,
                    "html_url": "https://github.com/owner/repo/pull/200",
                    "base": {"ref": "main"},
                    "title": "Open PR",
                    "body": "Open body",
                    "state": "open",
                    "draft": False,
                },
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_pr_for_branch("feature")

    # Verify state=open is used in the query
    assert route.calls.last.request.url.params["state"] == "open"

    assert result is not None
    assert result.number == 200
    assert result.state == "open"


@respx.mock
def test_github_client_get_pr_for_branch_ignores_closed() -> None:
    """Test that closed PRs are ignored (returns None when no open PRs)."""
    # API returns empty list when state=open and only closed PRs exist
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_pr_for_branch("feature")

    assert result is None


@respx.mock
def test_github_client_handles_http_error() -> None:
    """Test that HTTP errors are raised properly."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )

    with (
        GitHubClient("token", "owner", "repo") as client,
        pytest.raises(httpx.HTTPStatusError) as exc_info,
    ):
        client.get_pr_for_branch("feature")

    assert exc_info.value.response.status_code == 401


@respx.mock
def test_github_client_has_merged_pr_true() -> None:
    """Test has_merged_pr returns True when merged PR exists."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "state": "closed",
                    "merged_at": "2024-01-15T10:30:00Z",
                }
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.has_merged_pr("feature")

    assert result is True


@respx.mock
def test_github_client_has_merged_pr_false_no_prs() -> None:
    """Test has_merged_pr returns False when no PRs exist."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.has_merged_pr("feature")

    assert result is False


@respx.mock
def test_github_client_has_merged_pr_false_closed_not_merged() -> None:
    """Test has_merged_pr returns False for closed but not merged PR."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "state": "closed",
                    "merged_at": None,  # Closed but not merged
                }
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.has_merged_pr("feature")

    assert result is False


@respx.mock
def test_github_client_get_merged_pr_base_returns_base() -> None:
    """Test get_merged_pr_base returns the base branch of a merged PR."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "state": "closed",
                    "merged_at": "2024-01-15T10:30:00Z",
                    "base": {"ref": "main"},
                }
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_merged_pr_base("feature")

    assert result == "main"


@respx.mock
def test_github_client_get_merged_pr_base_returns_none_no_merged() -> None:
    """Test get_merged_pr_base returns None when no merged PR exists."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "state": "closed",
                    "merged_at": None,
                    "base": {"ref": "main"},
                }
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_merged_pr_base("feature")

    assert result is None


@respx.mock
def test_github_client_get_merged_pr_base_returns_none_empty() -> None:
    """Test get_merged_pr_base returns None when no PRs exist."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_merged_pr_base("feature")

    assert result is None


@respx.mock
def test_github_client_has_merged_pr_queries_closed() -> None:
    """Test has_merged_pr queries closed PRs."""
    route = respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    with GitHubClient("token", "owner", "repo") as client:
        client.has_merged_pr("feature")

    assert route.calls.last.request.url.params["state"] == "closed"


# Tests for get_merged_pr_number


@respx.mock
def test_github_client_get_merged_pr_number_returns_number() -> None:
    """Test get_merged_pr_number returns PR number when merged PR exists."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 123, "merged_at": "2024-01-01T00:00:00Z"},
                {"number": 456, "merged_at": None},  # Closed but not merged
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_merged_pr_number("feature")

    assert result == 123


@respx.mock
def test_github_client_get_merged_pr_number_returns_none() -> None:
    """Test get_merged_pr_number returns None when no merged PR exists."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_merged_pr_number("feature")

    assert result is None


# Tests for get_closed_pr_info


@respx.mock
def test_github_client_get_closed_pr_info_closed_not_merged() -> None:
    """Test get_closed_pr_info returns closed (not merged) PR."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 456, "merged_at": None},
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        number, is_merged = client.get_closed_pr_info("feature")

    assert number == 456
    assert is_merged is False


@respx.mock
def test_github_client_get_closed_pr_info_prefers_merged() -> None:
    """Test get_closed_pr_info prefers merged PR over closed."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"number": 456, "merged_at": None},
                {"number": 123, "merged_at": "2024-01-01T00:00:00Z"},
            ],
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        number, is_merged = client.get_closed_pr_info("feature")

    assert number == 123
    assert is_merged is True


@respx.mock
def test_github_client_get_closed_pr_info_no_prs() -> None:
    """Test get_closed_pr_info returns None when no closed PRs."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    with GitHubClient("token", "owner", "repo") as client:
        number, is_merged = client.get_closed_pr_info("feature")

    assert number is None
    assert is_merged is False


# Tests for get_pr_by_number


@respx.mock
def test_github_client_get_pr_by_number_found() -> None:
    """Test get_pr_by_number returns PR info when found."""
    respx.get("https://api.github.com/repos/owner/repo/pulls/123").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 123,
                "html_url": "https://github.com/owner/repo/pull/123",
                "base": {"ref": "main"},
                "head": {"ref": "feature-branch"},
                "title": "Test PR",
                "body": "PR body",
                "state": "open",
                "draft": False,
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_pr_by_number(123)

    assert result is not None
    assert result.number == 123
    assert result.url == "https://github.com/owner/repo/pull/123"
    assert result.base == "main"
    assert result.head_ref == "feature-branch"
    assert result.title == "Test PR"
    assert result.state == "open"


@respx.mock
def test_github_client_get_pr_by_number_not_found() -> None:
    """Test get_pr_by_number returns None when PR doesn't exist."""
    respx.get("https://api.github.com/repos/owner/repo/pulls/999").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with GitHubClient("token", "owner", "repo") as client:
        result = client.get_pr_by_number(999)

    assert result is None


@respx.mock
def test_github_client_get_pr_by_number_http_error() -> None:
    """Test get_pr_by_number raises on non-404 errors."""
    respx.get("https://api.github.com/repos/owner/repo/pulls/123").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )

    with (
        GitHubClient("token", "owner", "repo") as client,
        pytest.raises(httpx.HTTPStatusError) as exc_info,
    ):
        client.get_pr_by_number(123)

    assert exc_info.value.response.status_code == 401


# Tests for push_branch


def test_push_branch_new_branch_no_tracking_ref(temp_repo: Repo) -> None:
    """Test push succeeds for new branch without tracking ref."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create a branch
    temp_repo.refs[b"refs/heads/feature"] = temp_repo.head()

    with patch("shortcake._github.porcelain.push") as mock_push:
        success, error = push_branch(temp_repo, "feature")

    assert success is True
    assert error is None
    mock_push.assert_called_once()


def test_push_branch_force_with_lease_passes(temp_repo: Repo) -> None:
    """Test push succeeds when remote matches tracking ref."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create branch and tracking ref pointing to same commit
    head_sha = temp_repo.head()
    temp_repo.refs[b"refs/heads/feature"] = head_sha
    temp_repo.refs[b"refs/remotes/origin/feature"] = head_sha

    # Mock ls_remote to return same SHA
    mock_ls_result = MagicMock()
    mock_ls_result.refs = {b"refs/heads/feature": head_sha}

    with (
        patch("shortcake._github.porcelain.ls_remote", return_value=mock_ls_result),
        patch("shortcake._github.porcelain.push") as mock_push,
    ):
        success, error = push_branch(temp_repo, "feature")

    assert success is True
    assert error is None
    mock_push.assert_called_once()


def test_push_branch_force_with_lease_fails(temp_repo: Repo) -> None:
    """Test push fails when remote differs from tracking ref."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create branch and tracking ref
    head_sha = temp_repo.head()
    temp_repo.refs[b"refs/heads/feature"] = head_sha
    temp_repo.refs[b"refs/remotes/origin/feature"] = head_sha

    # Mock ls_remote to return DIFFERENT SHA (someone else pushed)
    mock_ls_result = MagicMock()
    mock_ls_result.refs = {b"refs/heads/feature": b"different_sha_from_someone_else"}

    with (
        patch("shortcake._github.porcelain.ls_remote", return_value=mock_ls_result),
        patch("shortcake._github.porcelain.push") as mock_push,
    ):
        success, error = push_branch(temp_repo, "feature")

    assert success is False
    assert error == "remote has diverged (use --force to overwrite)"
    mock_push.assert_not_called()


def test_push_branch_force_with_lease_uses_correct_url(temp_repo: Repo) -> None:
    """Test that ls_remote is called with the origin URL, not repo."""
    # Set up origin remote with specific URL
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:myorg/myrepo.git")
    config.write_to_path()

    # Create branch and tracking ref
    head_sha = temp_repo.head()
    temp_repo.refs[b"refs/heads/feature"] = head_sha
    temp_repo.refs[b"refs/remotes/origin/feature"] = head_sha

    mock_ls_result = MagicMock()
    mock_ls_result.refs = {b"refs/heads/feature": head_sha}

    with (
        patch(
            "shortcake._github.porcelain.ls_remote", return_value=mock_ls_result
        ) as mock_ls,
        patch("shortcake._github.porcelain.push"),
    ):
        push_branch(temp_repo, "feature")

    # Verify ls_remote was called with the URL string and quiet=True
    mock_ls.assert_called_once_with("git@github.com:myorg/myrepo.git", quiet=True)


def test_push_branch_disabled_force_with_lease(temp_repo: Repo) -> None:
    """Test push with force_with_lease=False skips the check."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create branch and tracking ref with different SHA (would fail if checked)
    head_sha = temp_repo.head()
    temp_repo.refs[b"refs/heads/feature"] = head_sha
    temp_repo.refs[b"refs/remotes/origin/feature"] = head_sha

    with (
        patch("shortcake._github.porcelain.ls_remote") as mock_ls,
        patch("shortcake._github.porcelain.push") as mock_push,
    ):
        success, error = push_branch(temp_repo, "feature", force_with_lease=False)

    assert success is True
    assert error is None
    mock_ls.assert_not_called()  # Should not check remote
    mock_push.assert_called_once()


# Tests for repo identity resolution (renamed/transferred repos)

REPO_RESPONSE = {
    "owner": {"login": "owner"},
    "name": "repo",
}

PR_JSON = {
    "number": 42,
    "html_url": "https://github.com/new-owner/repo/pull/42",
    "base": {"ref": "main"},
    "title": "Feature PR",
    "body": "Body",
    "state": "open",
    "draft": False,
}


@respx.mock
def test_github_client_resolves_transferred_repo_owner() -> None:
    """Test that _resolve_repo_identity detects transferred repo."""
    respx.get("https://api.github.com/repos/old-owner/repo").mock(
        return_value=httpx.Response(
            200,
            json={"owner": {"login": "new-owner"}, "name": "repo"},
        )
    )

    with GitHubClient("token", "old-owner", "repo") as client:
        _real_resolve_repo_identity(client)
        assert client.owner == "new-owner"
        assert client.repo == "repo"


@respx.mock
def test_github_client_resolves_renamed_repo() -> None:
    """Test that _resolve_repo_identity detects renamed repo."""
    respx.get("https://api.github.com/repos/owner/old-repo").mock(
        return_value=httpx.Response(
            200,
            json={"owner": {"login": "owner"}, "name": "new-repo"},
        )
    )

    with GitHubClient("token", "owner", "old-repo") as client:
        _real_resolve_repo_identity(client)
        assert client.owner == "owner"
        assert client.repo == "new-repo"


@respx.mock
def test_github_client_identity_unchanged_when_not_transferred() -> None:
    """Test that owner/repo stay the same for non-transferred repos."""
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(200, json=REPO_RESPONSE)
    )

    with GitHubClient("token", "owner", "repo") as client:
        _real_resolve_repo_identity(client)
        assert client.owner == "owner"
        assert client.repo == "repo"


@respx.mock
def test_github_client_identity_resolution_network_error() -> None:
    """Test that network errors during identity resolution are non-fatal."""
    respx.get("https://api.github.com/repos/owner/repo").mock(
        side_effect=httpx.ConnectError("connection failed")
    )

    with GitHubClient("token", "owner", "repo") as client:
        _real_resolve_repo_identity(client)
        assert client.owner == "owner"
        assert client.repo == "repo"


@respx.mock
def test_github_client_identity_resolution_api_error() -> None:
    """Test that API errors during identity resolution are non-fatal."""
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with GitHubClient("token", "owner", "repo") as client:
        _real_resolve_repo_identity(client)
        assert client.owner == "owner"
        assert client.repo == "repo"


@respx.mock
def test_github_client_transferred_repo_uses_new_owner_for_prs() -> None:
    """Test that after identity resolution, PR queries use the new owner."""
    respx.get("https://api.github.com/repos/old-owner/repo").mock(
        return_value=httpx.Response(
            200,
            json={"owner": {"login": "new-owner"}, "name": "repo"},
        )
    )
    pr_route = respx.get("https://api.github.com/repos/new-owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[PR_JSON])
    )

    with GitHubClient("token", "old-owner", "repo") as client:
        _real_resolve_repo_identity(client)
        result = client.get_pr_for_branch("feature")

    assert result is not None
    assert result.number == 42
    assert pr_route.called
    assert pr_route.calls.last.request.url.params["head"] == "new-owner:feature"


@respx.mock
def test_github_client_transferred_repo_creates_pr_with_new_owner() -> None:
    """Test that create_pr uses the resolved owner."""
    respx.get("https://api.github.com/repos/old-owner/repo").mock(
        return_value=httpx.Response(
            200,
            json={"owner": {"login": "new-owner"}, "name": "repo"},
        )
    )
    create_route = respx.post("https://api.github.com/repos/new-owner/repo/pulls").mock(
        return_value=httpx.Response(201, json=PR_JSON)
    )

    with GitHubClient("token", "old-owner", "repo") as client:
        _real_resolve_repo_identity(client)
        result = client.create_pr(
            head="feature", base="main", title="Test", body="", draft=False
        )

    assert result.number == 42
    assert create_route.called


# Tests for get_check_status


@respx.mock
def test_get_check_status_success() -> None:
    """All checks passed returns 'success'."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": "success", "status": "completed"},
                    {"conclusion": "skipped", "status": "completed"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "success"


@respx.mock
def test_get_check_status_failure() -> None:
    """Any failed check returns 'failure'."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": "success", "status": "completed"},
                    {"conclusion": "failure", "status": "completed"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "failure"


@respx.mock
def test_get_check_status_pending() -> None:
    """In-progress checks return 'pending'."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": None, "status": "in_progress"},
                    {"conclusion": "success", "status": "completed"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "pending"


@respx.mock
def test_get_check_status_no_checks() -> None:
    """No check runs returns None."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(200, json={"check_runs": []})
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") is None


@respx.mock
def test_get_check_status_api_error() -> None:
    """Non-200 response returns None."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(404)
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") is None


@respx.mock
def test_get_check_status_network_error() -> None:
    """Network error returns None."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        side_effect=httpx.ConnectError("connection failed")
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") is None


@respx.mock
def test_get_check_status_timed_out() -> None:
    """Timed out check returns 'failure'."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": "timed_out", "status": "completed"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "failure"


@respx.mock
def test_get_check_status_cancelled() -> None:
    """Cancelled check returns 'failure'."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": "cancelled", "status": "completed"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "failure"


@respx.mock
def test_get_check_status_queued() -> None:
    """Queued check returns 'pending'."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": None, "status": "queued"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "pending"


@respx.mock
def test_get_check_status_unknown_conclusion() -> None:
    """Unknown conclusion falls back to 'pending'."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": "action_required", "status": "completed"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "pending"


@respx.mock
def test_get_check_status_neutral() -> None:
    """Neutral conclusion counts as success."""
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": [
                    {"conclusion": "neutral", "status": "completed"},
                ]
            },
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        assert client.get_check_status("feat") == "success"


# Tests for get_branch_github_info


@respx.mock
def test_get_branch_github_info_with_pr_and_checks() -> None:
    """Returns combined PR and CI info."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 42,
                    "html_url": "https://github.com/owner/repo/pull/42",
                    "base": {"ref": "main"},
                    "title": "Test",
                    "body": "",
                    "state": "open",
                    "draft": True,
                }
            ],
        )
    )
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={"check_runs": [{"conclusion": "success", "status": "completed"}]},
        )
    )

    with GitHubClient("token", "owner", "repo") as client:
        info = client.get_branch_github_info("feat")

    assert info == BranchGitHubInfo(
        pr_number=42,
        pr_url="https://github.com/owner/repo/pull/42",
        pr_is_draft=True,
        check_status="success",
    )


@respx.mock
def test_get_branch_github_info_no_pr_no_checks() -> None:
    """Returns None fields when no PR or checks exist."""
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("https://api.github.com/repos/owner/repo/commits/feat/check-runs").mock(
        return_value=httpx.Response(200, json={"check_runs": []})
    )

    with GitHubClient("token", "owner", "repo") as client:
        info = client.get_branch_github_info("feat")

    assert info == BranchGitHubInfo(
        pr_number=None,
        pr_url=None,
        pr_is_draft=False,
        check_status=None,
    )

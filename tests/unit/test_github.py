"""Tests for the GitHub API client."""

from unittest.mock import patch

import pytest
import respx
from httpx import Response

from shortcake.github import GitHubClient, GitHubError, PullRequest, _get_token_from_gh_cli


@pytest.fixture
def github_client(monkeypatch: pytest.MonkeyPatch) -> GitHubClient:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    return GitHubClient()


def test_github_client_no_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # Also mock gh CLI to return nothing
    with patch("shortcake.github._get_token_from_gh_cli", return_value=None):
        with pytest.raises(GitHubError) as exc_info:
            GitHubClient()
        assert "GitHub token not found" in str(exc_info.value)
        assert "gh auth login" in str(exc_info.value)


def test_get_token_from_gh_cli_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "gh-cli-token\n"
        mock_run.return_value.returncode = 0
        token = _get_token_from_gh_cli()
        assert token == "gh-cli-token"


def test_get_token_from_gh_cli_not_installed():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        token = _get_token_from_gh_cli()
        assert token is None


def test_github_client_uses_gh_cli_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch("shortcake.github._get_token_from_gh_cli", return_value="gh-token"):
        client = GitHubClient()
        assert client.token == "gh-token"
        client.close()


def test_github_client_with_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    client = GitHubClient()
    assert client.token == "test-token"
    client.close()


def test_github_client_explicit_token():
    client = GitHubClient(token="explicit-token")
    assert client.token == "explicit-token"
    client.close()


def test_github_client_context_manager(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    with GitHubClient() as client:
        assert client.token == "test-token"


@respx.mock
def test_create_pull_request(github_client: GitHubClient):
    respx.post("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=Response(
            201,
            json={
                "number": 1,
                "title": "Test PR",
                "body": "Description",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "open",
            },
        )
    )

    pr = github_client.create_pull_request(
        owner="owner",
        repo="repo",
        title="Test PR",
        head="feature",
        base="main",
        body="Description",
    )

    assert pr.number == 1
    assert pr.title == "Test PR"
    assert pr.html_url == "https://github.com/owner/repo/pull/1"
    assert pr.head_ref == "feature"
    assert pr.base_ref == "main"


@respx.mock
def test_create_pull_request_draft(github_client: GitHubClient):
    respx.post("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=Response(
            201,
            json={
                "number": 1,
                "title": "Draft PR",
                "body": "",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "open",
            },
        )
    )

    pr = github_client.create_pull_request(
        owner="owner",
        repo="repo",
        title="Draft PR",
        head="feature",
        base="main",
        draft=True,
    )

    assert pr.number == 1


@respx.mock
def test_create_pull_request_error(github_client: GitHubClient):
    respx.post("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=Response(
            422,
            json={"message": "Validation Failed"},
        )
    )

    with pytest.raises(GitHubError) as exc_info:
        github_client.create_pull_request(
            owner="owner",
            repo="repo",
            title="Test PR",
            head="feature",
            base="main",
        )

    assert "422" in str(exc_info.value)
    assert "Validation Failed" in str(exc_info.value)


@respx.mock
def test_update_pull_request(github_client: GitHubClient):
    respx.patch("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "number": 1,
                "title": "Updated Title",
                "body": "Updated body",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "develop", "sha": "def456"},
                "state": "open",
            },
        )
    )

    pr = github_client.update_pull_request(
        owner="owner",
        repo="repo",
        pr_number=1,
        title="Updated Title",
        body="Updated body",
        base="develop",
    )

    assert pr.title == "Updated Title"
    assert pr.base_ref == "develop"


@respx.mock
def test_get_pull_request(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=Response(
            200,
            json={
                "number": 42,
                "title": "Test PR",
                "body": "Description",
                "html_url": "https://github.com/owner/repo/pull/42",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "open",
            },
        )
    )

    pr = github_client.get_pull_request("owner", "repo", 42)

    assert pr.number == 42
    assert pr.title == "Test PR"


@respx.mock
def test_get_pull_request_not_found(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/999").mock(
        return_value=Response(
            404,
            json={"message": "Not Found"},
        )
    )

    with pytest.raises(GitHubError) as exc_info:
        github_client.get_pull_request("owner", "repo", 999)

    assert "404" in str(exc_info.value)


@respx.mock
def test_get_pull_requests_for_branch(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "PR 1",
                    "body": "",
                    "html_url": "https://github.com/owner/repo/pull/1",
                    "head": {"ref": "feature", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                    "state": "open",
                },
                {
                    "number": 2,
                    "title": "PR 2",
                    "body": "",
                    "html_url": "https://github.com/owner/repo/pull/2",
                    "head": {"ref": "feature", "sha": "abc123"},
                    "base": {"ref": "develop", "sha": "ghi789"},
                    "state": "open",
                },
            ],
        )
    )

    prs = github_client.get_pull_requests_for_branch("owner", "repo", "feature")

    assert len(prs) == 2
    assert prs[0].number == 1
    assert prs[1].number == 2


@respx.mock
def test_get_pull_requests_for_branch_empty(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=Response(200, json=[])
    )

    prs = github_client.get_pull_requests_for_branch("owner", "repo", "no-prs")

    assert len(prs) == 0


def test_pull_request_dataclass():
    pr = PullRequest(
        number=1,
        title="Test",
        body="Body",
        html_url="https://example.com",
        head_ref="feature",
        base_ref="main",
        state="open",
    )

    assert pr.number == 1
    assert pr.title == "Test"
    assert pr.state == "open"


def test_pull_request_with_author():
    pr = PullRequest(
        number=1,
        title="Test",
        body="Body",
        html_url="https://example.com",
        head_ref="feature",
        base_ref="main",
        state="open",
        author="testuser",
    )

    assert pr.author == "testuser"


@respx.mock
def test_is_pr_merged_true(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "number": 1,
                "title": "Merged PR",
                "body": "",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "closed",
                "merged": True,
            },
        )
    )

    result = github_client.is_pr_merged("owner", "repo", 1)
    assert result is True


@respx.mock
def test_is_pr_merged_false(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "number": 1,
                "title": "Open PR",
                "body": "",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "open",
                "merged": False,
            },
        )
    )

    result = github_client.is_pr_merged("owner", "repo", 1)
    assert result is False


@respx.mock
def test_is_pr_merged_error(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/999").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )

    result = github_client.is_pr_merged("owner", "repo", 999)
    assert result is False


@respx.mock
def test_is_pr_closed_unmerged_true(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "number": 1,
                "title": "Closed PR",
                "body": "",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "closed",
                "merged": False,
            },
        )
    )

    result = github_client.is_pr_closed_unmerged("owner", "repo", 1)
    assert result is True


@respx.mock
def test_is_pr_closed_unmerged_false_merged(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "number": 1,
                "title": "Merged PR",
                "body": "",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "closed",
                "merged": True,
            },
        )
    )

    result = github_client.is_pr_closed_unmerged("owner", "repo", 1)
    assert result is False


@respx.mock
def test_is_pr_closed_unmerged_false_open(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "number": 1,
                "title": "Open PR",
                "body": "",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "open",
                "merged": False,
            },
        )
    )

    result = github_client.is_pr_closed_unmerged("owner", "repo", 1)
    assert result is False


@respx.mock
def test_is_pr_closed_unmerged_error(github_client: GitHubClient):
    respx.get("https://api.github.com/repos/owner/repo/pulls/999").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )

    result = github_client.is_pr_closed_unmerged("owner", "repo", 999)
    assert result is False


@respx.mock
def test_get_current_user(github_client: GitHubClient):
    respx.get("https://api.github.com/user").mock(
        return_value=Response(200, json={"login": "testuser"})
    )

    username = github_client.get_current_user()
    assert username == "testuser"


@respx.mock
def test_update_pull_request_body_only(github_client: GitHubClient):
    respx.patch("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            200,
            json={
                "number": 1,
                "title": "Test PR",
                "body": "New body only",
                "html_url": "https://github.com/owner/repo/pull/1",
                "head": {"ref": "feature", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "state": "open",
            },
        )
    )

    pr = github_client.update_pull_request(
        owner="owner",
        repo="repo",
        pr_number=1,
        body="New body only",
    )

    assert pr.body == "New body only"


@respx.mock
def test_update_pull_request_error(github_client: GitHubClient):
    respx.patch("https://api.github.com/repos/owner/repo/pulls/1").mock(
        return_value=Response(
            404,
            json={"message": "Not Found"},
        )
    )

    with pytest.raises(GitHubError) as exc_info:
        github_client.update_pull_request(
            owner="owner",
            repo="repo",
            pr_number=1,
            title="Updated",
        )

    assert "404" in str(exc_info.value)

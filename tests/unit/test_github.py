"""Tests for the GitHub API client."""

import pytest
import respx
from httpx import Response

from shortcake.github import GitHubClient, GitHubError, PullRequest


@pytest.fixture
def github_client(monkeypatch: pytest.MonkeyPatch) -> GitHubClient:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    return GitHubClient()


def test_github_client_no_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubError) as exc_info:
        GitHubClient()
    assert "GitHub token not found" in str(exc_info.value)


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

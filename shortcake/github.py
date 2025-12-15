"""GitHub API client for shortcake."""

import os
import re
import subprocess
from dataclasses import dataclass

import httpx

from shortcake.git import GitRepo


class GitHubError(Exception):
    """Exception raised for GitHub API errors."""

    pass


def _get_token_from_gh_cli() -> str | None:
    """Try to get GitHub token from the gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        token = result.stdout.strip()
        return token if token else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


@dataclass
class PullRequest:
    """Represents a GitHub Pull Request."""

    number: int
    title: str
    body: str
    html_url: str
    head_ref: str
    base_ref: str
    state: str
    author: str | None = None
    merged: bool = False


class GitHubClient:
    """Client for interacting with GitHub API."""

    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com"):
        """Initialize the GitHub client.

        Args:
            token: GitHub personal access token. If not provided, will try
                   GITHUB_TOKEN env var, then gh CLI.
            base_url: Base URL for GitHub API (for GitHub Enterprise support).
        """
        self.token = token or os.environ.get("GITHUB_TOKEN") or _get_token_from_gh_cli()
        if not self.token:
            raise GitHubError(
                "GitHub token not found. Either:\n"
                "  1. Install and authenticate with gh CLI: gh auth login\n"
                "  2. Set GITHUB_TOKEN environment variable"
            )
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            headers={
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, method: str, endpoint: str, **kwargs: object) -> dict:
        """Make an HTTP request to GitHub API.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            endpoint: API endpoint (e.g., /repos/owner/repo/pulls)
            **kwargs: Additional arguments to pass to httpx

        Returns:
            JSON response as dict

        Raises:
            GitHubError: If the request fails
        """
        url = f"{self.base_url}{endpoint}"
        try:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_data = e.response.json()
                error_body = error_data.get("message", str(error_data))
            except Exception:
                error_body = e.response.text
            raise GitHubError(f"GitHub API error: {e.response.status_code} - {error_body}") from e
        except httpx.RequestError as e:
            raise GitHubError(f"GitHub API request failed: {e}") from e

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> PullRequest:
        """Create a new pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            title: PR title
            head: Head branch name
            base: Base branch name
            body: PR body/description
            draft: Whether to create as draft PR

        Returns:
            PullRequest object with PR details
        """
        data = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        response = self._request("POST", f"/repos/{owner}/{repo}/pulls", json=data)
        return PullRequest(
            number=response["number"],
            title=response["title"],
            body=response.get("body") or "",
            html_url=response["html_url"],
            head_ref=response["head"]["ref"],
            base_ref=response["base"]["ref"],
            state=response["state"],
        )

    def update_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
        base: str | None = None,
        state: str | None = None,
    ) -> PullRequest:
        """Update an existing pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number to update
            title: New title (optional)
            body: New body (optional)
            base: New base branch (optional)
            state: New state - 'open' or 'closed' (optional)

        Returns:
            Updated PullRequest object
        """
        data: dict[str, str] = {}
        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if base is not None:
            data["base"] = base
        if state is not None:
            data["state"] = state

        response = self._request("PATCH", f"/repos/{owner}/{repo}/pulls/{pr_number}", json=data)
        return PullRequest(
            number=response["number"],
            title=response["title"],
            body=response.get("body") or "",
            html_url=response["html_url"],
            head_ref=response["head"]["ref"],
            base_ref=response["base"]["ref"],
            state=response["state"],
        )

    def get_pull_request(self, owner: str, repo: str, pr_number: int) -> PullRequest:
        """Get a pull request by number.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number

        Returns:
            PullRequest object
        """
        response = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return PullRequest(
            number=response["number"],
            title=response["title"],
            body=response.get("body") or "",
            html_url=response["html_url"],
            head_ref=response["head"]["ref"],
            base_ref=response["base"]["ref"],
            state=response["state"],
            merged=response.get("merged", False),
        )

    def is_pr_merged(self, owner: str, repo: str, pr_number: int) -> bool:
        """Check if a pull request has been merged.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number

        Returns:
            True if the PR has been merged
        """
        try:
            pr = self.get_pull_request(owner, repo, pr_number)
            return pr.merged
        except GitHubError:
            return False

    def is_pr_closed_unmerged(self, owner: str, repo: str, pr_number: int) -> bool:
        """Check if a pull request was closed without being merged.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number

        Returns:
            True if the PR was closed without being merged
        """
        try:
            pr = self.get_pull_request(owner, repo, pr_number)
            return pr.state == "closed" and not pr.merged
        except GitHubError:
            return False

    def get_pull_requests_for_branch(
        self, owner: str, repo: str, head: str, state: str = "open"
    ) -> list[PullRequest]:
        """Get pull requests for a specific head branch.

        Args:
            owner: Repository owner
            repo: Repository name
            head: Head branch name (can be 'owner:branch' or just 'branch')
            state: PR state filter ('open', 'closed', 'all')

        Returns:
            List of PullRequest objects
        """
        # GitHub API expects head in format 'owner:branch'
        if ":" not in head:
            head = f"{owner}:{head}"

        response = self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"head": head, "state": state},
        )
        return [
            PullRequest(
                number=pr["number"],
                title=pr["title"],
                body=pr.get("body") or "",
                html_url=pr["html_url"],
                head_ref=pr["head"]["ref"],
                base_ref=pr["base"]["ref"],
                state=pr["state"],
            )
            for pr in response
        ]

    def list_pull_requests(
        self, owner: str, repo: str, state: str = "open", per_page: int = 30
    ) -> list[PullRequest]:
        """List pull requests for a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            state: PR state filter ('open', 'closed', 'all')
            per_page: Number of results per page (max 100)

        Returns:
            List of PullRequest objects
        """
        response = self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": per_page, "sort": "updated", "direction": "desc"},
        )
        return [
            PullRequest(
                number=pr["number"],
                title=pr["title"],
                body=pr.get("body") or "",
                html_url=pr["html_url"],
                head_ref=pr["head"]["ref"],
                base_ref=pr["base"]["ref"],
                state=pr["state"],
                author=pr["user"]["login"],
            )
            for pr in response
        ]

    def get_current_user(self) -> str:
        """Get the login of the currently authenticated user.

        Returns:
            The username of the authenticated user
        """
        response = self._request("GET", "/user")
        return response["login"]


def parse_github_remote(remote_url: str) -> tuple[str, str]:
    """Parse GitHub owner and repo from a remote URL.

    Supports both HTTPS and SSH URLs:
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git

    Args:
        remote_url: Git remote URL

    Returns:
        Tuple of (owner, repo)

    Raises:
        GitHubError: If URL cannot be parsed
    """
    # Try SSH format: git@github.com:owner/repo.git
    ssh_match = re.match(r"git@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    # Try HTTPS format: https://github.com/owner/repo.git
    https_match = re.match(r"https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
    if https_match:
        return https_match.group(1), https_match.group(2)

    raise GitHubError(f"Could not parse GitHub owner/repo from remote URL: {remote_url}")


def get_github_repo_info(git: GitRepo, remote: str = "origin") -> tuple[str, str]:
    """Get GitHub owner and repo from git remote.

    Args:
        git: GitRepo instance
        remote: Remote name to use (default: origin)

    Returns:
        Tuple of (owner, repo)

    Raises:
        GitHubError: If remote doesn't exist or URL cannot be parsed
    """
    if not git.has_remote(remote):
        raise GitHubError(f"Remote '{remote}' not found")

    remote_url = git.get_remote_url(remote)
    return parse_github_remote(remote_url)

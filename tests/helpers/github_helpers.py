"""GitHub API mocking helpers for testing."""

import json
from pathlib import Path
from typing import Any

import respx
from httpx import Response


class GitHubMocker:
    """Helper class for mocking GitHub API responses."""

    def __init__(
        self, respx_mock: respx.MockRouter, owner: str = "testuser", repo: str = "testrepo"
    ):
        """Initialize the GitHub mocker.

        Args:
            respx_mock: The respx mock router
            owner: Repository owner/organization
            repo: Repository name
        """
        self.mock = respx_mock
        self.owner = owner
        self.repo = repo
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"

    def mock_get_pr(
        self,
        pr_number: int,
        title: str,
        body: str,
        head_ref: str,
        base_ref: str = "main",
        state: str = "open",
        mergeable: bool = True,
    ) -> None:
        """Mock a GET request for a pull request."""
        response_data = {
            "number": pr_number,
            "title": title,
            "body": body,
            "state": state,
            "head": {
                "ref": head_ref,
                "sha": "a" * 40,
            },
            "base": {
                "ref": base_ref,
                "sha": "b" * 40,
            },
            "mergeable": mergeable,
            "html_url": f"https://github.com/{self.owner}/{self.repo}/pull/{pr_number}",
        }
        self.mock.get(f"{self.base_url}/pulls/{pr_number}").mock(
            return_value=Response(200, json=response_data)
        )

    def mock_create_pr(
        self,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
        head_ref: str | None = None,
        base_ref: str | None = None,
    ) -> None:
        """Mock a POST request to create a pull request."""

        def create_pr_response(request: Any) -> Response:
            json_data = request.json()
            response_data = {
                "number": pr_number,
                "title": json_data.get("title", title),
                "body": json_data.get("body", body),
                "state": "open",
                "head": {
                    "ref": json_data.get("head", head_ref),
                    "sha": "a" * 40,
                },
                "base": {
                    "ref": json_data.get("base", base_ref),
                    "sha": "b" * 40,
                },
                "html_url": f"https://github.com/{self.owner}/{self.repo}/pull/{pr_number}",
            }
            return Response(201, json=response_data)

        self.mock.post(f"{self.base_url}/pulls").mock(side_effect=create_pr_response)

    def mock_update_pr(
        self,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
        base_ref: str | None = None,
    ) -> None:
        """Mock a PATCH request to update a pull request."""

        def update_pr_response(request: Any) -> Response:
            json_data = request.json()
            response_data = {
                "number": pr_number,
                "title": json_data.get("title", title),
                "body": json_data.get("body", body),
                "state": "open",
                "head": {
                    "ref": f"branch-{pr_number}",
                    "sha": "a" * 40,
                },
                "base": {
                    "ref": json_data.get("base", base_ref) or "main",
                    "sha": "b" * 40,
                },
                "html_url": f"https://github.com/{self.owner}/{self.repo}/pull/{pr_number}",
            }
            return Response(200, json=response_data)

        self.mock.patch(f"{self.base_url}/pulls/{pr_number}").mock(side_effect=update_pr_response)

    def mock_list_prs(self, prs: list[dict[str, Any]]) -> None:
        """Mock a GET request to list pull requests.

        Args:
            prs: List of PR data dictionaries with keys: number, title, head_ref, base_ref
        """
        response_data = [
            {
                "number": pr["number"],
                "title": pr.get("title", f"PR #{pr['number']}"),
                "body": pr.get("body", ""),
                "state": pr.get("state", "open"),
                "head": {
                    "ref": pr["head_ref"],
                    "sha": "a" * 40,
                },
                "base": {
                    "ref": pr.get("base_ref", "main"),
                    "sha": "b" * 40,
                },
                "html_url": f"https://github.com/{self.owner}/{self.repo}/pull/{pr['number']}",
            }
            for pr in prs
        ]
        self.mock.get(f"{self.base_url}/pulls").mock(return_value=Response(200, json=response_data))

    def mock_get_branch(self, branch_name: str, sha: str) -> None:
        """Mock a GET request for a branch."""
        response_data = {
            "name": branch_name,
            "commit": {
                "sha": sha,
                "url": f"{self.base_url}/commits/{sha}",
            },
        }
        self.mock.get(f"{self.base_url}/branches/{branch_name}").mock(
            return_value=Response(200, json=response_data)
        )

    def mock_branch_not_found(self, branch_name: str) -> None:
        """Mock a 404 response for a branch that doesn't exist."""
        self.mock.get(f"{self.base_url}/branches/{branch_name}").mock(
            return_value=Response(404, json={"message": "Branch not found"})
        )

    def mock_error(self, status_code: int, message: str, url_pattern: str | None = None) -> None:
        """Mock an error response.

        Args:
            status_code: HTTP status code
            message: Error message
            url_pattern: Optional URL pattern to match (if None, matches all URLs)
        """
        error_data = {"message": message}
        if url_pattern:
            self.mock.route(url__regex=url_pattern).mock(
                return_value=Response(status_code, json=error_data)
            )
        else:
            self.mock.route().mock(return_value=Response(status_code, json=error_data))


def load_github_fixture(fixture_name: str) -> dict[str, Any]:
    """Load a GitHub API response fixture from JSON.

    Args:
        fixture_name: Name of the fixture file (without .json extension)

    Returns:
        The parsed JSON data
    """
    fixture_path = (
        Path(__file__).parent.parent / "fixtures" / "github_responses" / f"{fixture_name}.json"
    )
    return json.loads(fixture_path.read_text())


def create_pr_body(description: str, stack_info: dict[str, Any] | None = None) -> str:
    """Create a PR body with optional stack information.

    Args:
        description: The main PR description
        stack_info: Optional stack metadata (parent/child PRs)

    Returns:
        Formatted PR body
    """
    body = description

    if stack_info:
        body += "\n\n---\n\n"
        if "parent" in stack_info:
            body += f"⬆️ Parent PR: #{stack_info['parent']}\n"
        if "children" in stack_info and stack_info["children"]:
            body += "⬇️ Child PRs:\n"
            for child in stack_info["children"]:
                body += f"  - #{child}\n"

    return body

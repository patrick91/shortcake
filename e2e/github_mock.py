"""
Mock GitHub API server for E2E testing.

Provides a lightweight HTTP server that simulates GitHub API endpoints
for PR management, allowing full testing of `sc submit` without a real token.
"""

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass
class MockPR:
    """A mock pull request."""

    number: int
    head: str  # branch name
    base: str
    title: str
    body: str
    state: str = "open"
    draft: bool = False
    merged_at: str | None = None


@dataclass
class MockStack:
    """A mock native pull request stack."""

    number: int
    pull_requests: list[int]
    base: str


@dataclass
class GitHubMockState:
    """State for the mock GitHub server."""

    prs: dict[int, MockPR] = field(default_factory=dict)
    stacks: dict[int, MockStack] = field(default_factory=dict)
    next_pr_number: int = 1
    next_stack_number: int = 1
    error_mode: str | None = None  # "auth", "rate_limit", or None

    def add_pr(
        self,
        head: str,
        base: str,
        title: str = "",
        body: str = "",
        number: int | None = None,
        draft: bool = False,
    ) -> MockPR:
        """Add a PR to the mock state."""
        if number is None:
            number = self.next_pr_number
            self.next_pr_number = max(self.next_pr_number, number + 1)
        else:
            self.next_pr_number = max(self.next_pr_number, number + 1)

        pr = MockPR(
            number=number,
            head=head,
            base=base,
            title=title or f"PR for {head}",
            body=body,
            draft=draft,
        )
        self.prs[number] = pr
        return pr

    def merge_pr(self, number: int) -> bool:
        """Mark a PR as merged."""
        if number in self.prs:
            self.prs[number].state = "closed"
            self.prs[number].merged_at = "2024-01-15T10:30:00Z"
            return True
        return False

    def get_pr_by_head(self, head: str, state: str = "open") -> MockPR | None:
        """Find PR by head branch."""
        for pr in self.prs.values():
            if pr.head == head and pr.state == state:
                return pr
        return None

    def get_prs_by_head(self, head: str, state: str = "open") -> list[MockPR]:
        """Find all PRs by head branch and state."""
        return [pr for pr in self.prs.values() if pr.head == head and pr.state == state]

    def stack_for_pr(self, pr_number: int) -> MockStack | None:
        """Return the native stack containing a PR."""
        return next(
            (
                stack
                for stack in self.stacks.values()
                if pr_number in stack.pull_requests
            ),
            None,
        )

    def add_stack(self, pull_requests: list[int]) -> MockStack:
        """Create a native stack from bottom-to-top PR numbers."""
        number = self.next_stack_number
        self.next_stack_number += 1
        stack = MockStack(
            number=number,
            pull_requests=list(pull_requests),
            base=self.prs[pull_requests[0]].base,
        )
        self.stacks[number] = stack
        return stack


def create_mock_handler(state: GitHubMockState, owner: str, repo: str):
    """Create a request handler with the given state."""

    class MockGitHubHandler(BaseHTTPRequestHandler):
        """HTTP request handler for mock GitHub API."""

        def log_message(self, format: str, *args: Any) -> None:
            """Suppress logging."""
            pass

        def _send_json(self, data: Any, status: int = 200) -> None:
            """Send JSON response."""
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_response(self, status: int, message: str) -> None:
            """Send error response."""
            self._send_json({"message": message}, status)

        def _check_error_mode(self) -> bool:
            """Check error mode and send error response if active."""
            if state.error_mode == "auth":
                self._send_error_response(401, "Bad credentials")
                return True
            if state.error_mode == "rate_limit":
                self._send_error_response(403, "API rate limit exceeded")
                return True
            return False

        def _pr_to_json(self, pr: MockPR) -> dict[str, Any]:
            """Convert PR to JSON response format."""
            result = {
                "number": pr.number,
                "html_url": f"https://github.com/{owner}/{repo}/pull/{pr.number}",
                "base": {"ref": pr.base},
                "head": {"ref": pr.head},
                "title": pr.title,
                "body": pr.body,
                "state": pr.state,
                "draft": pr.draft,
                "merged_at": pr.merged_at,
            }
            stack = state.stack_for_pr(pr.number)
            if stack is not None:
                result["stack"] = {
                    "id": stack.number,
                    "number": stack.number,
                    "size": len(stack.pull_requests),
                    "position": stack.pull_requests.index(pr.number) + 1,
                    "base": {"ref": stack.base, "sha": "0" * 40},
                }
            else:
                result["stack"] = None
            return result

        def _stack_to_json(self, stack: MockStack) -> dict[str, Any]:
            """Convert a native stack to GitHub's preview response shape."""
            return {
                "id": stack.number,
                "number": stack.number,
                "node_id": f"STACK_{stack.number}",
                "url": (
                    f"https://api.github.com/repos/{owner}/{repo}/stacks/{stack.number}"
                ),
                "base": {"ref": stack.base, "sha": "0" * 40},
                "open": any(
                    state.prs[number].state == "open" for number in stack.pull_requests
                ),
                "created_at": "2026-07-30T00:00:00Z",
                "pull_requests": [
                    {
                        "number": number,
                        "state": state.prs[number].state,
                        "draft": state.prs[number].draft,
                        "merged_at": state.prs[number].merged_at,
                        "head": {
                            "ref": state.prs[number].head,
                            "sha": f"{number:040x}",
                        },
                    }
                    for number in stack.pull_requests
                ],
            }

        def do_GET(self) -> None:
            """Handle GET requests."""
            if self._check_error_mode():
                return

            # Parse URL
            parsed = urlparse(self.path)
            path = parsed.path
            query_params = parse_qs(parsed.query)

            stacks_list_match = re.match(rf"^/repos/{owner}/{repo}/stacks$", path)
            if stacks_list_match:
                pull_request = query_params.get("pull_request")
                stacks = list(state.stacks.values())
                if pull_request:
                    number = int(pull_request[0])
                    stacks = [
                        stack for stack in stacks if number in stack.pull_requests
                    ]
                self._send_json([self._stack_to_json(stack) for stack in stacks])
                return

            stack_get_match = re.match(rf"^/repos/{owner}/{repo}/stacks/(\d+)$", path)
            if stack_get_match:
                stack_number = int(stack_get_match.group(1))
                stack = state.stacks.get(stack_number)
                if stack is None:
                    self._send_error_response(404, "Not Found")
                else:
                    self._send_json(self._stack_to_json(stack))
                return

            # GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state={state}
            pulls_list_match = re.match(rf"^/repos/{owner}/{repo}/pulls$", path)
            if pulls_list_match:
                # Get head param (URL-decoded)
                head_param = query_params.get("head", [""])[0]
                state_param = query_params.get("state", ["open"])[0]

                # Parse head parameter (format: owner:branch)
                if ":" in head_param:
                    _, branch = head_param.split(":", 1)
                else:
                    branch = head_param

                prs = state.get_prs_by_head(branch, state_param)
                self._send_json([self._pr_to_json(pr) for pr in prs])
                return

            # GET /repos/{owner}/{repo}/pulls/{number}
            pr_get_match = re.match(rf"^/repos/{owner}/{repo}/pulls/(\d+)$", path)
            if pr_get_match:
                pr_number = int(pr_get_match.group(1))
                if pr_number in state.prs:
                    self._send_json(self._pr_to_json(state.prs[pr_number]))
                else:
                    self._send_error_response(404, "Not Found")
                return

            self._send_error_response(404, "Not Found")

        def do_POST(self) -> None:
            """Handle POST requests."""
            if self._check_error_mode():
                return

            parsed = urlparse(self.path)
            stack_create_match = re.match(
                rf"^/repos/{owner}/{repo}/stacks$", parsed.path
            )
            if stack_create_match:
                content_length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(content_length))
                stack = state.add_stack(body["pull_requests"])
                self._send_json(self._stack_to_json(stack), 201)
                return

            stack_add_match = re.match(
                rf"^/repos/{owner}/{repo}/stacks/(\d+)/add$", parsed.path
            )
            if stack_add_match:
                stack_number = int(stack_add_match.group(1))
                stack = state.stacks.get(stack_number)
                if stack is None:
                    self._send_error_response(404, "Not Found")
                    return
                content_length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(content_length))
                stack.pull_requests.extend(body["pull_requests"])
                self._send_json(self._stack_to_json(stack))
                return

            stack_unstack_match = re.match(
                rf"^/repos/{owner}/{repo}/stacks/(\d+)/unstack$", parsed.path
            )
            if stack_unstack_match:
                stack_number = int(stack_unstack_match.group(1))
                if stack_number not in state.stacks:
                    self._send_error_response(404, "Not Found")
                    return
                stack = state.stacks[stack_number]
                stack.pull_requests = [
                    number
                    for number in stack.pull_requests
                    if state.prs[number].merged_at is not None
                ]
                if stack.pull_requests:
                    self._send_json(self._stack_to_json(stack))
                else:
                    del state.stacks[stack_number]
                    self.send_response(204)
                    self.end_headers()
                return

            # POST /repos/{owner}/{repo}/pulls
            pulls_create_match = re.match(
                rf"^/repos/{owner}/{repo}/pulls$", parsed.path
            )
            if pulls_create_match:
                content_length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(content_length))

                pr = state.add_pr(
                    head=body["head"],
                    base=body["base"],
                    title=body.get("title", ""),
                    body=body.get("body", ""),
                    draft=body.get("draft", False),
                )
                self._send_json(self._pr_to_json(pr), 201)
                return

            self._send_error_response(404, "Not Found")

        def do_PATCH(self) -> None:
            """Handle PATCH requests."""
            if self._check_error_mode():
                return

            # PATCH /repos/{owner}/{repo}/pulls/{number}
            parsed = urlparse(self.path)
            pr_update_match = re.match(
                rf"^/repos/{owner}/{repo}/pulls/(\d+)$", parsed.path
            )
            if pr_update_match:
                pr_number = int(pr_update_match.group(1))
                if pr_number not in state.prs:
                    self._send_error_response(404, "Not Found")
                    return

                content_length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(content_length))

                pr = state.prs[pr_number]
                if "base" in body:
                    pr.base = body["base"]
                if "body" in body:
                    pr.body = body["body"]
                if "title" in body:
                    pr.title = body["title"]

                self._send_json(self._pr_to_json(pr))
                return

            self._send_error_response(404, "Not Found")

    return MockGitHubHandler


class GitHubMockServer:
    """Mock GitHub API server."""

    def __init__(self, owner: str = "test", repo: str = "repo"):
        self.owner = owner
        self.repo = repo
        self.state = GitHubMockState()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """Get the server port."""
        if self._server is None:
            raise RuntimeError("Server not started")
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        """Get the base URL for the mock server."""
        return f"http://localhost:{self.port}"

    def start(self) -> None:
        """Start the mock server in a background thread."""
        handler = create_mock_handler(self.state, self.owner, self.repo)
        self._server = HTTPServer(("localhost", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever)
        self._thread.daemon = True
        self._thread.start()

    def stop(self) -> None:
        """Stop the mock server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def add_pr(
        self,
        head: str,
        base: str,
        number: int | None = None,
        title: str = "",
        body: str = "",
        draft: bool = False,
    ) -> MockPR:
        """Add a PR to the mock state."""
        return self.state.add_pr(head, base, title, body, number, draft)

    def merge_pr(self, number: int) -> bool:
        """Mark a PR as merged."""
        return self.state.merge_pr(number)

    def add_stack(self, pull_requests: list[int]) -> MockStack:
        """Add a native stack to the mock state."""
        return self.state.add_stack(pull_requests)

    def set_error_mode(self, mode: str | None) -> None:
        """Set error mode: 'auth', 'rate_limit', or None to clear."""
        self.state.error_mode = mode

    def clear_errors(self) -> None:
        """Clear error mode."""
        self.state.error_mode = None

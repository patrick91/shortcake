"""GitHub API client for PR management."""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from shortcake._git._core import Repo
from shortcake._git._pygit2 import get_remote_raw_url, get_remote_url


@dataclass
class PRInfo:
    """Information about a GitHub Pull Request."""

    number: int
    url: str
    base: str
    title: str
    body: str
    state: str  # "open", "closed", "merged"
    is_draft: bool
    head_ref: str | None = None  # Branch name (head ref)
    stack: "PullRequestStackMembership | None" = None


@dataclass(frozen=True)
class PullRequestStackMembership:
    """Native GitHub stack membership included on pull request resources."""

    id: int
    number: int
    size: int
    position: int
    base_ref: str
    base_sha: str


@dataclass(frozen=True)
class NativeStackPullRequest:
    """A pull request entry in GitHub's native stack resource."""

    number: int
    state: str
    is_draft: bool
    merged_at: str | None
    head_ref: str
    head_sha: str

    @property
    def is_open(self) -> bool:
        return self.state == "open" and self.merged_at is None


@dataclass(frozen=True)
class NativeStack:
    """A native GitHub pull request stack."""

    id: int
    number: int
    node_id: str
    url: str
    base_ref: str
    is_open: bool
    created_at: str
    pull_requests: tuple[NativeStackPullRequest, ...]

    @property
    def pr_numbers(self) -> list[int]:
        return [pr.number for pr in self.pull_requests]

    @property
    def open_pr_numbers(self) -> list[int]:
        return [pr.number for pr in self.pull_requests if pr.is_open]


def _parse_pull_request_stack(
    data: dict[str, object] | None,
) -> PullRequestStackMembership | None:
    if not data:
        return None

    base = data["base"]
    if not isinstance(base, dict):  # pragma: no cover - GitHub schema guarantee
        return None

    return PullRequestStackMembership(
        id=int(data["id"]),
        number=int(data["number"]),
        size=int(data["size"]),
        position=int(data["position"]),
        base_ref=str(base["ref"]),
        base_sha=str(base["sha"]),
    )


def _parse_native_stack(data: dict[str, object]) -> NativeStack:
    base = data["base"]
    if not isinstance(base, dict):  # pragma: no cover - GitHub schema guarantee
        raise TypeError("Native stack base must be an object")

    raw_pull_requests = data["pull_requests"]
    if not isinstance(raw_pull_requests, list):  # pragma: no cover
        raise TypeError("Native stack pull_requests must be a list")

    pull_requests: list[NativeStackPullRequest] = []
    for raw_pr in raw_pull_requests:
        if not isinstance(raw_pr, dict):  # pragma: no cover
            raise TypeError("Native stack pull request must be an object")
        head = raw_pr["head"]
        if not isinstance(head, dict):  # pragma: no cover
            raise TypeError("Native stack pull request head must be an object")
        merged_at = raw_pr.get("merged_at")
        pull_requests.append(
            NativeStackPullRequest(
                number=int(raw_pr["number"]),
                state=str(raw_pr["state"]),
                is_draft=bool(raw_pr["draft"]),
                merged_at=str(merged_at) if merged_at is not None else None,
                head_ref=str(head["ref"]),
                head_sha=str(head["sha"]),
            )
        )

    return NativeStack(
        id=int(data["id"]),
        number=int(data["number"]),
        node_id=str(data["node_id"]),
        url=str(data["url"]),
        base_ref=str(base["ref"]),
        is_open=bool(data["open"]),
        created_at=str(data["created_at"]),
        pull_requests=tuple(pull_requests),
    )


@dataclass
class BranchGitHubInfo:
    """Combined GitHub info (PR + CI status) for a branch."""

    pr_number: int | None
    pr_url: str | None
    pr_is_draft: bool
    pr_state: str | None  # "open" | "merged" | None (no PR)
    check_status: str | None  # "success" | "failure" | "pending" | None
    native_stack_number: int | None = None
    native_stack_position: int | None = None
    native_stack_size: int | None = None


def get_github_token() -> str | None:
    """Get GitHub token from environment or gh CLI config.

    Token resolution order:
    1. GH_TOKEN environment variable
    2. GITHUB_TOKEN environment variable
    3. ~/.config/gh/hosts.yml oauth_token field
    4. `gh auth token` subprocess call
    """
    # Check environment variables
    if token := os.environ.get("GH_TOKEN"):
        return token
    if token := os.environ.get("GITHUB_TOKEN"):
        return token

    # Check gh CLI config file
    config_path = Path.home() / ".config" / "gh" / "hosts.yml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            if (
                config
                and "github.com" in config
                and (token := config["github.com"].get("oauth_token"))
            ):
                return token
        except (yaml.YAMLError, OSError, KeyError, TypeError):
            pass

    # Fall back to gh auth token command
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    return None


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo_name) from a GitHub remote URL, or None."""
    # SSH format: git@github.com:owner/repo.git
    ssh_match = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    # SSH URL format: ssh://git@github.com/owner/repo.git
    ssh_url_match = re.match(r"ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_url_match:
        return ssh_url_match.group(1), ssh_url_match.group(2)

    # HTTPS format: https://github.com/owner/repo.git
    https_match = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if https_match:
        return https_match.group(1), https_match.group(2)

    return None


def get_repo_info(repo: Repo) -> tuple[str, str] | None:
    """Extract owner and repo name from origin remote URL.

    Returns (owner, repo_name) or None if cannot be determined.
    Supports:
    - git@github.com:owner/repo.git
    - ssh://git@github.com/owner/repo.git
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo

    Tries the effective URL first (with url.<base>.insteadOf applied), then
    the raw configured URL — the repo identity stays GitHub even when a
    rewrite points the transport somewhere else (e.g. a local mirror).
    """
    url = get_remote_url(repo, "origin")
    if url is not None and (info := _parse_github_url(url)):
        return info

    raw_url = get_remote_raw_url(repo, "origin")
    if raw_url is not None and raw_url != url:
        return _parse_github_url(raw_url)

    return None


class GitHubClient:
    """Client for GitHub REST API operations."""

    def __init__(self, token: str, owner: str, repo: str, base_url: str | None = None):
        self.owner = owner
        self.repo = repo
        effective_base_url = (
            base_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com"
        )
        self.client = httpx.Client(
            base_url=effective_base_url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    def __enter__(self) -> "GitHubClient":
        self._resolve_repo_identity()
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()

    def _resolve_repo_identity(self) -> None:
        """Check and update owner/repo in case the repo was renamed or transferred.

        GitHub serves API requests for old owner/repo URLs but the
        ``head`` filter on PRs uses the current owner. This queries the
        repo endpoint once to learn the canonical owner/repo.
        """
        try:
            response = self.client.get(f"/repos/{self.owner}/{self.repo}")
            if response.status_code == 200:
                data = response.json()
                actual_owner = data.get("owner", {}).get("login")
                actual_repo = data.get("name")
                if actual_owner:
                    self.owner = actual_owner
                if actual_repo:
                    self.repo = actual_repo
        except httpx.RequestError:
            pass  # Network error, proceed with original values

    def get_pr_for_branch(self, branch: str) -> PRInfo | None:
        """Find an existing open PR for the given branch.

        Returns PRInfo if an open PR is found, None otherwise.
        Closed PRs are ignored.
        """
        response = self.client.get(
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={"head": f"{self.owner}:{branch}", "state": "open"},
        )
        response.raise_for_status()

        prs = response.json()
        if not prs:
            return None

        pr = prs[0]
        return PRInfo(
            number=pr["number"],
            url=pr["html_url"],
            base=pr["base"]["ref"],
            title=pr["title"],
            body=pr["body"] or "",
            state=pr["state"],
            is_draft=pr.get("draft", False),
            head_ref=pr.get("head", {}).get("ref"),
            stack=_parse_pull_request_stack(pr.get("stack")),
        )

    def get_pr_by_number(self, pr_number: int) -> PRInfo | None:
        """Get PR info by PR number.

        Returns PRInfo if PR exists, None if not found (404).
        Raises httpx.HTTPStatusError for other errors.
        """
        response = self.client.get(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()

        pr = response.json()
        return PRInfo(
            number=pr["number"],
            url=pr["html_url"],
            base=pr["base"]["ref"],
            title=pr["title"],
            body=pr["body"] or "",
            state=pr["state"],
            is_draft=pr.get("draft", False),
            head_ref=pr["head"]["ref"],
            stack=_parse_pull_request_stack(pr.get("stack")),
        )

    def get_check_status(self, branch: str) -> str | None:
        """Get combined CI check status for a branch's HEAD.

        Returns "success", "failure", "pending", or None (no checks).
        """
        try:
            response = self.client.get(
                f"/repos/{self.owner}/{self.repo}/commits/{branch}/check-runs",
                params={"per_page": 100},
            )
            if response.status_code != 200:
                return None
            data = response.json()
            check_runs = data.get("check_runs", [])
            if not check_runs:
                return None

            statuses = [
                run.get("conclusion") or run.get("status") for run in check_runs
            ]
            fail = ("failure", "timed_out", "cancelled")
            pend = ("in_progress", "queued", "pending", "waiting")
            if any(s in fail for s in statuses):
                return "failure"
            if any(s in pend for s in statuses):
                return "pending"
            if all(s in ("success", "skipped", "neutral") for s in statuses):
                return "success"
            return "pending"
        except (httpx.RequestError, KeyError):
            return None

    def get_branch_github_info(self, branch: str) -> BranchGitHubInfo:
        """Get combined PR + CI info for a branch.

        Falls back to a merged PR when no open PR exists, so the UI can flag
        merged branches as cleanup candidates.
        """
        pr = self.get_pr_for_branch(branch)
        check_status = self.get_check_status(branch)
        if pr is not None:
            return BranchGitHubInfo(
                pr_number=pr.number,
                pr_url=pr.url,
                pr_is_draft=pr.is_draft,
                pr_state="open",
                check_status=check_status,
                native_stack_number=pr.stack.number if pr.stack else None,
                native_stack_position=pr.stack.position if pr.stack else None,
                native_stack_size=pr.stack.size if pr.stack else None,
            )

        merged_number = self.get_merged_pr_number(branch)
        if merged_number is not None:
            return BranchGitHubInfo(
                pr_number=merged_number,
                pr_url=f"https://github.com/{self.owner}/{self.repo}/pull/{merged_number}",
                pr_is_draft=False,
                pr_state="merged",
                check_status=check_status,
            )

        return BranchGitHubInfo(
            pr_number=None,
            pr_url=None,
            pr_is_draft=False,
            pr_state=None,
            check_status=check_status,
        )

    def has_merged_pr(self, branch: str) -> bool:
        """Check if the branch has a merged PR.

        Returns True if a merged PR exists for this branch.
        """
        response = self.client.get(
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={"head": f"{self.owner}:{branch}", "state": "closed"},
        )
        response.raise_for_status()

        prs = response.json()
        return any(pr.get("merged_at") is not None for pr in prs)

    def get_merged_pr_number(self, branch: str) -> int | None:
        """Get the PR number for a merged PR on this branch.

        Returns the PR number if a merged PR exists, None otherwise.
        """
        number, is_merged = self.get_closed_pr_info(branch)
        if is_merged:
            return number
        return None

    def get_merged_pr_base(self, branch: str) -> str | None:
        """Get the base (target) branch of a merged PR for the given head branch.

        Returns the base branch name if a merged PR exists, None otherwise.
        Useful for resolving the effective parent when a branch was merged and deleted.
        """
        response = self.client.get(
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={"head": f"{self.owner}:{branch}", "state": "closed"},
        )
        response.raise_for_status()

        prs = response.json()
        for pr in prs:
            if pr.get("merged_at") is not None:
                return pr["base"]["ref"]
        return None

    def get_closed_pr_info(self, branch: str) -> tuple[int | None, bool]:
        """Get PR info for a closed PR on this branch.

        Returns (pr_number, is_merged). Prefers merged PRs over closed ones.
        Returns (None, False) if no closed PR exists.
        """
        response = self.client.get(
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={"head": f"{self.owner}:{branch}", "state": "closed"},
        )
        response.raise_for_status()

        prs = response.json()
        if not prs:
            return None, False

        # Prefer merged over just closed
        for pr in prs:
            if pr.get("merged_at") is not None:
                return pr["number"], True

        # Return first closed (not merged) PR
        return prs[0]["number"], False

    def create_pr(
        self, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> PRInfo:
        """Create a new pull request.

        Returns PRInfo for the created PR.
        Raises httpx.HTTPStatusError on failure.
        """
        response = self.client.post(
            f"/repos/{self.owner}/{self.repo}/pulls",
            json={
                "head": head,
                "base": base,
                "title": title,
                "body": body,
                "draft": draft,
            },
        )
        response.raise_for_status()

        pr = response.json()
        return PRInfo(
            number=pr["number"],
            url=pr["html_url"],
            base=pr["base"]["ref"],
            title=pr["title"],
            body=pr["body"] or "",
            state=pr["state"],
            is_draft=pr.get("draft", False),
            head_ref=pr.get("head", {}).get("ref"),
            stack=_parse_pull_request_stack(pr.get("stack")),
        )

    def list_stacks(self, pull_request: int | None = None) -> list[NativeStack]:
        """List native GitHub stacks, optionally filtering by PR number.

        GitHub returns 404 while stacked pull requests are unavailable for a
        repository; callers use that response for capability detection.
        """
        params = {"pull_request": pull_request} if pull_request is not None else None
        response = self.client.get(
            f"/repos/{self.owner}/{self.repo}/stacks",
            params=params,
        )
        response.raise_for_status()
        return [_parse_native_stack(stack) for stack in response.json()]

    def get_stack(self, stack_number: int) -> NativeStack | None:
        """Get a native stack, returning None when it no longer exists."""
        response = self.client.get(
            f"/repos/{self.owner}/{self.repo}/stacks/{stack_number}",
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _parse_native_stack(response.json())

    def create_stack(self, pull_requests: list[int]) -> NativeStack:
        """Create a native stack from PR numbers ordered bottom-to-top."""
        response = self.client.post(
            f"/repos/{self.owner}/{self.repo}/stacks",
            json={"pull_requests": pull_requests},
        )
        response.raise_for_status()
        return _parse_native_stack(response.json())

    def add_to_stack(self, stack_number: int, pull_requests: list[int]) -> NativeStack:
        """Append PR numbers to the top of a native stack."""
        response = self.client.post(
            f"/repos/{self.owner}/{self.repo}/stacks/{stack_number}/add",
            json={"pull_requests": pull_requests},
        )
        response.raise_for_status()
        return _parse_native_stack(response.json())

    def unstack(self, stack_number: int) -> NativeStack | None:
        """Remove every unmerged PR from a native stack.

        GitHub returns 204 when that dissolves the stack, otherwise it returns
        the remaining stack containing merged or queued PRs.
        """
        response = self.client.post(
            f"/repos/{self.owner}/{self.repo}/stacks/{stack_number}/unstack",
        )
        response.raise_for_status()
        if response.status_code == 204:
            return None
        return _parse_native_stack(response.json())

    def update_pr(
        self,
        pr_number: int,
        base: str | None = None,
        body: str | None = None,
        title: str | None = None,
    ) -> None:
        """Update an existing pull request.

        Only non-None parameters are updated.
        Raises httpx.HTTPStatusError on failure.
        """
        data: dict[str, str] = {}
        if base is not None:
            data["base"] = base
        if body is not None:
            data["body"] = body
        if title is not None:
            data["title"] = title

        if data:
            response = self.client.patch(
                f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
                json=data,
            )
            response.raise_for_status()


def push_branch(
    repo: Repo, branch: str, force_with_lease: bool = True
) -> tuple[bool, str | None]:
    """Push a branch to origin with force-with-lease semantics.

    Uses git CLI for pushing. When force_with_lease is True, checks that
    the remote ref hasn't changed since we last fetched before force pushing.

    Args:
        repo: The repository.
        branch: Branch name to push.
        force_with_lease: If True (default), only force-push if the remote
            ref matches our local tracking ref. This prevents overwriting
            work that someone else has pushed.

    Returns:
        Tuple of (success, error_message). On success, error_message is None.
        On failure, error_message contains the reason for failure.
    """
    try:
        if force_with_lease:
            # Get our local tracking ref - what we expect remote to be
            tracking_ref_name = f"refs/remotes/origin/{branch}"
            tracking_ref_obj = repo.references.get(tracking_ref_name)
            expected_remote_sha = (
                str(tracking_ref_obj.target).encode() if tracking_ref_obj else None
            )

            if expected_remote_sha is not None:
                # Get origin URL for ls_remote
                origin_url = get_remote_url(repo, "origin")
                if origin_url is None:  # pragma: no cover
                    return (False, "No origin remote configured")

                # Check current remote ref via git ls-remote
                ls_result = subprocess.run(
                    [
                        "git",
                        "ls-remote",
                        "--heads",
                        "--quiet",
                        origin_url,
                        f"refs/heads/{branch}",
                    ],
                    capture_output=True,
                    text=True,
                )
                if ls_result.returncode != 0:
                    return (
                        False,
                        "failed to check remote ref (ls-remote failed)",
                    )

                actual_remote_sha = None
                if ls_result.stdout.strip():
                    # Format: "<sha>\trefs/heads/<branch>"
                    actual_remote_sha = ls_result.stdout.split()[0].encode()

                # If remote exists and differs from our expectation, abort
                if (
                    actual_remote_sha is not None
                    and actual_remote_sha != expected_remote_sha
                ):
                    return (
                        False,
                        "remote has diverged (use --force to overwrite)",
                    )

        # Proceed with force push via git CLI
        result = subprocess.run(
            ["git", "push", "origin", f"refs/heads/{branch}", "--force"],
            cwd=repo.workdir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (False, result.stderr.strip() or "Push failed")
        return (True, None)
    except Exception as e:  # pragma: no cover
        return (False, str(e))

"""GitHub API client for PR management."""

import io
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from dulwich import porcelain
from dulwich.repo import Repo


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


@dataclass
class BranchGitHubInfo:
    """Combined GitHub info (PR + CI status) for a branch."""

    pr_number: int | None
    pr_url: str | None
    pr_is_draft: bool
    check_status: str | None  # "success" | "failure" | "pending" | None


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


def get_repo_info(repo: Repo) -> tuple[str, str] | None:
    """Extract owner and repo name from origin remote URL.

    Returns (owner, repo_name) or None if cannot be determined.
    Supports:
    - git@github.com:owner/repo.git
    - ssh://git@github.com/owner/repo.git
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo
    """
    config = repo.get_config()
    try:
        url = config.get((b"remote", b"origin"), b"url").decode()
    except KeyError:
        return None

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
        """Get combined PR + CI info for a branch."""
        pr = self.get_pr_for_branch(branch)
        check_status = self.get_check_status(branch)
        return BranchGitHubInfo(
            pr_number=pr.number if pr else None,
            pr_url=pr.url if pr else None,
            pr_is_draft=pr.is_draft if pr else False,
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
        )

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

    Uses dulwich for pushing. When force_with_lease is True, checks that
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
            tracking_ref_name = f"refs/remotes/origin/{branch}".encode()
            try:
                expected_remote_sha = repo.refs[tracking_ref_name]
            except KeyError:
                # No tracking ref means this is a new branch, allow push
                expected_remote_sha = None

            if expected_remote_sha is not None:
                # Get origin URL for ls_remote
                config = repo.get_config()
                origin_url = config.get((b"remote", b"origin"), b"url").decode()

                # Check current remote ref (quiet=True suppresses server messages)
                remote_result = porcelain.ls_remote(origin_url, quiet=True)
                remote_ref_name = f"refs/heads/{branch}".encode()
                actual_remote_sha = remote_result.refs.get(remote_ref_name)

                # If remote exists and differs from our expectation, abort
                if (
                    actual_remote_sha is not None
                    and actual_remote_sha != expected_remote_sha
                ):
                    return (
                        False,
                        "remote has diverged (use --force to overwrite)",
                    )

        # Proceed with force push (suppress server messages)
        porcelain.push(
            repo,
            "origin",
            refspecs=[f"refs/heads/{branch}"],
            force=True,
            outstream=io.BytesIO(),
            errstream=io.BytesIO(),
        )
        return (True, None)
    except Exception as e:  # pragma: no cover
        return (False, str(e))

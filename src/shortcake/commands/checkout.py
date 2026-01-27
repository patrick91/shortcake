"""Checkout command - smart checkout for branches and PRs."""

from dataclasses import dataclass
from typing import Annotated

import httpx
import typer
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._github import GitHubClient, get_github_token, get_repo_info
from shortcake.commands.adopt import AdoptError, _adopt


class CheckoutError(ShortcakeError):
    """Error during checkout operation."""

    pass


@dataclass
class CheckoutResult:
    """Result of checkout operation."""

    branch: str
    from_remote: bool = False
    adopted: bool = False
    pr_number: int | None = None


def _fetch_branch(repo: Repo, branch: str) -> bool:
    """Fetch from origin to get updates for the branch.

    Returns True if fetch succeeded, False otherwise.
    Note: dulwich's porcelain.fetch fetches all refs, which is fine.
    The branch parameter is reserved for future selective fetch.
    """
    try:
        porcelain.fetch(
            repo,
            "origin",
            quiet=True,
        )
        return True
    except Exception:  # pragma: no cover
        return False


def _create_branch_from_remote(repo: Repo, branch: str) -> bool:
    """Create local branch from remote tracking branch.

    Returns True if successful, False otherwise.
    """
    remote_ref = f"refs/remotes/origin/{branch}".encode()
    try:
        remote_sha = repo.refs[remote_ref]
        local_ref = f"refs/heads/{branch}".encode()
        repo.refs[local_ref] = remote_sha
        return True
    except KeyError:
        return False


def _checkout(
    repo: Repo,
    target: str,
    adopt: bool = True,
) -> CheckoutResult:
    """
    Smart checkout - handles local branches, remote branches, and PR numbers.

    Args:
        repo: The git repository.
        target: Branch name or PR number (as string).
        adopt: If True, adopt untracked branches after checkout.

    Returns:
        CheckoutResult with details of what was done.

    Raises:
        CheckoutError on failure.
    """
    branch: str
    pr_number: int | None = None
    from_remote = False

    # Check if target is a PR number
    if target.isdigit():
        pr_number = int(target)
        # Need GitHub API to resolve PR number to branch
        token = get_github_token()
        if not token:
            raise CheckoutError(
                "Cannot checkout by PR number without GitHub token. "
                "Run 'gh auth login' or set GH_TOKEN."
            )

        repo_info = get_repo_info(repo)
        if not repo_info:
            raise CheckoutError("Cannot determine GitHub repo from origin URL.")

        owner, repo_name = repo_info
        with GitHubClient(token, owner, repo_name) as gh:
            try:
                pr = gh.get_pr_by_number(pr_number)
            except httpx.HTTPStatusError as e:
                raise CheckoutError(
                    f"GitHub API error: {e.response.status_code}"
                ) from None

            if not pr:
                raise CheckoutError(f"PR #{pr_number} not found.")

            if not pr.head_ref:
                raise CheckoutError(
                    f"PR #{pr_number} has no head branch (may be from a fork)."
                )

            branch = pr.head_ref
    else:
        branch = target

    # Check if branch exists locally
    if git.branch_exists(repo, branch):
        # Just switch to it
        git.switch_branch(repo, branch)
        result = CheckoutResult(branch=branch, pr_number=pr_number)

        # Check if it's tracked, offer to adopt if not
        if adopt:
            all_branches = set(git.get_all_local_branches(repo))
            parent = git.get_branch_parent(repo, branch, all_branches)
            if parent is None:
                # Not tracked - try to adopt
                default_branch = git.get_default_branch(repo)
                if default_branch and branch != default_branch:
                    try:
                        _adopt(repo, branch)
                        result.adopted = True
                    except AdoptError:
                        # Can't adopt (no commits relative to parent, etc.)
                        pass

        return result

    # Branch doesn't exist locally - try to fetch from remote
    if not git.has_remote(repo):
        raise CheckoutError(
            f"Branch '{branch}' not found locally and no remote configured."
        )

    # Fetch the branch
    if not _fetch_branch(repo, branch):
        raise CheckoutError(f"Branch '{branch}' not found locally or on remote.")

    # Check if we got it
    remote_ref = git.get_remote_ref(repo, f"origin/{branch}")
    if not remote_ref:
        raise CheckoutError(f"Branch '{branch}' not found on remote.")

    # Create local branch from remote
    if not _create_branch_from_remote(repo, branch):
        raise CheckoutError(f"Failed to create local branch '{branch}' from remote.")

    from_remote = True

    # Switch to the new branch
    git.switch_branch(repo, branch)

    result = CheckoutResult(
        branch=branch,
        from_remote=from_remote,
        pr_number=pr_number,
    )

    # Try to adopt the branch
    if adopt:
        default_branch = git.get_default_branch(repo)
        if default_branch and branch != default_branch:
            try:
                _adopt(repo, branch)
                result.adopted = True
            except AdoptError:
                # Can't adopt - that's okay
                pass

    return result


# Typer command


def checkout(
    target: Annotated[
        str,
        typer.Argument(help="Branch name or PR number"),
    ],
    no_adopt: Annotated[
        bool,
        typer.Option("--no-adopt", help="Don't adopt untracked branches"),
    ] = False,
) -> None:
    """Checkout a branch by name or PR number.

    If the branch exists locally, switches to it.
    If not, fetches from remote and creates a local branch.
    By default, adopts untracked branches to enable stack tracking.
    """
    repo = git.open_repo()

    # Check for uncommitted changes
    if git.has_uncommitted_changes(repo):
        typer.echo("Warning: You have uncommitted changes.", err=True)

    try:
        result = _checkout(repo, target, adopt=not no_adopt)
    except CheckoutError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Build output message
    if result.pr_number:
        typer.echo(f"Checked out PR #{result.pr_number} ({result.branch})")
    elif result.from_remote:
        typer.echo(f"Checked out '{result.branch}' from remote")
    else:
        typer.echo(f"Switched to '{result.branch}'")

    if result.adopted:
        typer.echo(f"  Adopted '{result.branch}' for stack tracking")


# Alias
co = checkout

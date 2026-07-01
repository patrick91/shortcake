"""Checkout command - smart checkout for branches and PRs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import httpx
import typer

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._git._pygit2 import fetch_remote
from shortcake._github import GitHubClient, get_github_token, get_repo_info


class CheckoutError(ShortcakeError):
    """Error during checkout operation."""

    pass


@dataclass
class CheckoutResult:
    """Result of checkout operation."""

    branch: str
    from_remote: bool = False
    pr_number: int | None = None
    worktree_paths: list[str] | None = None


def _other_worktrees_for_branch(repo: Repo, branch: str) -> list[str]:
    """Return display paths for non-current worktrees that have branch checked out."""
    current_path = Path(repo.workdir).resolve()
    return [
        git.format_worktree_path(path)
        for path in sorted(git.get_branch_worktrees(repo).get(branch, []), key=str)
        if path.resolve() != current_path
    ]


def _fetch_branch(repo: Repo, branch: str) -> bool:
    """Fetch from origin to get updates for the branch.

    Returns True if fetch succeeded, False otherwise.
    The branch parameter is reserved for future selective fetch.
    """
    return fetch_remote(repo, "origin")


def _create_branch_from_remote(repo: Repo, branch: str) -> bool:
    """Create local branch from remote tracking branch.

    Returns True if successful, False otherwise.
    """
    import pygit2

    remote_ref_name = f"refs/remotes/origin/{branch}"
    try:
        remote_ref_obj = repo.references.get(remote_ref_name)
        if remote_ref_obj is None:
            return False
        remote_oid = remote_ref_obj.target
        local_ref_name = f"refs/heads/{branch}"
        if local_ref_name in repo.references:  # pragma: no cover
            repo.references[local_ref_name].set_target(remote_oid)
        else:
            repo.references.create(local_ref_name, remote_oid)
        return True
    except (KeyError, pygit2.GitError):  # pragma: no cover
        return False


def _checkout(
    repo: Repo,
    target: str,
) -> CheckoutResult:
    """
    Smart checkout - handles local branches, remote branches, and PR numbers.

    Args:
        repo: The git repository.
        target: Branch name or PR number (as string).

    Returns:
        CheckoutResult with details of what was done.

    Raises:
        CheckoutError on failure.
    """
    branch: str
    pr_number: int | None = None

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
        worktree_paths = _other_worktrees_for_branch(repo, branch)
        if worktree_paths:
            return CheckoutResult(
                branch=branch,
                pr_number=pr_number,
                worktree_paths=worktree_paths,
            )
        # Just switch to it
        git.switch_branch(repo, branch)
        return CheckoutResult(branch=branch, pr_number=pr_number)

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

    # Switch to the new branch
    git.switch_branch(repo, branch, ignore_other_worktrees=True)

    return CheckoutResult(
        branch=branch,
        from_remote=True,
        pr_number=pr_number,
    )


# Typer command


def checkout(
    target: Annotated[
        str,
        typer.Argument(help="Branch name or PR number"),
    ],
) -> None:
    """Checkout a branch by name or PR number.

    If the branch exists locally, switches to it.
    If not, fetches from remote and creates a local branch.
    """
    repo = git.open_repo()

    has_uncommitted = git.has_uncommitted_changes(repo)

    try:
        result = _checkout(repo, target)
    except CheckoutError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if has_uncommitted and not result.worktree_paths:
        typer.echo("Warning: You have uncommitted changes.", err=True)

    # Build output message
    if result.pr_number:
        if result.worktree_paths:
            typer.echo(
                f"PR #{result.pr_number} ({result.branch}) is checked out in "
                "another worktree:"
            )
        else:
            typer.echo(f"Checked out PR #{result.pr_number} ({result.branch})")
    elif result.worktree_paths:
        typer.echo(f"Branch '{result.branch}' is checked out in another worktree:")
    elif result.from_remote:
        typer.echo(f"Checked out '{result.branch}' from remote")
    else:
        typer.echo(f"Switched to '{result.branch}'")

    if result.worktree_paths:
        for path in result.worktree_paths:
            typer.echo(f"  {path}")
        if len(result.worktree_paths) == 1:
            typer.echo(f"cd {result.worktree_paths[0]}")


# Alias
co = checkout

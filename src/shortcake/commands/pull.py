"""Pull command - update current branch from remote."""

import os
import subprocess
from dataclasses import dataclass
from typing import Annotated

import typer
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError


class PullError(ShortcakeError):
    """Error during pull operation."""

    pass


@dataclass
class PullResult:
    """Result of pull operation."""

    branch: str
    already_up_to_date: bool = False
    fast_forwarded: bool = False
    rebased: bool = False
    new_sha: str | None = None


def _fetch(repo: Repo) -> bool:
    """Fetch from origin.

    Returns True if fetch succeeded, False otherwise.
    """
    if not git.has_remote(repo, "origin"):
        return False

    try:
        with open(os.devnull, "wb") as devnull:
            porcelain.fetch(repo, "origin", quiet=True, errstream=devnull)
        return True
    except git.DULWICH_IO_ERRORS:
        return False


def _rebase_onto_remote(repo: Repo, branch: str) -> bool:
    """Rebase current branch onto origin/branch.

    Returns True if successful, False if conflict.
    """
    result = subprocess.run(
        ["git", "rebase", f"origin/{branch}"],
        cwd=repo.path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _pull(
    repo: Repo,
    rebase: bool = False,
) -> PullResult:
    """
    Update current branch from remote.

    Args:
        repo: The git repository.
        rebase: If True, rebase when fast-forward not possible.

    Returns:
        PullResult with details of what was done.

    Raises:
        PullError on failure.
    """
    # Get current branch
    branch = git.get_current_branch(repo)
    if branch is None:
        raise PullError("Not on a branch (detached HEAD).")

    # Check for uncommitted changes
    if git.has_uncommitted_changes(repo):
        raise PullError("You have uncommitted changes. Commit or stash them first.")

    # Check if rebase already in progress
    if git.is_rebase_in_progress(repo):
        raise PullError("Git rebase in progress. Complete or abort it first.")

    # Check if remote exists
    if not git.has_remote(repo, "origin"):
        raise PullError("No remote 'origin' configured.")

    # Fetch from origin
    if not _fetch(repo):
        raise PullError("Failed to fetch from origin.")

    # Check if remote tracking branch exists
    remote_ref = git.get_remote_ref(repo, f"origin/{branch}")
    if remote_ref is None:
        raise PullError(
            f"No remote tracking branch 'origin/{branch}'. "
            f"Push your branch first with 'git push -u origin {branch}'."
        )

    # Get local branch head
    local_ref = f"refs/heads/{branch}".encode()
    local_sha = repo.refs[local_ref]

    # Already up to date?
    if local_sha == remote_ref:
        return PullResult(branch=branch, already_up_to_date=True)

    # Can we fast-forward? (local is ancestor of remote)
    if git.is_ancestor(repo, local_sha, remote_ref):
        # Fast-forward
        repo.refs[local_ref] = remote_ref
        # Update working directory
        git.switch_branch(repo, branch)
        return PullResult(
            branch=branch,
            fast_forwarded=True,
            new_sha=remote_ref[:7].decode(),
        )

    # Branches have diverged
    if not rebase:
        raise PullError(
            f"Branch '{branch}' has diverged from 'origin/{branch}'. "
            "Use --rebase to rebase onto the remote branch."
        )

    # Rebase onto remote
    if not _rebase_onto_remote(repo, branch):
        raise PullError(
            "Conflict during rebase. Resolve conflicts and run "
            "'git rebase --continue', or run 'sc abort' to abort."
        )

    return PullResult(
        branch=branch,
        rebased=True,
        new_sha=git.get_branch_head(repo, branch)[:7].decode(),
    )


# Typer command


def pull(
    no_rebase: Annotated[
        bool,
        typer.Option("--no-rebase", help="Fail instead of rebasing when diverged"),
    ] = False,
) -> None:
    """Update current branch from remote.

    Fetches from origin and updates the current branch. If the branch has
    diverged (common after amending), automatically rebases local commits
    onto the remote.
    """
    repo = git.open_repo()

    try:
        result = _pull(repo, rebase=not no_rebase)
    except PullError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.already_up_to_date:
        typer.echo("Already up to date.")
    elif result.fast_forwarded:
        typer.echo(f"Fast-forwarded '{result.branch}' to {result.new_sha}")
    elif result.rebased:
        typer.echo(
            f"Rebased '{result.branch}' onto origin/{result.branch} ({result.new_sha})"
        )

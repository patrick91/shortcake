"""Pull command - update current branch from remote."""

import subprocess
from dataclasses import dataclass, field
from typing import Annotated

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake.commands.restack import RestackResult, _get_stack_in_order, _restack


class PullError(ShortcakeError):
    """Error during pull operation."""

    pass


@dataclass
class PullResult:
    """Result of pull operation."""

    branch: str
    already_up_to_date: bool = False
    fast_forwarded: bool = False
    reset: bool = False
    rebased: bool = False
    new_sha: str | None = None


@dataclass
class BranchPullResult:
    """Per-branch result of stack pull."""

    branch: str
    already_up_to_date: bool = False
    updated: bool = False
    skipped_no_remote: bool = False
    new_sha: str | None = None


@dataclass
class PullStackResult:
    """Aggregate result of pulling an entire stack."""

    branch_results: list[BranchPullResult] = field(default_factory=list)
    restack_result: RestackResult | None = None
    original_branch: str = ""
    is_stack: bool = False


def _fetch(repo: Repo) -> bool:
    """Fetch from origin using git CLI.

    Returns True if fetch succeeded, False otherwise.
    """
    if not git.has_remote(repo, "origin"):
        return False

    result = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=repo.path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _reset_to_remote(repo: Repo, branch: str) -> None:
    """Reset current branch to match origin/branch."""
    subprocess.run(
        ["git", "reset", "--hard", f"origin/{branch}"],
        cwd=repo.path,
        capture_output=True,
        text=True,
        check=True,
    )


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
        rebase: If True, rebase local commits onto remote instead of resetting.

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

    # Branches have diverged - either reset or rebase
    if rebase:
        # Rebase local commits onto remote
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
    else:
        # Reset to remote (default - remote is source of truth)
        _reset_to_remote(repo, branch)
        return PullResult(
            branch=branch,
            reset=True,
            new_sha=remote_ref[:7].decode(),
        )


def _update_branch_from_remote(repo: Repo, branch: str) -> BranchPullResult:
    """Update a single branch ref to match its remote tracking ref.

    Does not update working tree — caller is responsible for that.
    """
    remote_ref = git.get_remote_ref(repo, f"origin/{branch}")
    if remote_ref is None:
        return BranchPullResult(branch=branch, skipped_no_remote=True)

    local_ref = f"refs/heads/{branch}".encode()
    local_sha = repo.refs[local_ref]

    if local_sha == remote_ref:
        return BranchPullResult(branch=branch, already_up_to_date=True)

    # Remote wins — update local ref to match remote
    repo.refs[local_ref] = remote_ref
    return BranchPullResult(
        branch=branch, updated=True, new_sha=remote_ref[:7].decode()
    )


def _pull_stack(repo: Repo) -> PullStackResult:
    """Pull updates for all branches in the current stack.

    1. Precondition checks
    2. Fetch once
    3. Get stack in order
    4. If untracked: fall back to single-branch _pull()
    5. For each branch: update ref to match origin/<branch>
    6. Update working tree for current branch
    7. If any updated: restack
    8. Return to original branch
    """
    # Precondition checks
    current_branch = git.get_current_branch(repo)
    if current_branch is None:
        raise PullError("Not on a branch (detached HEAD).")

    if git.has_uncommitted_changes(repo):
        raise PullError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise PullError("Git rebase in progress. Complete or abort it first.")

    if not git.has_remote(repo, "origin"):
        raise PullError("No remote 'origin' configured.")

    # Fetch once
    if not _fetch(repo):
        raise PullError("Failed to fetch from origin.")

    # Get stack
    stack = _get_stack_in_order(repo, current_branch)

    # If untracked (empty stack): fall back to single-branch behavior
    if not stack:
        single_result = _pull_single_after_fetch(repo, current_branch)
        result = PullStackResult(original_branch=current_branch)
        result.branch_results = [
            BranchPullResult(
                branch=single_result.branch,
                already_up_to_date=single_result.already_up_to_date,
                updated=single_result.fast_forwarded or single_result.reset,
                new_sha=single_result.new_sha,
            )
        ]
        return result

    # Update each branch in the stack
    branch_results = []
    for branch in stack:
        br_result = _update_branch_from_remote(repo, branch)
        branch_results.append(br_result)

    # Update working tree for current branch
    git.switch_branch(repo, current_branch, force=True)

    return PullStackResult(
        branch_results=branch_results,
        restack_result=None,
        original_branch=current_branch,
        is_stack=True,
    )


def _pull_single_after_fetch(repo: Repo, branch: str) -> PullResult:
    """Single-branch pull logic after fetch has already happened.

    Like _pull() but skips precondition checks and fetch (already done).
    """
    remote_ref = git.get_remote_ref(repo, f"origin/{branch}")
    if remote_ref is None:
        raise PullError(
            f"No remote tracking branch 'origin/{branch}'. "
            f"Push your branch first with 'git push -u origin {branch}'."
        )

    local_ref = f"refs/heads/{branch}".encode()
    local_sha = repo.refs[local_ref]

    if local_sha == remote_ref:
        return PullResult(branch=branch, already_up_to_date=True)

    if git.is_ancestor(repo, local_sha, remote_ref):
        repo.refs[local_ref] = remote_ref
        git.switch_branch(repo, branch)
        return PullResult(
            branch=branch, fast_forwarded=True, new_sha=remote_ref[:7].decode()
        )

    # Diverged — reset to remote
    _reset_to_remote(repo, branch)
    return PullResult(branch=branch, reset=True, new_sha=remote_ref[:7].decode())


# Typer command


def pull(
    rebase: Annotated[
        bool,
        typer.Option(
            "--rebase", "-r", help="Rebase local commits onto remote instead of reset"
        ),
    ] = False,
) -> None:
    """Update current branch and its stack from remote.

    Fetches from origin and updates all branches in the current stack.
    If any branch has diverged (common after amending), resets to match remote.
    After updating, restacks to propagate changes. Use --rebase for single-branch
    mode that preserves local commits by rebasing them onto remote.
    """
    repo = git.open_repo()

    # --rebase uses the old single-branch behavior
    if rebase:
        try:
            result = _pull(repo, rebase=True)
        except PullError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None

        if result.already_up_to_date:
            typer.echo("Already up to date.")
        elif result.rebased:
            typer.echo(
                f"Rebased '{result.branch}' onto "
                f"origin/{result.branch} ({result.new_sha})"
            )
        return

    # Stack-aware pull
    try:
        stack_result = _pull_stack(repo)
    except PullError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Format output
    any_updated = False
    for br in stack_result.branch_results:
        if br.updated:
            typer.echo(f"Updated '{br.branch}' to origin/{br.branch} ({br.new_sha})")
            any_updated = True
        elif br.skipped_no_remote:
            typer.echo(f"Skipped '{br.branch}' (no remote tracking branch)")

    if not any_updated:
        n = len(stack_result.branch_results)
        if stack_result.is_stack and n > 1:
            typer.echo(f"Checked {n} branches in stack. Already up to date.")
        else:
            typer.echo("Already up to date.")
        return

    # Restack after printing updates (only for tracked stacks)
    if stack_result.is_stack:
        restack_result = _restack(repo)
        if restack_result.restacked_branches:
            typer.echo(
                f"Restacked {len(restack_result.restacked_branches)} branch(es)."
            )

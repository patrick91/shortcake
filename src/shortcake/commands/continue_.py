from dataclasses import dataclass
from pathlib import Path

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._restack_state import RestackState
from shortcake.commands.restack import (
    RebaseResult,
    _get_conflict_files,
    _needs_restack,
    _rebase_branch,
    _show_conflict_message,
    _show_rebase_error,
)


class ContinueError(ShortcakeError):
    """Error during continue operation."""

    pass


@dataclass
class ContinueResult:
    """Result of continue operation."""

    restacked_branches: list[str]
    conflict_branch: str | None = None


def _apply_remaining_commits(
    repo: Repo, branch: str, merge_base: str, original_head: str, after: bytes | None
) -> RebaseResult:
    """Apply remaining commits after a conflict resolution.

    After resolving a cherry-pick conflict, this function applies any commits
    that come after the resolved commit in the rebase sequence.

    Args:
        repo: The git repository
        branch: Branch being rebased (for error context)
        merge_base: The merge base SHA (commits after this are rebased)
        original_head: Original branch head before rebase started
        after: The commit that was just resolved (skip commits up to and including this)

    Returns:
        RebaseResult indicating success or failure with error details
    """
    try:
        commits = git.get_rebase_commits(repo, original_head, merge_base)
    except (ValueError, KeyError) as e:
        return RebaseResult(success=False, error_output=str(e))
    start_index = 0
    if after is not None:
        try:
            start_index = commits.index(after) + 1
        except ValueError:
            # Resolved commit not in list - state may be inconsistent.
            # Start from beginning; already-applied commits will fail safely.
            typer.echo(
                "Warning: Could not find resolved commit in rebase sequence. "
                "Attempting to continue from the beginning.",
                err=True,
            )
            start_index = 0
    for commit in commits[start_index:]:
        try:
            git.cherry_pick(repo, commit)
        except git.RebaseFailure as e:
            return RebaseResult(success=False, error_output=str(e))
    return RebaseResult(success=True)


def _continue_rebase(repo: Repo | str | Path) -> bool:
    """Continue an in-progress rebase. Returns True if successful."""
    try:
        repo_obj = repo if isinstance(repo, Repo) else git.open_repo(Path(repo))
        git.rebase_continue(repo_obj)
        return True
    except git.RebaseFailure:
        return False


def _continue(repo: Repo) -> ContinueResult:
    """
    Continue an in-progress restack after resolving conflicts.

    Raises ContinueError on failure, returns ContinueResult on success.
    """
    # Check if restack is in progress
    if (state := RestackState.load(repo)) is None:
        raise ContinueError("No restack in progress.")

    # Check if current step still needs to be done (user may have manually aborted)
    current_step = state.plan[state.current_index]

    # If git rebase is in progress, continue it first
    if git.is_rebase_in_progress(repo):
        typer.echo("Continuing rebase...")
        conflict_head = git.get_cherry_pick_head(repo)
        if not _continue_rebase(repo):
            # Still has conflicts
            conflict_files = _get_conflict_files(repo)
            _show_conflict_message(
                current_step.branch, current_step.onto, conflict_files
            )
            return ContinueResult(
                restacked_branches=[], conflict_branch=current_step.branch
            )

        if conflict_head is not None:
            result = _apply_remaining_commits(
                repo,
                current_step.branch,
                current_step.merge_base,
                state.original_refs[current_step.branch],
                conflict_head,
            )
            if not result.success:
                if git.is_rebase_in_progress(repo):
                    conflict_files = _get_conflict_files(repo)
                    _show_conflict_message(
                        current_step.branch, current_step.onto, conflict_files
                    )
                else:
                    _show_rebase_error(
                        current_step.branch, current_step.onto, result.error_output
                    )
                return ContinueResult(
                    restacked_branches=[], conflict_branch=current_step.branch
                )

    # Check if parent branch still exists (may have been deleted during resolution)
    if not git.branch_exists(repo, current_step.onto):
        raise ContinueError(
            f"Parent branch '{current_step.onto}' no longer exists. "
            f"It may have been deleted during conflict resolution. "
            f"Run 'sc abort' to cancel and restore original state, "
            f"then recreate the parent branch or update the Shortcake-Parent trailer."
        )

    if _needs_restack(repo, current_step.branch, current_step.onto):
        raise ContinueError(
            f"Branch '{current_step.branch}' was not rebased onto "
            f"'{current_step.onto}'. The rebase may have been manually aborted. "
            "Run 'sc abort' to clean up, then 'sc restack' to restart."
        )

    # Continue with remaining branches
    restacked = [current_step.branch]  # Current step completed
    for i in range(state.current_index + 1, len(state.plan)):
        step = state.plan[i]
        state.current_index = i
        state.save(repo)

        # Check if parent branch still exists
        if not git.branch_exists(repo, step.onto):
            raise ContinueError(
                f"Parent branch '{step.onto}' no longer exists. "
                f"It may have been deleted during conflict resolution. "
                f"Run 'sc abort' to cancel and restore original state, "
                f"then recreate the parent branch or update Shortcake-Parent."
            )

        typer.echo(f"Rebasing '{step.branch}' onto '{step.onto}'...")
        result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)

        if not result.success:
            # Check if this is a conflict or other error
            if git.is_rebase_in_progress(repo):
                conflict_files = _get_conflict_files(repo)
                _show_conflict_message(step.branch, step.onto, conflict_files)
            else:
                _show_rebase_error(step.branch, step.onto, result.error_output)
            return ContinueResult(
                restacked_branches=restacked, conflict_branch=step.branch
            )

        restacked.append(step.branch)

    # Success - clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, state.original_branch, force=True)

    return ContinueResult(restacked_branches=restacked)


# Typer command - named continue_cmd to avoid shadowing builtin


def continue_cmd() -> None:
    """Continue restack after resolving conflicts."""
    repo = git.open_repo()

    try:
        result = _continue(repo)
    except ContinueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.conflict_branch:
        raise typer.Exit(1)

    typer.echo("Restack completed successfully.")

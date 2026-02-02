from dataclasses import dataclass

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._restack_state import RestackState
from shortcake.commands.restack import (
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
    skipped_empty: bool = False


def _continue_rebase(repo: Repo) -> git.RebaseResult:
    """Continue an in-progress git rebase.

    Returns RebaseResult indicating success, conflict, or skipped empty commits.
    """
    return git.rebase_continue(repo)


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
    any_skipped_empty = False

    # If git rebase is in progress, continue it first
    if git.is_rebase_in_progress(repo):
        typer.echo("Continuing rebase...")
        result = _continue_rebase(repo)

        if not result.success:
            # Still has conflicts or other error
            if result.conflict:
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

        if result.skipped_empty:
            typer.echo(
                f"  Skipped empty commit (changes already in '{current_step.onto}')"
            )
            any_skipped_empty = True

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

        if result.skipped_empty:
            typer.echo(f"  Skipped empty commit (changes already in '{step.onto}')")
            any_skipped_empty = True

        restacked.append(step.branch)

    # Success - clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, state.original_branch, force=True)

    return ContinueResult(restacked_branches=restacked, skipped_empty=any_skipped_empty)


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

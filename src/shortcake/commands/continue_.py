import os
import subprocess
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


def _continue_rebase(repo_path: str) -> bool:
    """Continue an in-progress rebase. Returns True if successful."""
    # Set GIT_EDITOR to prevent git from trying to open an editor (fails on CI)
    env = {**os.environ, "GIT_EDITOR": "true"}
    result = subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode == 0


def _continue(repo: Repo) -> ContinueResult:
    """
    Continue an in-progress restack after resolving conflicts.

    Raises ContinueError on failure, returns ContinueResult on success.
    """
    # Check if restack is in progress
    state = RestackState.load(repo)
    if state is None:
        raise ContinueError("No restack in progress.")

    # If git rebase is in progress, continue it first
    if git.is_rebase_in_progress(repo):
        typer.echo("Continuing rebase...")
        if not _continue_rebase(repo.path):
            # Still has conflicts
            conflict_files = _get_conflict_files(repo.path)
            current_step = state.plan[state.current_index]
            _show_conflict_message(
                current_step.branch, current_step.onto, conflict_files
            )
            return ContinueResult(
                restacked_branches=[], conflict_branch=current_step.branch
            )

    # Check if current step still needs to be done (user may have manually aborted)
    current_step = state.plan[state.current_index]

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
            f"Run 'sc restack' to restart."
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
        result = _rebase_branch(repo.path, step.branch, step.onto, step.merge_base)

        if not result.success:
            # Check if this is a conflict or other error
            if git.is_rebase_in_progress(repo):
                conflict_files = _get_conflict_files(repo.path)
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
    git.switch_branch(repo, state.original_branch)

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

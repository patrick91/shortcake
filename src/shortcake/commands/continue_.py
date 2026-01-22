import subprocess
from dataclasses import dataclass

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._restack_state import RestackState
from shortcake.commands.restack import (
    _get_conflict_files,
    _rebase_branch,
    _show_conflict_message,
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
    result = subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _continue(repo: Repo) -> ContinueResult:
    """
    Continue an in-progress restack after resolving conflicts.

    Raises ContinueError on failure, returns ContinueResult on success.
    """
    repo_path = repo.path

    # Check if restack is in progress
    state = RestackState.load(repo)
    if state is None:
        raise ContinueError("No restack in progress.")

    # If git rebase is in progress, continue it first
    if git.is_rebase_in_progress(repo):
        typer.echo("Continuing rebase...")
        if not _continue_rebase(repo_path):
            # Still has conflicts
            conflict_files = _get_conflict_files(repo_path)
            current_step = state.plan[state.current_index]
            _show_conflict_message(
                current_step.branch, current_step.onto, conflict_files
            )
            return ContinueResult(
                restacked_branches=[], conflict_branch=current_step.branch
            )

    # Continue with remaining branches
    restacked = []
    for i in range(state.current_index + 1, len(state.plan)):
        step = state.plan[i]
        state.current_index = i
        state.save(repo)

        typer.echo(f"Rebasing '{step.branch}' onto '{step.onto}'...")
        success = _rebase_branch(repo_path, step.branch, step.onto, step.merge_base)

        if not success:
            conflict_files = _get_conflict_files(repo_path)
            _show_conflict_message(step.branch, step.onto, conflict_files)
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

from dataclasses import dataclass

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git import RebaseFailure
from shortcake._restack_state import RestackState


class AbortError(ShortcakeError):
    """Error during abort operation."""

    pass


@dataclass
class AbortResult:
    """Result of abort operation."""

    restored_branches: list[str]


def _abort(repo: Repo) -> AbortResult:
    """
    Abort an in-progress restack and restore original state.

    Raises AbortError on failure, returns AbortResult on success.
    """
    # Check if restack is in progress
    state = RestackState.load(repo)
    if state is None:
        raise AbortError("No restack in progress.")

    # If git rebase is in progress, abort it first
    if git.is_rebase_in_progress(repo):
        typer.echo("Aborting in-progress rebase...")
        try:
            git.rebase_abort(repo)
        except RebaseFailure:
            typer.echo(
                "Warning: Failed to abort git rebase. You may need to run "
                "'git rebase --abort' manually.",
                err=True,
            )

    # Restore original refs
    restored = []
    for branch, sha_hex in state.original_refs.items():
        git.update_branch(repo, branch, sha_hex)
        restored.append(branch)

    # Clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, state.original_branch, force=True)

    return AbortResult(restored_branches=restored)


# Typer command


def abort() -> None:
    """Abort restack and restore original state."""
    repo = git.open_repo()

    try:
        _abort(repo)
    except AbortError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo("Restack aborted. Restored original branch state.")

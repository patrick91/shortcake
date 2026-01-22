import subprocess
from dataclasses import dataclass

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._restack_state import RestackState


class AbortError(ShortcakeError):
    """Error during abort operation."""

    pass


@dataclass
class AbortResult:
    """Result of abort operation."""

    restored_branches: list[str]


def _abort_rebase(repo_path: str) -> bool:
    """Abort an in-progress rebase. Returns True if successful."""
    result = subprocess.run(
        ["git", "rebase", "--abort"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


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
        if not _abort_rebase(repo.path):
            typer.echo(
                "Warning: Failed to abort git rebase. You may need to run "
                "'git rebase --abort' manually.",
                err=True,
            )

    # Restore original refs
    restored = []
    for branch, sha_hex in state.original_refs.items():
        # dulwich expects 40 ASCII hex bytes, not raw SHA bytes
        sha = sha_hex.encode()
        git.update_branch(repo, branch, sha)
        restored.append(branch)

    # Clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, state.original_branch)

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

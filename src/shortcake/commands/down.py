from dataclasses import dataclass

import typer

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo


class DownError(ShortcakeError):
    """Error during down navigation."""

    pass


class NotTrackedError(DownError):
    """Raised when branch is not tracked (no parent trailer)."""

    pass


class DetachedHeadError(DownError):
    """Raised when in detached HEAD state."""

    pass


@dataclass
class DownResult:
    from_branch: str
    to_branch: str
    at_bottom: bool  # True if we landed on trunk


def _down(repo: Repo) -> DownResult:
    """
    Move to parent branch.

    Returns:
        DownResult with from/to branch names and whether at bottom

    Raises:
        DetachedHeadError: If in detached HEAD state
        NotTrackedError: If branch has no parent trailer
    """
    current = git.get_current_branch(repo)
    if current is None:
        raise DetachedHeadError("Not on a branch (detached HEAD)")

    all_branches = set(git.get_all_local_branches(repo))
    parent = git.get_branch_parent(repo, current, all_branches)

    if parent is None:
        raise NotTrackedError(f"Branch '{current}' is not tracked")

    # Check if parent is trunk (default branch)
    default_branch = git.get_default_branch(repo)
    at_bottom = parent == default_branch

    git.switch_branch(repo, parent)
    return DownResult(from_branch=current, to_branch=parent, at_bottom=at_bottom)


def down() -> None:
    """Move to parent branch (down the stack)."""
    repo = git.open_repo()

    try:
        result = _down(repo)
    except DetachedHeadError:
        typer.echo("Error: Not on a branch (detached HEAD)", err=True)
        raise typer.Exit(1) from None
    except NotTrackedError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.at_bottom:
        typer.echo(f"Switched to '{result.to_branch}' (bottom of stack)")
    else:
        typer.echo(f"Switched to '{result.to_branch}'")

from dataclasses import dataclass

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError


class BottomError(ShortcakeError):
    """Error during bottom navigation."""

    pass


class NotTrackedError(BottomError):
    """Raised when branch is not tracked (no parent trailer)."""

    pass


class DetachedHeadError(BottomError):
    """Raised when in detached HEAD state."""

    pass


@dataclass
class BottomResult:
    from_branch: str
    to_branch: str
    already_at_bottom: bool


def _bottom(repo: Repo) -> BottomResult:
    """
    Jump to bottom of stack (first branch above trunk).

    Walks down the parent chain until we find a branch whose parent is trunk.

    Returns:
        BottomResult with from/to branch names and whether already at bottom

    Raises:
        DetachedHeadError: If in detached HEAD state
        NotTrackedError: If branch has no parent trailer
    """
    current = git.get_current_branch(repo)
    if current is None:
        raise DetachedHeadError("Not on a branch (detached HEAD)")

    default_branch = git.get_default_branch(repo)
    all_branches = set(git.get_all_local_branches(repo))

    # First check if current branch is even tracked
    parent = git.get_branch_parent(repo, current, all_branches)
    if parent is None:
        raise NotTrackedError(f"Branch '{current}' is not tracked")

    # If parent is already trunk, we're at bottom
    if parent == default_branch:
        return BottomResult(
            from_branch=current, to_branch=current, already_at_bottom=True
        )

    # Walk down until we find the branch whose parent is trunk
    start = current
    while True:
        parent = git.get_branch_parent(repo, current, all_branches)
        # parent is guaranteed to be not None here since we're walking
        # a tracked parent chain (first branch was already verified)
        assert parent is not None

        if parent == default_branch:
            # Current branch's parent is trunk, so current is bottom
            break

        current = parent

    git.checkout_branch(repo, current)
    return BottomResult(from_branch=start, to_branch=current, already_at_bottom=False)


def bottom() -> None:
    """Jump to bottom of stack (first branch above trunk)."""
    repo = git.open_repo()

    try:
        result = _bottom(repo)
    except DetachedHeadError:
        typer.echo("Error: Not on a branch (detached HEAD)", err=True)
        raise typer.Exit(1) from None
    except NotTrackedError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.already_at_bottom:
        typer.echo("Already at bottom of stack")
    else:
        typer.echo(f"Switched to '{result.to_branch}'")

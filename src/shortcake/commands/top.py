from dataclasses import dataclass

import typer

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo


class TopError(ShortcakeError):
    """Error during top navigation."""

    pass


class DetachedHeadError(TopError):
    """Raised when in detached HEAD state."""

    pass


class MultipleChildrenError(TopError):
    """Raised when multiple children exist at some level."""

    def __init__(self, branch: str, children: list[str]) -> None:
        self.branch = branch
        self.children = children
        super().__init__(f"Multiple children at '{branch}': {', '.join(children)}")


@dataclass
class TopResult:
    from_branch: str
    to_branch: str
    already_at_top: bool


def _top(repo: Repo) -> TopResult:
    """
    Jump to top of stack (leaf branch).

    Walks up the tree until no more children exist.

    Returns:
        TopResult with from/to branch names and whether already at top

    Raises:
        DetachedHeadError: If in detached HEAD state
        MultipleChildrenError: If multiple children at any level
    """
    current = git.get_current_branch(repo)
    if current is None:
        raise DetachedHeadError("Not on a branch (detached HEAD)")

    start = current
    while True:
        children = git.get_branch_children(repo, current)

        if not children:
            # No children, we're at the top
            break

        if len(children) > 1:
            raise MultipleChildrenError(current, children)

        current = children[0]

    already_at_top = current == start
    if not already_at_top:
        git.switch_branch(repo, current)

    return TopResult(
        from_branch=start, to_branch=current, already_at_top=already_at_top
    )


def top() -> None:
    """Jump to top of stack (leaf branch)."""
    repo = git.open_repo()

    try:
        result = _top(repo)
    except DetachedHeadError:
        typer.echo("Error: Not on a branch (detached HEAD)", err=True)
        raise typer.Exit(1) from None
    except MultipleChildrenError as e:
        # Prompt user to pick a child
        typer.echo(f"Multiple children at '{e.branch}': {', '.join(e.children)}")
        selected = typer.prompt("Choose child branch")
        if selected not in e.children:
            typer.echo(f"Error: '{selected}' is not a valid child", err=True)
            raise typer.Exit(1) from None
        # Checkout selected and try again from there
        git.switch_branch(repo, selected)
        typer.echo(f"Switched to '{selected}'")
        # Continue walking up from selected branch
        try:
            result = _top(repo)
            if not result.already_at_top:
                typer.echo(f"Switched to '{result.to_branch}'")
            return
        except MultipleChildrenError:
            # If we hit another fork, user needs to run top again
            typer.echo("Run 'sc top' again to continue navigating")
            return

    if result.already_at_top:
        typer.echo("Already at top of stack")
    else:
        typer.echo(f"Switched to '{result.to_branch}'")

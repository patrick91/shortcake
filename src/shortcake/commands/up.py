from dataclasses import dataclass
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo


class UpError(ShortcakeError):
    """Error during up navigation."""

    pass


class AlreadyAtTopError(UpError):
    """Raised when already at top of stack (no children)."""

    pass


class DetachedHeadError(UpError):
    """Raised when in detached HEAD state."""

    pass


class MultipleChildrenError(UpError):
    """Raised when multiple children exist and no selection made."""

    def __init__(self, children: list[str]) -> None:
        self.children = children
        super().__init__(f"Multiple children: {', '.join(children)}")


@dataclass
class UpResult:
    from_branch: str
    to_branch: str


def _up(repo: Repo, child: str | None = None) -> UpResult:
    """
    Move to child branch.

    Args:
        repo: The git repository
        child: Specific child to move to (for multiple children case)

    Returns:
        UpResult with from/to branch names

    Raises:
        DetachedHeadError: If in detached HEAD state
        AlreadyAtTopError: If no children exist
        MultipleChildrenError: If multiple children and no child specified
    """
    current = git.get_current_branch(repo)
    if current is None:
        raise DetachedHeadError("Not on a branch (detached HEAD)")

    children = git.get_branch_children(repo, current)

    if not children:
        raise AlreadyAtTopError("Already at top of stack")

    if child is not None:
        if child not in children:
            raise UpError(f"'{child}' is not a child of '{current}'")
        target = child
    elif len(children) == 1:
        target = children[0]
    else:
        raise MultipleChildrenError(children)

    git.switch_branch(repo, target)
    return UpResult(from_branch=current, to_branch=target)


def up(
    child: Annotated[str | None, typer.Argument()] = None,
) -> None:
    """Move to child branch (up the stack)."""
    repo = git.open_repo()

    try:
        result = _up(repo, child)
    except AlreadyAtTopError:
        typer.echo("Already at top of stack (no children)")
        return
    except DetachedHeadError:
        typer.echo("Error: Not on a branch (detached HEAD)", err=True)
        raise typer.Exit(1) from None
    except MultipleChildrenError as e:
        # Prompt user to pick a child
        typer.echo(f"Multiple children: {', '.join(e.children)}")
        selected = typer.prompt("Choose child branch")
        if selected not in e.children:
            typer.echo(f"Error: '{selected}' is not a valid child", err=True)
            raise typer.Exit(1) from None
        git.switch_branch(repo, selected)
        typer.echo(f"Switched to '{selected}'")
        return
    except UpError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Switched to '{result.to_branch}'")

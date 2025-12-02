"""Navigation commands for moving through the stack."""

from typing import Annotated

import typer
from rich_toolkit.menu import Menu, Option
from rich_toolkit.styles import TaggedStyle

from shortcake.git import GitError, GitRepo
from shortcake.metadata import (
    get_all_branch_metadata,
    get_branch_metadata,
    get_children,
)
from shortcake.output import print_error

app = typer.Typer()


def _safe_checkout(git: GitRepo, branch: str) -> None:
    """Checkout a branch with user-friendly error handling."""
    try:
        git.checkout_branch(branch)
    except GitError as e:
        error_str = str(e)
        if "local changes" in error_str and "would be overwritten" in error_str:
            print_error("You have uncommitted changes that would be overwritten.")
            print_error("Please commit or stash your changes before switching branches.")
        else:
            print_error(f"Failed to checkout '{branch}': {e}")
        raise typer.Exit(1) from None


def _get_parent(branch: str) -> str | None:
    """Get the parent branch of the given branch."""
    metadata = get_branch_metadata(branch)
    return metadata.get("parent")


@app.command()
def up():
    """Move up the stack to a child branch (toward tip, away from main)."""
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    current = git.get_current_branch()
    children = get_children(current)

    if not children:
        typer.echo(f"Already at top of stack (no children for '{current}')")
        return

    if len(children) == 1:
        _safe_checkout(git, children[0])
        typer.echo(f"Switched to {children[0]}")
    else:
        # Multiple children - show options
        typer.echo("Multiple child branches:")
        for i, child in enumerate(children, 1):
            typer.echo(f"  {i}. {child}")
        typer.echo()
        typer.echo("Use 'git checkout <branch>' to switch to one")


@app.command()
def down():
    """Move down the stack to the parent branch (toward main)."""
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    current = git.get_current_branch()

    if git.is_trunk_branch(current):
        typer.echo("Already at trunk (main/master)")
        return

    parent = _get_parent(current)
    if not parent:
        print_error(f"Branch '{current}' has no parent (not managed by shortcake)")
        raise typer.Exit(1)

    _safe_checkout(git, parent)
    typer.echo(f"Switched to {parent}")


@app.command()
def top():
    """Move to the top of the stack (furthest from main)."""
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    current = git.get_current_branch()

    # Walk down to find the leaf (top of stack)
    branch = current
    while True:
        children = get_children(branch)
        if not children:
            break
        if len(children) > 1:
            typer.echo(f"Multiple branches at '{branch}', stopping here")
            break
        branch = children[0]

    if branch == current:
        typer.echo("Already at top of stack")
        return

    _safe_checkout(git, branch)
    typer.echo(f"Switched to {branch}")


@app.command()
def bottom():
    """Move to the bottom of the stack (closest to main)."""
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    current = git.get_current_branch()

    if git.is_trunk_branch(current):
        typer.echo("Already at trunk (main/master)")
        return

    # Walk up to find the root (bottom of stack)
    branch = current
    while True:
        parent = _get_parent(branch)
        if not parent or git.is_trunk_branch(parent):
            break
        branch = parent

    if branch == current:
        typer.echo("Already at bottom of stack")
        return

    _safe_checkout(git, branch)
    typer.echo(f"Switched to {branch}")


def _find_branch_by_pr_number(pr_number: int) -> str | None:
    """Find a branch by its PR number."""
    all_metadata = get_all_branch_metadata()
    for branch_name, metadata in all_metadata.items():
        if metadata.get("pr_number") == pr_number:
            return branch_name
    return None


def _pick_branch_interactive(git: GitRepo) -> str | None:
    """Show an interactive menu to pick a branch."""
    all_metadata = get_all_branch_metadata()
    current_branch = git.get_current_branch()

    if not all_metadata:
        return None

    options = []
    for branch_name, metadata in sorted(all_metadata.items()):
        pr_number = metadata.get("pr_number")
        pr_suffix = f" #{pr_number}" if pr_number else ""
        current_suffix = " (current)" if branch_name == current_branch else ""
        options.append(
            Option({"value": branch_name, "name": f"{branch_name}{pr_suffix}{current_suffix}"})
        )

    result = Menu(
        label="Select a branch:",
        options=options,
        allow_filtering=True,
        max_visible=15,
        style=TaggedStyle(),
    ).ask()

    return result


@app.command()
def checkout(
    target: Annotated[
        str | None,
        typer.Argument(help="Branch name or PR number (e.g., 'feature-1' or '123')"),
    ] = None,
):
    """Switch to a branch by name or PR number.

    Examples:
        shortcake checkout              # Interactive branch selection
        shortcake checkout feature-1    # Switch by branch name
        shortcake checkout 123          # Switch by PR number
    """
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    # Interactive mode if no target provided
    if target is None:
        selected = _pick_branch_interactive(git)
        if selected is None:
            typer.echo("No branch selected")
            return
        _safe_checkout(git, selected)
        typer.echo(f"Switched to {selected}")
        return

    # Check if target is a PR number (all digits)
    if target.isdigit():
        pr_number = int(target)
        branch = _find_branch_by_pr_number(pr_number)
        if not branch:
            print_error(f"No branch found for PR #{pr_number}")
            raise typer.Exit(1)
        _safe_checkout(git, branch)
        typer.echo(f"Switched to {branch} (PR #{pr_number})")
    else:
        # Target is a branch name
        if not git.branch_exists(target):
            print_error(f"Branch '{target}' does not exist")
            raise typer.Exit(1)
        _safe_checkout(git, target)
        typer.echo(f"Switched to {target}")

"""Navigation commands for moving through the stack."""

import json

import typer

from shortcake.git import GitError, GitRepo

app = typer.Typer()


def _get_branch_metadata(git: GitRepo, branch: str) -> dict:
    """Get shortcake metadata for a branch from git notes."""
    notes = git.get_notes(branch, "shortcake")
    if notes:
        try:
            return json.loads(notes)
        except json.JSONDecodeError:
            return {}
    return {}


def _get_parent(git: GitRepo, branch: str) -> str | None:
    """Get the parent branch of the given branch."""
    metadata = _get_branch_metadata(git, branch)
    return metadata.get("parent")


def _get_children(git: GitRepo, branch: str) -> list[str]:
    """Get all branches that have the given branch as their parent."""
    children = []
    for branch_name in git.get_branches():
        metadata = _get_branch_metadata(git, branch_name)
        if metadata.get("parent") == branch:
            children.append(branch_name)
    return children


def _is_trunk(branch: str) -> bool:
    """Check if branch is a trunk branch (main/master)."""
    return branch in ("main", "master")


@app.command()
def up():
    """Move to the parent branch (toward main)."""
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    current = git.get_current_branch()

    if _is_trunk(current):
        typer.echo("Already at trunk (main/master)")
        return

    parent = _get_parent(git, current)
    if not parent:
        typer.echo(f"Branch '{current}' has no parent (not managed by shortcake)")
        raise typer.Exit(1)

    git.checkout_branch(parent)
    typer.echo(f"Switched to {parent}")


@app.command()
def down():
    """Move to a child branch (away from main)."""
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    current = git.get_current_branch()
    children = _get_children(git, current)

    if not children:
        typer.echo(f"No child branches found for '{current}'")
        return

    if len(children) == 1:
        git.checkout_branch(children[0])
        typer.echo(f"Switched to {children[0]}")
    else:
        # Multiple children - show options
        typer.echo("Multiple child branches:")
        for i, child in enumerate(children, 1):
            typer.echo(f"  {i}. {child}")
        typer.echo()
        typer.echo("Use 'git checkout <branch>' to switch to one")


@app.command()
def top():
    """Move to the top of the stack (furthest from main)."""
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    current = git.get_current_branch()

    # Walk down to find the leaf (top of stack)
    branch = current
    while True:
        children = _get_children(git, branch)
        if not children:
            break
        if len(children) > 1:
            typer.echo(f"Multiple branches at '{branch}', stopping here")
            break
        branch = children[0]

    if branch == current:
        typer.echo("Already at top of stack")
        return

    git.checkout_branch(branch)
    typer.echo(f"Switched to {branch}")


@app.command()
def bottom():
    """Move to the bottom of the stack (closest to main)."""
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    current = git.get_current_branch()

    if _is_trunk(current):
        typer.echo("Already at trunk (main/master)")
        return

    # Walk up to find the root (bottom of stack)
    branch = current
    while True:
        parent = _get_parent(git, branch)
        if not parent or _is_trunk(parent):
            break
        branch = parent

    if branch == current:
        typer.echo("Already at bottom of stack")
        return

    git.checkout_branch(branch)
    typer.echo(f"Switched to {branch}")

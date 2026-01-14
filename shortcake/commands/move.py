"""Move command for changing a branch's parent."""

import typer
from rich.console import Console
from rich_toolkit.menu import Menu, Option

from shortcake import get_cli_name
from shortcake.git import GitError, GitRepo
from shortcake.metadata import get_branch_metadata, update_branch_metadata
from shortcake.output import print_warning
from shortcake.trailers import SHORTCAKE_PARENT_TRAILER

app = typer.Typer()
console = Console(stderr=True)


def _pick_new_parent(git: GitRepo, branch_to_move: str, current_parent: str | None) -> str | None:
    """Show interactive menu to pick new parent branch."""
    all_branches = git.get_branches()

    # Build options: all branches except the one being moved
    options = []
    for b in sorted(all_branches):
        if b == branch_to_move:
            continue
        # Mark current parent
        suffix = " (current parent)" if b == current_parent else ""
        options.append(Option({"value": b, "name": f"{b}{suffix}"}))

    if not options:
        return None

    result = Menu(
        label=f"Select new parent for '{branch_to_move}'",
        options=options,
        allow_filtering=True,
    ).ask()

    return result


@app.command()
def move(
    onto: str | None = typer.Option(None, "--onto", "-o", help="New parent branch"),
    branch: str | None = typer.Argument(None, help="Branch to move (defaults to current)"),
    no_rebase: bool = typer.Option(False, "--no-rebase", help="Only update metadata, don't rebase"),
):
    """Move a branch to a new parent.

    Changes the parent of a branch and rebases it onto the new parent.
    Use --no-rebase to only update the metadata without rebasing.

    Examples:
        sc move                          # Interactive: select new parent
        sc move --onto main              # Move current branch to main
        sc move feature-2 --onto main    # Move feature-2 to main
        sc move --onto feature-1 --no-rebase  # Only update parent, don't rebase
    """
    try:
        git = GitRepo()
    except GitError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1) from None

    cli = get_cli_name()

    try:
        branch_to_move = branch or git.get_current_branch()

        # Validate branch exists
        if not git.branch_exists(branch_to_move):
            console.print(f"[bold red]Error:[/] Branch '{branch_to_move}' does not exist")
            raise typer.Exit(1)

        # Can't move main/master
        if git.is_trunk_branch(branch_to_move):
            console.print(f"[bold red]Error:[/] Cannot move '{branch_to_move}' branch")
            raise typer.Exit(1)

        # Check if branch is tracked
        metadata = get_branch_metadata(branch_to_move)
        old_parent = metadata.get("parent")

        if not old_parent:
            console.print(
                f"[bold red]Error:[/] Branch '{branch_to_move}' is not managed by shortcake. "
                f"Use '{cli} adopt' first."
            )
            raise typer.Exit(1)

        # Interactive mode if --onto not provided
        if onto is None:
            onto = _pick_new_parent(git, branch_to_move, old_parent)
            if onto is None:
                typer.echo("Cancelled")
                raise typer.Exit(0)

        # Validate new parent exists
        if not git.branch_exists(onto):
            console.print(f"[bold red]Error:[/] Parent branch '{onto}' does not exist")
            raise typer.Exit(1)

        # Can't move onto itself
        if branch_to_move == onto:
            console.print("[bold red]Error:[/] Cannot move branch onto itself")
            raise typer.Exit(1)

        # Check if already has this parent
        if old_parent == onto:
            typer.echo(f"Branch '{branch_to_move}' already has parent '{onto}'")
            return

        if no_rebase:
            # Just update metadata
            update_branch_metadata(
                branch_to_move,
                parent=onto,
                parent_revision=git.get_commit_sha(onto),
            )
            typer.echo(f"Moved '{branch_to_move}' from '{old_parent}' to '{onto}' (metadata only)")
            if branch_to_move == git.get_current_branch():
                try:
                    git.update_commit_trailers(
                        {SHORTCAKE_PARENT_TRAILER: onto},
                        no_verify=True,
                    )
                except GitError as e:
                    print_warning(f"Failed to add trailers to commit: {e}")
            else:
                print_warning(
                    f"Trailers not updated for '{branch_to_move}' "
                    "(branch is not checked out)."
                )
        else:
            # Get the old parent revision for rebasing
            old_parent_rev = metadata.get("parent_revision")
            if not old_parent_rev:
                # Fallback to merge-base
                old_parent_rev = git.get_merge_base(branch_to_move, old_parent)

            # Checkout the branch if not already on it
            current = git.get_current_branch()
            if current != branch_to_move:
                git.checkout_branch(branch_to_move)

            # Rebase onto new parent
            typer.echo(f"Rebasing '{branch_to_move}' onto '{onto}'...")
            try:
                git.rebase_onto(onto, old_parent_rev, branch_to_move)
            except GitError as e:
                if "CONFLICT" in str(e) or "could not apply" in str(e):
                    console.print()
                    console.print("[bold red]Rebase conflict.[/] Resolve conflicts and run:")
                    typer.echo("  git rebase --continue")
                    typer.echo("")
                    typer.echo("Or abort with:")
                    typer.echo("  git rebase --abort")
                    # Still update metadata so after resolving, parent is correct
                    update_branch_metadata(
                        branch_to_move,
                        parent=onto,
                        parent_revision=git.get_commit_sha(onto),
                    )
                    raise typer.Exit(1) from None
                raise

            # Update metadata
            update_branch_metadata(
                branch_to_move,
                parent=onto,
                parent_revision=git.get_commit_sha(onto),
            )
            try:
                git.update_commit_trailers(
                    {SHORTCAKE_PARENT_TRAILER: onto},
                    no_verify=True,
                )
            except GitError as e:
                print_warning(f"Failed to add trailers to commit: {e}")
            typer.echo(f"Moved '{branch_to_move}' from '{old_parent}' to '{onto}'")

    except GitError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1) from None

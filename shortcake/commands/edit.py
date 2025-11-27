import typer
from rich.console import Console

from shortcake.git import GitError, GitRepo

app = typer.Typer()
console = Console(stderr=True)


def _do_edit(no_verify: bool = False) -> None:
    """Internal implementation for edit/modify commands."""
    try:
        git = GitRepo()
    except GitError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1) from None

    try:
        # Check if there are staged changes
        if not git.has_staged_changes():
            console.print("[bold red]Error:[/] No staged changes to amend. Use 'git add' first.")
            raise typer.Exit(1)

        # Amend the commit without opening editor (reuse previous message)
        # Note: Metadata is stored by branch name in JSON, so no need to save/restore
        git.commit(amend=True, no_verify=no_verify)

        typer.echo("Successfully amended the commit")

    except GitError as e:
        error_msg = str(e)
        console.print()  # Add blank line after any hook output
        if "returned non-zero exit status 1" in error_msg:
            # Pre-commit hook failed - the hook output was already shown
            console.print("[bold red]Amend failed.[/] Pre-commit hooks modified files or failed.")
            console.print("Review the changes, stage them, and try again.")
        else:
            console.print(f"[bold red]Error:[/] Failed to amend commit - {error_msg}")
        raise typer.Exit(1) from None


@app.command()
def edit(
    no_verify: bool = typer.Option(
        False, "--no-verify", "-n", help="Skip pre-commit and commit-msg hooks"
    ),
):
    """Edit the current stack by amending the commit.

    Stage your changes first with 'git add', then run this command.
    Amends the previous commit without opening an editor.
    """
    _do_edit(no_verify=no_verify)


@app.command(name="modify")
def modify(
    no_verify: bool = typer.Option(
        False, "--no-verify", "-n", help="Skip pre-commit and commit-msg hooks"
    ),
):
    """Alias for edit - Edit the current stack by amending the commit.

    Stage your changes first with 'git add', then run this command.
    Amends the previous commit without opening an editor.
    """
    _do_edit(no_verify=no_verify)

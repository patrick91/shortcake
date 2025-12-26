import typer
from rich.console import Console

from shortcake.git import GitError, GitRepo

app = typer.Typer()
console = Console(stderr=True)


def _do_edit(no_verify: bool = False, message: str | None = None, reword: bool = False) -> None:
    """Internal implementation for edit/modify commands."""
    try:
        git = GitRepo()
    except GitError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1) from None

    try:
        # Reword mode: edit just the commit message
        if reword:
            git.commit(amend=True, no_verify=no_verify, edit_message=True)
            new_message = git.get_last_commit_message()
            typer.echo(f"Updated commit message: {new_message}")
            return

        # Check if there are staged changes
        if not git.has_staged_changes():
            action = "commit" if message else "amend"
            console.print(
                f"[bold red]Error:[/] No staged changes to {action}. Use 'git add' first."
            )
            console.print("Hint: Use --reword to edit just the commit message.")
            raise typer.Exit(1)

        if message:
            # Create a new commit with the given message
            git.commit(message=message, no_verify=no_verify)
            typer.echo(f"Created commit: {message}")
        else:
            # Amend the commit without opening editor (reuse previous message)
            # Note: Metadata is stored by branch name in JSON, so no need to save/restore
            git.commit(amend=True, no_verify=no_verify)
            typer.echo("Successfully amended the commit")

    except GitError as e:
        error_msg = str(e)
        console.print()  # Add blank line after hook output
        # Pre-commit hook failed - the hook output was already shown above
        if "non-zero exit status" in error_msg:
            action = "Commit" if message else "Amend"
            console.print(
                f"[bold red]{action} failed.[/] Pre-commit hooks modified files or failed."
            )
            console.print("Review the changes, stage them, and try again.")
        else:
            console.print(f"[bold red]Error:[/] {error_msg}")
        raise typer.Exit(1) from None


@app.command()
def edit(
    no_verify: bool = typer.Option(
        False, "--no-verify", "-n", help="Skip pre-commit and commit-msg hooks"
    ),
    message: str | None = typer.Option(
        None, "--message", "-m", help="Create a new commit with this message instead of amending"
    ),
    reword: bool = typer.Option(
        False, "--reword", "-r", help="Edit only the commit message (no staged changes required)"
    ),
):
    """Edit the current stack by amending or adding a commit.

    Stage your changes first with 'git add', then run this command.
    Without --message: amends the previous commit.
    With --message: creates a new commit with the given message.
    With --reword: edit just the commit message (opens editor).
    """
    _do_edit(no_verify=no_verify, message=message, reword=reword)


@app.command(name="modify")
def modify(
    no_verify: bool = typer.Option(
        False, "--no-verify", "-n", help="Skip pre-commit and commit-msg hooks"
    ),
    message: str | None = typer.Option(
        None, "--message", "-m", help="Create a new commit with this message instead of amending"
    ),
    reword: bool = typer.Option(
        False, "--reword", "-r", help="Edit only the commit message (no staged changes required)"
    ),
):
    """Alias for edit - Edit the current stack by amending or adding a commit.

    Stage your changes first with 'git add', then run this command.
    Without --message: amends the previous commit.
    With --message: creates a new commit with the given message.
    With --reword: edit just the commit message (opens editor).
    """
    _do_edit(no_verify=no_verify, message=message, reword=reword)

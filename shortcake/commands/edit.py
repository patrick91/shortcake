import typer

from shortcake.git import GitError, GitRepo

app = typer.Typer()


def _do_edit(no_verify: bool = False) -> None:
    """Internal implementation for edit/modify commands."""
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    try:
        # Check if there are staged changes
        if not git.has_staged_changes():
            typer.echo("Error: No staged changes to amend. Use 'git add' first.", err=True)
            raise typer.Exit(1)

        # Save existing shortcake notes before amending (amend changes commit SHA)
        existing_notes = git.get_notes("HEAD", "shortcake")

        # Amend the commit without opening editor (reuse previous message)
        git.commit(amend=True, no_verify=no_verify)

        # Re-attach shortcake notes to the new commit SHA
        if existing_notes:
            git.add_notes(existing_notes, "HEAD", "shortcake")

        typer.echo("Successfully amended the commit")

    except GitError as e:
        error_msg = str(e)
        if "returned non-zero exit status 1" in error_msg:
            # Pre-commit hook failed - the hook output was already shown
            typer.echo("")  # Add blank line after hook output
            typer.echo("Amend failed. Pre-commit hooks modified files or failed.", err=True)
            typer.echo("Review the changes, stage them, and try again.", err=True)
        else:
            typer.echo(f"Error: Failed to amend commit - {error_msg}", err=True)
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

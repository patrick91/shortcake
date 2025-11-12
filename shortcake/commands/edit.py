import typer

from shortcake.git import GitError, GitRepo

app = typer.Typer()


@app.command()
def edit():
    """Edit the current stack by amending the commit.

    Stage your changes first with 'git add', then run this command.
    Amends the previous commit without opening an editor.
    """
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

        # Amend the commit without opening editor (reuse previous message)
        git.commit(amend=True)
        typer.echo("Successfully amended the commit")

    except GitError as e:
        typer.echo(f"Error: Failed to amend commit - {e}", err=True)
        raise typer.Exit(1) from None


@app.command(name="modify")
def modify():
    """Alias for edit - Edit the current stack by amending the commit.

    Stage your changes first with 'git add', then run this command.
    Amends the previous commit without opening an editor.
    """
    edit()

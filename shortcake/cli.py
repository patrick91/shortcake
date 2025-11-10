"""CLI module for shortcake."""

import re
import subprocess
import typer

app = typer.Typer(help="Shortcake CLI - A CLI built with typer and uv")


@app.command()
def hello(name: str = typer.Option("World", help="Name to greet")):
    """Say hello to someone."""
    typer.echo(f"Hello {name}!")


@app.command()
def version():
    """Show the version."""
    from shortcake import __version__
    typer.echo(f"Shortcake version {__version__}")


def _generate_branch_name(commit_message: str) -> str:
    """Generate a branch name from a commit message.
    
    Converts the commit message to lowercase, replaces spaces with hyphens,
    and removes special characters.
    """
    # Convert to lowercase and replace spaces with hyphens
    branch_name = commit_message.lower().strip()
    # Replace multiple spaces with single hyphen
    branch_name = re.sub(r'\s+', '-', branch_name)
    # Remove special characters except hyphens and alphanumeric
    branch_name = re.sub(r'[^a-z0-9-]', '', branch_name)
    # Remove leading/trailing hyphens
    branch_name = branch_name.strip('-')
    # Limit length to 50 characters
    branch_name = branch_name[:50].rstrip('-')
    
    return branch_name


@app.command()
def create():
    """Create a stack with a new branch and commit."""
    # Prompt for commit message
    commit_message = typer.prompt("Enter commit message")
    
    if not commit_message.strip():
        typer.echo("Error: Commit message cannot be empty", err=True)
        raise typer.Exit(1)
    
    # Generate branch name from commit message
    branch_name = _generate_branch_name(commit_message)
    
    if not branch_name:
        typer.echo("Error: Could not generate a valid branch name from the commit message", err=True)
        raise typer.Exit(1)
    
    try:
        # Create and checkout new branch
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            capture_output=True,
            text=True,
            check=True
        )
        typer.echo(f"Created and switched to branch: {branch_name}")
        
        # Stage all changes
        subprocess.run(
            ["git", "add", "."],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Create commit
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True,
            text=True,
            check=True
        )
        typer.echo(f"Created commit: {commit_message}")
        
    except subprocess.CalledProcessError as e:
        typer.echo(f"Error: {e.stderr.strip()}", err=True)
        raise typer.Exit(1)


@app.command()
def edit():
    """Edit the current stack by amending the commit."""
    try:
        # Stage all changes first
        subprocess.run(
            ["git", "add", "."],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Amend the commit without opening editor (reuse previous message)
        subprocess.run(
            ["git", "commit", "--amend", "--no-edit"],
            capture_output=True,
            text=True,
            check=True
        )
        typer.echo("Successfully amended the commit")
        
    except subprocess.CalledProcessError as e:
        typer.echo("Error: Failed to amend commit", err=True)
        raise typer.Exit(1)


# Create alias for edit command
@app.command(name="modify")
def modify():
    """Alias for edit - Edit the current stack by amending the commit."""
    edit()


if __name__ == "__main__":
    app()

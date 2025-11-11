"""CLI module for shortcake."""

import re
import subprocess
import typer

from shortcake import config

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


def _generate_branch_name(commit_message: str, keep_emoji: bool = False) -> str:
    """Generate a branch name from a commit message.
    
    Converts the commit message to lowercase, replaces spaces with hyphens,
    and removes special characters. Optionally keeps emojis.
    
    Args:
        commit_message: The commit message to convert
        keep_emoji: If True, keeps emojis in the branch name. Default is False.
    
    Note: Future enhancement - support for gitmoji conventions
    """
    # Convert to lowercase and replace spaces with hyphens
    branch_name = commit_message.lower().strip()
    # Replace multiple spaces with single hyphen
    branch_name = re.sub(r'\s+', '-', branch_name)
    
    if keep_emoji:
        # Keep emojis, alphanumeric, and hyphens
        # Unicode ranges for emojis and alphanumeric characters
        branch_name = re.sub(r'[^\w\-\U0001F300-\U0001F9FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U00002600-\U000027BF\U0001F1E0-\U0001F1FF]', '', branch_name)
    else:
        # Remove special characters including emojis, keep only hyphens and alphanumeric
        branch_name = re.sub(r'[^a-z0-9-]', '', branch_name)
    
    # Remove leading/trailing hyphens
    branch_name = branch_name.strip('-')
    # Limit length to 50 characters
    branch_name = branch_name[:50].rstrip('-')
    
    return branch_name


@app.command()
def create():
    """Create a stack with a new branch and commit.
    
    Opens your configured editor to compose the commit message.
    Emoji handling in branch names is controlled by the keep_emoji configuration setting
    (use 'shortcake config set keep_emoji true/false').
    
    Note: Future enhancement will include gitmoji integration.
    """
    # Get keep_emoji setting from config
    keep_emoji = config.get_keep_emoji()
    
    try:
        # Stage all changes first
        subprocess.run(
            ["git", "add", "."],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Create commit using editor (this will open $EDITOR)
        subprocess.run(
            ["git", "commit"],
            check=True
        )
        
        # Get the commit message that was just created
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            capture_output=True,
            text=True,
            check=True
        )
        commit_message = result.stdout.strip()
        
        if not commit_message:
            typer.echo("Error: Commit message cannot be empty", err=True)
            raise typer.Exit(1)
        
        # Generate branch name from commit message
        branch_name = _generate_branch_name(commit_message, keep_emoji=keep_emoji)
        
        if not branch_name:
            typer.echo("Error: Could not generate a valid branch name from the commit message", err=True)
            raise typer.Exit(1)
        
        # Create new branch at current commit
        subprocess.run(
            ["git", "branch", branch_name],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Switch to the new branch
        subprocess.run(
            ["git", "checkout", branch_name],
            capture_output=True,
            text=True,
            check=True
        )
        
        typer.echo(f"Created and switched to branch: {branch_name}")
        typer.echo(f"Created commit: {commit_message}")
        
    except subprocess.CalledProcessError as e:
        typer.echo(f"Error: {e.stderr.strip() if e.stderr else 'Command failed'}", err=True)
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


@app.command(name="config")
def config_cmd(
    action: str = typer.Argument(..., help="Action to perform: 'get', 'set', or 'list'"),
    key: str = typer.Argument(None, help="Configuration key (e.g., 'keep_emoji')"),
    value: str = typer.Argument(None, help="Configuration value (for 'set' action)"),
):
    """Manage shortcake configuration.
    
    Examples:
        shortcake config list - List all configuration settings
        shortcake config get keep_emoji - Get a specific setting
        shortcake config set keep_emoji true - Set a configuration value
    """
    if action == "list":
        # List all configuration settings
        cfg = config.load_config()
        if not cfg:
            typer.echo("No configuration settings found.")
            typer.echo(f"Configuration file: {config.get_config_path()}")
        else:
            typer.echo("Current configuration:")
            for k, v in cfg.items():
                typer.echo(f"  {k} = {v}")
            typer.echo(f"\nConfiguration file: {config.get_config_path()}")
    
    elif action == "get":
        if not key:
            typer.echo("Error: Key is required for 'get' action", err=True)
            raise typer.Exit(1)
        
        cfg = config.load_config()
        if key in cfg:
            typer.echo(f"{key} = {cfg[key]}")
        else:
            typer.echo(f"Configuration key '{key}' not found")
            typer.echo(f"Available keys: {', '.join(cfg.keys()) if cfg else 'none'}")
    
    elif action == "set":
        if not key or value is None:
            typer.echo("Error: Both key and value are required for 'set' action", err=True)
            raise typer.Exit(1)
        
        # Handle boolean values
        if key == "keep_emoji":
            if value.lower() in ("true", "1", "yes"):
                config.set_keep_emoji(True)
                typer.echo(f"Set {key} = true")
            elif value.lower() in ("false", "0", "no"):
                config.set_keep_emoji(False)
                typer.echo(f"Set {key} = false")
            else:
                typer.echo(f"Error: Invalid value for {key}. Use 'true' or 'false'", err=True)
                raise typer.Exit(1)
        else:
            # Generic key-value setting
            cfg = config.load_config()
            cfg[key] = value
            config.save_config(cfg)
            typer.echo(f"Set {key} = {value}")
    
    else:
        typer.echo(f"Error: Unknown action '{action}'. Use 'list', 'get', or 'set'", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()

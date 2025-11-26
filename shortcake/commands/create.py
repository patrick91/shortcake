import json
import re
import time

import typer

from shortcake import config
from shortcake.git import GitError, GitRepo

app = typer.Typer()


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
    branch_name = re.sub(r"\s+", "-", branch_name)

    if keep_emoji:
        # Keep emojis, alphanumeric (including unicode), and hyphens
        # Comprehensive Unicode ranges for emojis:
        # - \U0001F300-\U0001F9FF: Miscellaneous Symbols and Pictographs, Emoticons, etc.
        # - \U0001F600-\U0001F64F: Emoticons
        # - \U0001F680-\U0001F6FF: Transport and Map Symbols
        # - \U00002600-\U000027BF: Miscellaneous Symbols
        # - \U00002B00-\U00002BFF: Miscellaneous Symbols and Arrows (includes ⭐)
        # - \U0001F1E0-\U0001F1FF: Regional Indicator Symbols (flags)
        branch_name = re.sub(
            r"[^\w\-\U0001F300-\U0001F9FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E0-\U0001F1FF]",
            "",
            branch_name,
        )
    else:
        # Remove special characters including emojis, keep hyphens and word chars (including unicode)
        branch_name = re.sub(r"[^\w-]", "", branch_name)

    # Collapse consecutive hyphens into a single hyphen
    branch_name = re.sub(r"-+", "-", branch_name)

    # Remove leading/trailing hyphens
    branch_name = branch_name.strip("-")
    # Limit length to 50 characters
    branch_name = branch_name[:50].rstrip("-")

    return branch_name


@app.command()
def create(
    no_verify: bool = typer.Option(
        False, "--no-verify", "-n", help="Skip pre-commit and commit-msg hooks"
    ),
    gitmoji: bool = typer.Option(
        False, "--gitmoji", "--gm", help="Select a gitmoji to prefix the commit message"
    ),
):
    """Create a stack with a new branch and commit.

    Stage your changes first with 'git add', then run this command.
    Opens your configured editor to compose the commit message.
    The branch name is automatically generated from the commit message.

    Emoji handling in branch names is controlled by the keep_emoji configuration setting
    (use 'shortcake config set keep_emoji true/false').

    Use --gitmoji (or --gm) to select an emoji from the gitmoji list before
    entering your commit message.
    """
    # Get keep_emoji setting from config
    keep_emoji = config.get_keep_emoji()

    # Handle gitmoji selection
    selected_emoji = None
    if gitmoji:
        from shortcake.gitmoji import pick_gitmoji

        selected_gitmoji = pick_gitmoji()
        if selected_gitmoji is None:
            typer.echo("Gitmoji selection cancelled", err=True)
            raise typer.Exit(1)
        selected_emoji = selected_gitmoji.emoji

    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Get the original branch to restore on error
    try:
        original_branch = git.get_current_branch()
    except GitError:
        original_branch = None

    temp_branch_name = None

    try:
        # Create a temporary branch name using timestamp
        temp_branch_name = f"temp-shortcake-{int(time.time() * 1000)}"

        # Create and switch to temporary branch (quiet mode)
        git.create_branch(temp_branch_name, checkout=True)

        # Create commit using git's normal flow (opens editor)
        # If gitmoji was selected, pass it as a prefix for the commit message
        try:
            git.commit(no_verify=no_verify, message_prefix=selected_emoji)
        except GitError as e:
            # Clean up temp branch before showing error
            if original_branch:
                try:
                    git.checkout_branch(original_branch)
                    git.delete_branch(temp_branch_name, force=True)
                except GitError:
                    pass

            error_msg = str(e)
            if "returned non-zero exit status 1" in error_msg:
                # Commit was aborted or failed - provide a friendlier message
                typer.echo("Commit aborted or failed. No changes were made.", err=True)
            else:
                typer.echo(f"Error: {error_msg}", err=True)
            raise typer.Exit(1) from None

        # Get the commit message that was just created
        commit_message = git.get_last_commit_message()

        if not commit_message:
            typer.echo("Error: Commit message cannot be empty", err=True)
            raise typer.Exit(1)

        # Generate branch name from commit message
        branch_name = _generate_branch_name(commit_message, keep_emoji=keep_emoji)

        if not branch_name:
            typer.echo(
                "Error: Could not generate a valid branch name from the commit message",
                err=True,
            )
            raise typer.Exit(1)

        # Rename the temporary branch to the final name
        git.rename_branch(temp_branch_name, branch_name)
        temp_branch_name = None  # Mark as renamed so cleanup doesn't try to delete it

        # Add shortcake notes to track this branch
        # The parent is the branch we were on before creating this one
        notes_data = {"parent": original_branch} if original_branch else {}
        notes_json = json.dumps(notes_data)
        git.add_notes(notes_json, "HEAD", "shortcake")

        typer.echo(f"Created and switched to branch: {branch_name}")
        typer.echo(f"Created commit: {commit_message}")

    except GitError as e:
        # Clean up: switch back to original branch and delete temp branch if it was created
        if temp_branch_name and original_branch:
            try:
                git.checkout_branch(original_branch)
                git.delete_branch(temp_branch_name, force=True)
            except GitError:
                pass  # Ignore cleanup errors

        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

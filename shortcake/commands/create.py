import re
import shutil
import subprocess
import time

import typer

from shortcake import config
from shortcake.git import GitError, GitRepo
from shortcake.metadata import update_branch_metadata
from shortcake.output import print_error

app = typer.Typer()


def _is_claude_cli_available() -> bool:
    """Check if Claude CLI is available.

    Checks both PATH and common installation locations,
    since claude may be installed as an alias.
    """
    # First try shutil.which for PATH lookup
    if shutil.which("claude"):
        return True

    # Check common installation location (handles alias case)
    import os

    home = os.path.expanduser("~")
    claude_path = os.path.join(home, ".claude", "local", "claude")
    if os.path.isfile(claude_path) and os.access(claude_path, os.X_OK):
        return True

    return False


def _get_claude_command() -> list[str]:
    """Get the command to run Claude CLI.

    Returns the appropriate command based on installation method.
    """
    if shutil.which("claude"):
        return ["claude"]

    # Check common installation location
    import os

    home = os.path.expanduser("~")
    claude_path = os.path.join(home, ".claude", "local", "claude")
    if os.path.isfile(claude_path):
        return [claude_path]

    return ["claude"]  # Fallback


def _generate_commit_message_with_claude(
    diff: str, use_gitmoji: bool = False
) -> tuple[str | None, str | None]:
    """Generate a commit message using Claude CLI.

    Args:
        diff: The git diff to analyze
        use_gitmoji: If True, include a gitmoji prefix

    Returns:
        Tuple of (message, error): message if successful, error string if failed
    """
    gitmoji_instruction = ""
    if use_gitmoji:
        gitmoji_instruction = """
Use a gitmoji prefix (e.g., ✨ for new feature, 🐛 for bug fix, ♻️ for refactor, 📝 for docs, etc.).
"""

    prompt = f"""Generate a concise commit message for this diff.
{gitmoji_instruction}
Rules:
- First line should be max 72 characters
- Use imperative mood (e.g., "Add feature" not "Added feature")
- Be specific but concise
- Only output the commit message, nothing else

Diff:
{diff}"""

    try:
        claude_cmd = _get_claude_command()
        result = subprocess.run(
            [*claude_cmd, "--print", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout.strip(), None
        # Return stderr, stdout, or include return code for debugging
        error_msg = result.stderr.strip() or result.stdout.strip()
        if not error_msg:
            error_msg = f"Claude CLI exited with code {result.returncode}"
        return None, error_msg
    except subprocess.TimeoutExpired:
        return None, "Claude CLI timed out (60s limit)"
    except FileNotFoundError:
        return None, "Claude CLI not found"


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
    claude: bool = typer.Option(
        False,
        "--claude",
        "-c",
        help="Use Claude to generate the commit message from staged changes",
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

    Use --claude (or -c) to generate a commit message using Claude CLI.
    The generated message will be pre-filled in your editor for review before committing.
    This requires the 'claude' command to be installed and authenticated.
    Can be combined with --gitmoji to include an emoji prefix.
    """
    # Get keep_emoji setting from config
    keep_emoji = config.get_keep_emoji()

    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    # Handle --claude flag: generate commit message from diff
    generated_message = None
    if claude:
        # Check for staged changes
        if not git.has_staged_changes():
            print_error("No staged changes. Use 'git add' to stage files first.")
            raise typer.Exit(1)

        # Check if claude CLI is available
        if not _is_claude_cli_available():
            print_error("Claude CLI not found. Install it from: https://claude.ai/code")
            raise typer.Exit(1)

        typer.echo("Generating commit message with Claude...")
        diff = git.get_staged_diff()
        generated_message, error = _generate_commit_message_with_claude(diff, use_gitmoji=gitmoji)

        if not generated_message:
            print_error(f"Failed to generate commit message with Claude: {error}")
            raise typer.Exit(1)

        typer.echo(f"Generated: {generated_message}")

    # Handle manual gitmoji selection (only if not using --claude)
    selected_emoji = None
    if gitmoji and not claude:
        from shortcake.gitmoji import pick_gitmoji

        selected_gitmoji = pick_gitmoji()
        if selected_gitmoji is None:
            print_error("Gitmoji selection cancelled")
            raise typer.Exit(1)
        selected_emoji = selected_gitmoji.emoji

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

        # Create commit
        # If --claude was used, pre-fill editor with generated message for review
        # Otherwise, open editor (with optional gitmoji prefix)
        try:
            if generated_message:
                git.commit(no_verify=no_verify, message_prefix=generated_message)
            else:
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
                print_error("Commit aborted or failed. No changes were made.")
            else:
                print_error(error_msg)
            raise typer.Exit(1) from None

        # Get the commit message that was just created
        commit_message = git.get_last_commit_message()

        if not commit_message:
            print_error("Commit message cannot be empty")
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

        # Store branch metadata
        # The parent is the branch we were on before creating this one
        # Also store parent_revision so we can detect when restack is needed
        if original_branch:
            # Use origin/main or origin/master for trunk branches to match restack behavior
            parent_ref = (
                f"origin/{original_branch}"
                if git.is_trunk_branch(original_branch) and git.has_remote("origin")
                else original_branch
            )
            update_branch_metadata(
                branch_name,
                parent=original_branch,
                parent_revision=git.get_commit_sha(parent_ref),
            )

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

        print_error(str(e))
        raise typer.Exit(1) from None

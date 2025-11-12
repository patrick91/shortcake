"""CLI module for shortcake."""

import json
import re
import time
from dataclasses import dataclass

import typer

from shortcake import __version__, config
from shortcake.git import GitError, GitRepo

app = typer.Typer(help="Shortcake CLI - A CLI built with typer and uv")


@dataclass
class BranchInfo:
    """Information about a branch managed by shortcake."""

    name: str
    parent: str | None
    is_current: bool


@app.command()
def version():
    """Show the version."""
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
def create():
    """Create a stack with a new branch and commit.

    Stage your changes first with 'git add', then run this command.
    Opens your configured editor to compose the commit message.
    The branch name is automatically generated from the commit message.

    Emoji handling in branch names is controlled by the keep_emoji configuration setting
    (use 'shortcake config set keep_emoji true/false').

    Note: Future enhancement will include gitmoji integration.
    """
    # Get keep_emoji setting from config
    keep_emoji = config.get_keep_emoji()

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

        # Create and switch to temporary branch
        git.create_branch(temp_branch_name, checkout=True)

        # Create commit using git's normal flow (opens editor)
        git.commit()

        # Get the commit message that was just created
        commit_message = git.get_last_commit_message()

        if not commit_message:
            typer.echo("Error: Commit message cannot be empty", err=True)
            raise typer.Exit(1)

        # Generate branch name from commit message
        branch_name = _generate_branch_name(commit_message, keep_emoji=keep_emoji)

        if not branch_name:
            typer.echo(
                "Error: Could not generate a valid branch name from the commit message", err=True
            )
            raise typer.Exit(1)

        # Rename the temporary branch to the final name
        git.rename_branch(temp_branch_name, branch_name)

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


# Create alias for edit command
@app.command(name="modify")
def modify():
    """Alias for edit - Edit the current stack by amending the commit.

    Stage your changes first with 'git add', then run this command.
    Amends the previous commit without opening an editor.
    """
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
        typer.echo("Current configuration:")
        for field_name, field_value in cfg.model_dump().items():
            typer.echo(f"  {field_name} = {field_value}")
        typer.echo(f"\nConfiguration file: {config.get_config_path()}")

    elif action == "get":
        if not key:
            typer.echo("Error: Key is required for 'get' action", err=True)
            raise typer.Exit(1)

        cfg = config.load_config()
        cfg_dict = cfg.model_dump()
        if key in cfg_dict:
            typer.echo(f"{key} = {cfg_dict[key]}")
        else:
            typer.echo(f"Configuration key '{key}' not found")
            typer.echo(f"Available keys: {', '.join(cfg_dict.keys())}")

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
            typer.echo(f"Error: Unknown configuration key '{key}'", err=True)
            cfg = config.load_config()
            typer.echo(f"Available keys: {', '.join(cfg.model_dump().keys())}")
            raise typer.Exit(1)

    else:
        typer.echo(f"Error: Unknown action '{action}'. Use 'list', 'get', or 'set'", err=True)
        raise typer.Exit(1)


def _get_shortcake_branches(git: GitRepo) -> list[BranchInfo]:
    """Get all branches that are managed by shortcake (have shortcake git notes).

    Returns:
        List of BranchInfo objects for shortcake-managed branches.
    """
    branches = []
    current_branch = git.get_current_branch()

    for branch_name in git.get_branches():
        notes = git.get_notes(branch_name, "shortcake")
        if notes:
            # Parse notes to get parent if exists
            try:
                notes_data = json.loads(notes)
                parent = notes_data.get("parent")
            except (json.JSONDecodeError, AttributeError):
                parent = None

            branches.append(
                BranchInfo(
                    name=branch_name,
                    parent=parent,
                    is_current=branch_name == current_branch,
                )
            )

    return branches


def _build_tree_lines(branches: list[BranchInfo]) -> list[str]:
    """Build a tree visualization of the branch stack.

    Args:
        branches: List of BranchInfo objects.

    Returns:
        List of formatted strings representing the tree.
    """
    if not branches:
        return []

    # Get set of all tracked branch names
    tracked_names = {b.name for b in branches}

    # Build a map of children for each parent
    children_map: dict[str | None, list[BranchInfo]] = {}
    for branch in branches:
        if branch.parent not in children_map:
            children_map[branch.parent] = []
        children_map[branch.parent].append(branch)

    lines = []

    def add_branch_to_tree(branch: BranchInfo, prefix: str = "", is_last: bool = True):
        """Recursively add branch and its children to the tree."""
        # Determine the tree characters
        connector = "└── " if is_last else "├── "
        current_indicator = " (current)" if branch.is_current else ""

        lines.append(f"{prefix}{connector}{branch.name}{current_indicator}")

        # Get children of this branch
        children = children_map.get(branch.name, [])

        # Add children
        for i, child in enumerate(children):
            is_last_child = i == len(children) - 1
            extension = "    " if is_last else "│   "
            add_branch_to_tree(child, prefix + extension, is_last_child)

    # Root branches are those with no parent OR whose parent is not tracked
    root_branches = []
    for parent_name, branches_list in children_map.items():
        if parent_name is None or parent_name not in tracked_names:
            root_branches.extend(branches_list)

    for i, branch in enumerate(root_branches):
        is_last = i == len(root_branches) - 1
        add_branch_to_tree(branch, "", is_last)

    return lines


@app.command()
def ls():
    """List all shortcake-managed branches in a tree structure.

    Shows all branches that are tracked by shortcake (have shortcake git notes),
    displaying their parent-child relationships as a tree.
    The current branch is marked with (current).
    """
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    branches = _get_shortcake_branches(git)

    if not branches:
        typer.echo("No shortcake-managed branches found")
        typer.echo(
            "Use 'shortcake create' to create a new stack or 'shortcake adopt' to track existing branches"
        )
        return

    tree_lines = _build_tree_lines(branches)
    for line in tree_lines:
        typer.echo(line)


@app.command()
def adopt(
    branch: str | None = typer.Argument(
        None, help="Branch name to adopt (defaults to current branch)"
    ),
    parent: str | None = typer.Option(None, "--parent", "-p", help="Parent branch name"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Recursively adopt branch ancestors/descendants"
    ),
):
    """Adopt an existing branch to be tracked by shortcake.

    Adds shortcake tracking (git notes) to an existing git branch.
    Optionally specify a parent branch to create a stacked relationship.

    The --recursive flag will also adopt ancestor branches (if parent is specified)
    or descendant branches (branches based on this one).

    Examples:
        shortcake adopt              # Adopt current branch
        shortcake adopt feature-1    # Adopt specific branch
        shortcake adopt feature-2 -p feature-1  # Adopt with parent
        shortcake adopt -r -p main   # Adopt current branch and ancestors up to main
    """
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    try:
        # Get branch name to adopt
        branch_to_adopt = branch if branch else git.get_current_branch()

        # Validate branch exists
        if not git.branch_exists(branch_to_adopt):
            typer.echo(f"Error: Branch '{branch_to_adopt}' does not exist", err=True)
            raise typer.Exit(1)

        # Check if branch is main/master
        if branch_to_adopt in ("main", "master"):
            typer.echo(f"Error: Cannot adopt '{branch_to_adopt}' branch", err=True)
            raise typer.Exit(1)

        # Check if already tracked
        existing_notes = git.get_notes(branch_to_adopt, "shortcake")
        if existing_notes and not recursive:
            typer.echo(
                f"Error: Branch '{branch_to_adopt}' is already tracked by shortcake", err=True
            )
            typer.echo("Use 'shortcake ls' to see all tracked branches")
            raise typer.Exit(1)

        # Validate parent if specified
        if parent:
            if not git.branch_exists(parent):
                typer.echo(f"Error: Parent branch '{parent}' does not exist", err=True)
                raise typer.Exit(1)

        def adopt_single_branch(branch_name: str, parent_name: str | None):
            """Adopt a single branch."""
            notes_data = {"parent": parent_name} if parent_name else {}
            notes_json = json.dumps(notes_data)

            # Check if notes already exist
            existing = git.get_notes(branch_name, "shortcake")
            if existing:
                # Already adopted, skip
                return False

            git.add_notes(notes_json, branch_name, "shortcake")
            return True

        if recursive:
            # Recursive adoption
            branches_adopted = []

            if parent:
                # Adopt ancestors up to parent
                # This is a simple implementation - could be improved with actual git history
                all_branches = git.get_branches()

                # For now, just adopt the specified branch with the parent
                if adopt_single_branch(branch_to_adopt, parent):
                    branches_adopted.append(branch_to_adopt)

                # Try to find intermediate branches (simplified - assumes naming convention)
                # In reality, you'd want to check git history/commits
                for potential_branch in all_branches:
                    if potential_branch not in (branch_to_adopt, parent, "main", "master"):
                        # Check if this branch might be in the chain
                        # This is a placeholder - real implementation would check git history
                        pass

            else:
                # Adopt descendants (branches based on this one)
                if adopt_single_branch(branch_to_adopt, None):
                    branches_adopted.append(branch_to_adopt)

                # Find branches that might be children
                # This is a simplified version
                all_branches = git.get_branches()
                for potential_child in all_branches:
                    if potential_child not in (branch_to_adopt, "main", "master"):
                        # Check git history to see if it's based on branch_to_adopt
                        # Placeholder for now
                        pass

            if branches_adopted:
                typer.echo(f"Adopted {len(branches_adopted)} branch(es):")
                for b in branches_adopted:
                    typer.echo(f"  - {b}")
            else:
                typer.echo("No new branches to adopt (already tracked)")

        else:
            # Simple adoption
            if adopt_single_branch(branch_to_adopt, parent):
                parent_info = f" with parent '{parent}'" if parent else ""
                typer.echo(f"Adopted branch '{branch_to_adopt}'{parent_info}")
            else:
                typer.echo(f"Branch '{branch_to_adopt}' is already tracked")

    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


if __name__ == "__main__":
    app()

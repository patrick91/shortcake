"""Restack command for rebasing stacked branches onto updated parent branches."""

import json
from dataclasses import dataclass

import typer

from shortcake.git import GitError, GitRepo

app = typer.Typer()


@dataclass
class BranchInfo:
    """Information about a branch in the stack."""

    name: str
    parent: str
    notes_data: dict


def _get_branch_metadata(git: GitRepo, branch: str) -> dict:
    """Get shortcake metadata for a branch from git notes."""
    notes = git.get_notes(branch, "shortcake")
    if notes:
        try:
            return json.loads(notes)
        except json.JSONDecodeError:
            return {}
    return {}


def _get_stack_from_current(git: GitRepo, current_branch: str) -> list[BranchInfo]:
    """Get the stack of branches from trunk up to current branch.

    Walks up from current branch to find all ancestors in the stack,
    then returns them in order from bottom (closest to trunk) to top.

    Args:
        git: GitRepo instance
        current_branch: The current branch (top of stack)

    Returns:
        List of BranchInfo from bottom of stack to current branch
    """
    branches = []
    branch = current_branch

    while branch:
        metadata = _get_branch_metadata(git, branch)
        parent = metadata.get("parent")

        if not parent:
            break  # Not a shortcake-managed branch or reached trunk

        branches.append(
            BranchInfo(
                name=branch,
                parent=parent,
                notes_data=metadata,
            )
        )

        # Check if parent is also a shortcake branch
        parent_metadata = _get_branch_metadata(git, parent)
        if not parent_metadata.get("parent"):
            break  # Parent is trunk (main/master)

        branch = parent

    # Reverse so branches are ordered from bottom of stack to top
    branches.reverse()
    return branches


def _get_children(git: GitRepo, branch: str) -> list[str]:
    """Get all branches that have the given branch as their parent."""
    children = []
    for branch_name in git.get_branches():
        metadata = _get_branch_metadata(git, branch_name)
        if metadata.get("parent") == branch:
            children.append(branch_name)
    return children


def _get_descendant_branches(git: GitRepo, branch: str) -> list[BranchInfo]:
    """Get all descendant branches (children, grandchildren, etc.) in topological order.

    Args:
        git: GitRepo instance
        branch: The branch to find descendants of

    Returns:
        List of BranchInfo for all descendants, in topological order (parents before children)
    """
    result = []
    queue = _get_children(git, branch)

    while queue:
        child = queue.pop(0)
        metadata = _get_branch_metadata(git, child)
        if metadata.get("parent"):
            result.append(
                BranchInfo(
                    name=child,
                    parent=metadata["parent"],
                    notes_data=metadata,
                )
            )
            # Add this child's children to the queue
            queue.extend(_get_children(git, child))

    return result


@app.command()
def restack(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be done without making changes"
    ),
    continue_rebase: bool = typer.Option(
        False, "--continue", help="Continue after resolving rebase conflicts"
    ),
    abort: bool = typer.Option(False, "--abort", help="Abort the current rebase operation"),
):
    """Restack branches onto their updated parents.

    This command fetches the latest changes from origin and rebases your stack
    to ensure all branches are based on their parent's latest commit.

    By default, restacks from trunk up to the current branch, plus all
    descendant branches below.

    Examples:
        shortcake restack              # Restack the stack
        shortcake restack --dry-run    # Preview what would happen
        shortcake restack --continue   # Continue after resolving conflicts
        shortcake restack --abort      # Abort a restack in progress
    """
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Handle --abort
    if abort:
        if not git.is_rebase_in_progress():
            typer.echo("Error: No rebase in progress", err=True)
            raise typer.Exit(1)
        try:
            git.rebase_abort()
            typer.echo("Rebase aborted")
            return
        except GitError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None

    # Handle --continue
    if continue_rebase:
        if not git.is_rebase_in_progress():
            typer.echo("Error: No rebase in progress", err=True)
            raise typer.Exit(1)
        try:
            git.rebase_continue()
            typer.echo("Rebase continued successfully")
            return
        except GitError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None

    # Check for rebase in progress
    if git.is_rebase_in_progress():
        typer.echo("Error: A rebase is already in progress", err=True)
        typer.echo("Run 'shortcake restack --continue' after resolving conflicts")
        typer.echo("Or run 'shortcake restack --abort' to abort")
        raise typer.Exit(1)

    current_branch = git.get_current_branch()

    # Check if on main branch
    if current_branch in ("main", "master"):
        typer.echo("Error: Cannot restack from main/master branch", err=True)
        raise typer.Exit(1)

    # Check if current branch is managed by shortcake
    metadata = _get_branch_metadata(git, current_branch)
    if not metadata.get("parent"):
        typer.echo(
            f"Error: Branch '{current_branch}' is not managed by shortcake. "
            "Use 'shortcake adopt' first.",
            err=True,
        )
        raise typer.Exit(1)

    # Always fetch from remote first
    if git.has_remote("origin"):
        if dry_run:
            typer.echo("Would fetch from origin...")
        else:
            typer.echo("Fetching from origin...")
            try:
                git.fetch("origin")
            except GitError as e:
                typer.echo(f"Warning: Failed to fetch: {e}", err=True)

    # Get stack from trunk up to current branch
    stack_up = _get_stack_from_current(git, current_branch)

    # Get descendant branches (children, grandchildren, etc.)
    descendants = _get_descendant_branches(git, current_branch)

    # Combine: stack up to current + descendants
    all_branches = stack_up + descendants

    if not all_branches:
        typer.echo("No branches to restack")
        return

    if dry_run:
        typer.echo("\nWould restack the following branches:")
        for branch in all_branches:
            typer.echo(f"  {branch.name} (parent: {branch.parent})")
        return

    typer.echo(f"\nRestacking {len(all_branches)} branch(es)...")

    # Save notes for all branches before rebasing (SHAs will change)
    saved_notes: dict[str, dict] = {}
    for branch in all_branches:
        saved_notes[branch.name] = branch.notes_data

    # Rebase each branch in order
    for branch in all_branches:
        typer.echo(f"  Rebasing {branch.name} onto {branch.parent}...", nl=False)

        try:
            # Get merge base between branch and its parent
            merge_base = git.get_merge_base(branch.name, branch.parent)

            if merge_base:
                # Rebase: git rebase --onto parent merge_base branch
                git.rebase_onto(branch.parent, merge_base, branch.name)
            else:
                # Fallback: simple rebase
                git.checkout_branch(branch.name)
                git.rebase(branch.parent)

            # Re-attach notes to the new commit SHA
            git.update_notes(json.dumps(saved_notes[branch.name]), branch.name, "shortcake")

            typer.echo(" done")

        except GitError as e:
            typer.echo(" CONFLICT")
            typer.echo(f"\nError: {e}", err=True)
            typer.echo("\nResolve the conflicts, then run:")
            typer.echo("  shortcake restack --continue")
            typer.echo("\nOr abort with:")
            typer.echo("  shortcake restack --abort")
            raise typer.Exit(1) from None

    # Return to the original branch
    try:
        git.checkout_branch(current_branch)
    except GitError:
        pass  # Ignore checkout errors

    typer.echo("\nRestack complete!")

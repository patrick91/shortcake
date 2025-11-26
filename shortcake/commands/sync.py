"""Sync command for rebasing stacked branches after merges."""

import json
from dataclasses import dataclass

import typer

from shortcake.git import GitError, GitRepo

app = typer.Typer()


@dataclass
class BranchInfo:
    """Information about a tracked branch."""

    name: str
    parent: str | None
    commit_sha: str


def _get_tracked_branches(git: GitRepo) -> dict[str, BranchInfo]:
    """Get all shortcake-tracked branches with their metadata.

    Returns:
        Dict mapping branch name to BranchInfo.
    """
    branches: dict[str, BranchInfo] = {}

    for branch_name in git.get_branches():
        notes = git.get_notes(branch_name, "shortcake")
        if notes:
            try:
                notes_data = json.loads(notes)
                parent = notes_data.get("parent")
            except (json.JSONDecodeError, AttributeError):
                parent = None

            branches[branch_name] = BranchInfo(
                name=branch_name,
                parent=parent,
                commit_sha=git.get_commit_sha(branch_name),
            )

    return branches


def _is_branch_merged(git: GitRepo, branch: str, into: str = "main") -> bool:
    """Check if a branch has been merged into another branch.

    A branch is considered merged if the target branch contains all its commits,
    i.e., the branch is an ancestor of the target.

    Args:
        git: GitRepo instance.
        branch: The branch to check.
        into: The branch to check if merged into (default: main).

    Returns:
        True if branch is merged into target.
    """
    if not git.branch_exists(branch):
        return True  # Branch was deleted, consider it merged

    return git.is_ancestor(branch, into)


def _get_main_branch(git: GitRepo) -> str:
    """Get the name of the main branch (main or master).

    Args:
        git: GitRepo instance.

    Returns:
        'main' or 'master' depending on which exists.

    Raises:
        GitError: If neither main nor master exists.
    """
    if git.branch_exists("main"):
        return "main"
    if git.branch_exists("master"):
        return "master"
    raise GitError("Neither 'main' nor 'master' branch exists")


def _topological_sort(branches: dict[str, BranchInfo]) -> list[str]:
    """Sort branches in topological order (parents before children).

    Args:
        branches: Dict of branch name to BranchInfo.

    Returns:
        List of branch names sorted so parents come before children.
    """
    result: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited or name not in branches:
            return
        visited.add(name)

        # Visit parent first
        parent = branches[name].parent
        if parent and parent in branches:
            visit(parent)

        result.append(name)

    for name in branches:
        visit(name)

    return result


def _find_new_parent(
    branch: str,
    old_parent: str,
    branches: dict[str, BranchInfo],
    main_branch: str,
) -> str:
    """Find the new parent for a branch whose parent was merged.

    Walks up the parent chain until we find a non-merged branch or main.

    Args:
        branch: The branch that needs a new parent.
        old_parent: The old (merged) parent.
        branches: All tracked branches.
        main_branch: Name of the main branch.

    Returns:
        The name of the new parent branch.
    """
    # If old parent is main or doesn't exist in tracked branches, return main
    if old_parent == main_branch or old_parent not in branches:
        return main_branch

    # Check if the old parent's parent exists and is not merged
    grandparent = branches[old_parent].parent
    if grandparent and grandparent in branches:
        return grandparent

    return main_branch


@app.command()
def sync(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be done without making changes"
    ),
    continue_rebase: bool = typer.Option(
        False, "--continue", help="Continue after resolving rebase conflicts"
    ),
    abort: bool = typer.Option(False, "--abort", help="Abort the current rebase operation"),
):
    """Sync branches after a parent branch has been merged.

    This command detects when parent branches have been merged into main
    and rebases child branches onto the updated main branch.

    Examples:
        shortcake sync              # Sync all branches
        shortcake sync --dry-run    # Preview what would happen
        shortcake sync --continue   # Continue after resolving conflicts
        shortcake sync --abort      # Abort a sync in progress
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
        typer.echo("Run 'shortcake sync --continue' after resolving conflicts")
        typer.echo("Or run 'shortcake sync --abort' to abort")
        raise typer.Exit(1)

    try:
        main_branch = _get_main_branch(git)
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Fetch from remote if available
    if git.has_remote("origin"):
        if dry_run:
            typer.echo("Would fetch from origin...")
        else:
            typer.echo("Fetching from origin...")
            try:
                git.fetch("origin")
            except GitError as e:
                typer.echo(f"Warning: Failed to fetch: {e}", err=True)

    # Get all tracked branches
    branches = _get_tracked_branches(git)

    if not branches:
        typer.echo("No shortcake-managed branches found")
        return

    # Find merged branches
    merged_branches: list[str] = []
    for name in branches:
        if _is_branch_merged(git, name, main_branch):
            merged_branches.append(name)

    if not merged_branches:
        typer.echo("All branches are up to date - nothing to sync")
        return

    if dry_run:
        typer.echo("Detected merged branches:")
        for name in merged_branches:
            typer.echo(f"  ✓ {name}")
        typer.echo()

    # Find branches that need rebasing (their parent was merged)
    branches_to_rebase: list[tuple[str, str, str]] = []  # (branch, old_parent, new_parent)

    for name, info in branches.items():
        if name in merged_branches:
            continue  # Skip merged branches

        if info.parent and info.parent in merged_branches:
            new_parent = _find_new_parent(name, info.parent, branches, main_branch)
            branches_to_rebase.append((name, info.parent, new_parent))

    if not branches_to_rebase:
        if dry_run:
            typer.echo("No branches need rebasing")
        else:
            typer.echo("No branches need rebasing - cleaning up merged branches...")

        # Clean up merged branches
        for name in merged_branches:
            if dry_run:
                typer.echo(f"Would delete merged branch: {name}")
            else:
                try:
                    git.delete_branch(name)
                    typer.echo(f"Deleted merged branch: {name}")
                except GitError as e:
                    typer.echo(f"Warning: Could not delete {name}: {e}", err=True)
        return

    # Sort branches to rebase in topological order
    sorted_branches = _topological_sort(branches)
    branches_to_rebase_sorted = [
        (b, old, new) for b, old, new in branches_to_rebase if b in sorted_branches
    ]
    # Re-sort based on topological order
    branch_order = {name: i for i, name in enumerate(sorted_branches)}
    branches_to_rebase_sorted.sort(key=lambda x: branch_order.get(x[0], 999))

    if dry_run:
        typer.echo("Would rebase the following branches:")
        for branch, old_parent, new_parent in branches_to_rebase_sorted:
            typer.echo(f"  • {branch}: {old_parent} → {new_parent}")
        typer.echo()
        typer.echo("Would update branch parents:")
        for branch, old_parent, new_parent in branches_to_rebase_sorted:
            typer.echo(f"  • {branch}: {old_parent} → {new_parent}")
        typer.echo()
        typer.echo("Would delete merged branches:")
        for name in merged_branches:
            typer.echo(f"  • {name}")
        return

    # Save current branch to return to later
    original_branch = git.get_current_branch()

    # Perform rebases
    typer.echo("Rebasing branches:")
    rebased_branches: list[tuple[str, str]] = []  # (branch, new_parent)

    for branch, old_parent, new_parent in branches_to_rebase_sorted:
        typer.echo(f"  • {branch} onto {new_parent}...", nl=False)
        try:
            # Get the old parent's commit SHA before rebasing
            old_parent_sha = branches[old_parent].commit_sha

            # Rebase: git rebase --onto new_parent old_parent branch
            git.rebase_onto(new_parent, old_parent_sha, branch)

            rebased_branches.append((branch, new_parent))
            typer.echo(" done")
        except GitError as e:
            typer.echo(" CONFLICT")
            typer.echo(f"\nError: {e}", err=True)
            typer.echo("\nResolve the conflicts, then run:")
            typer.echo("  shortcake sync --continue")
            typer.echo("\nOr abort with:")
            typer.echo("  shortcake sync --abort")
            raise typer.Exit(1) from None

    # Update git notes for rebased branches
    typer.echo("\nUpdating branch parents:")
    for branch, new_parent in rebased_branches:
        notes_data = {"parent": new_parent}
        git.update_notes(json.dumps(notes_data), branch, "shortcake")
        typer.echo(f"  • {branch}: parent → {new_parent}")

    # Delete merged branches
    typer.echo("\nCleaning up merged branches:")
    for name in merged_branches:
        try:
            git.delete_branch(name)
            typer.echo(f"  • Deleted: {name}")
        except GitError as e:
            typer.echo(f"  • Warning: Could not delete {name}: {e}", err=True)

    # Return to original branch if it still exists
    try:
        if original_branch in merged_branches:
            # Original branch was merged, switch to main
            git.checkout_branch(main_branch)
            typer.echo(f"\nSwitched to {main_branch} (original branch was merged)")
        elif git.branch_exists(original_branch):
            git.checkout_branch(original_branch)
    except GitError:
        pass  # Ignore checkout errors

    typer.echo("\nSync complete!")

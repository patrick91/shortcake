import typer

from shortcake.git import GitError, GitRepo
from shortcake.metadata import get_branch_metadata, update_branch_metadata
from shortcake.output import print_error, print_warning
from shortcake.trailers import SHORTCAKE_PARENT_TRAILER

app = typer.Typer()


def find_best_parent(git: GitRepo, branch: str) -> str | None:
    """Find the best parent branch by looking at git history.

    Looks for the closest ancestor branch. First checks non-main branches,
    then falls back to main/master if no other parent is found.

    Args:
        git: GitRepo instance
        branch: The branch to find a parent for

    Returns:
        The name of the best parent branch, or None if no suitable parent found
    """
    all_branches = git.get_branches()
    candidates = []

    for potential_parent in all_branches:
        # Skip the branch itself
        if potential_parent == branch:
            continue

        # Check if this branch is an ancestor of our branch
        if git.is_ancestor(potential_parent, branch):
            # Calculate distance (number of commits between them)
            distance = git.count_commits_between(potential_parent, branch)
            # Skip branches that point to the same commit (distance = 0)
            if distance > 0:
                candidates.append((potential_parent, distance))

    if not candidates:
        return None

    # Sort by distance
    candidates.sort(key=lambda x: x[1])

    # Prefer non-main branches if they're the closest
    # If only main/master is available, use it
    for candidate, _ in candidates:
        if not git.is_trunk_branch(candidate):
            return candidate

    # Fallback to main/master if it's the only option
    return candidates[0][0]


@app.command()
def adopt(
    branch: str | None = typer.Argument(
        None, help="Branch name to adopt (defaults to current branch)"
    ),
    parent: str | None = typer.Option(None, "--parent", "-p", help="Parent branch name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be adopted without actually adopting"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-adopt branch even if already tracked (updates parent)"
    ),
):
    """Adopt an existing branch to be tracked by shortcake.

    Adds shortcake tracking to an existing git branch.
    Parent branch is automatically detected from git history, or can be specified manually.

    Examples:
        shortcake adopt              # Adopt current branch (auto-detect parent)
        shortcake adopt feature-1    # Adopt specific branch (auto-detect parent)
        shortcake adopt feature-2 -p feature-1  # Adopt with explicit parent
        shortcake adopt --dry-run    # Show what would happen without adopting
    """
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    try:
        branch_to_adopt = branch or git.get_current_branch()
        current_branch = git.get_current_branch()

        if not git.branch_exists(branch_to_adopt):
            print_error(f"Branch '{branch_to_adopt}' does not exist")
            raise typer.Exit(1)

        if git.is_trunk_branch(branch_to_adopt):
            print_error(f"Cannot adopt '{branch_to_adopt}' branch")
            raise typer.Exit(1)

        # Check if already tracked
        existing_metadata = get_branch_metadata(branch_to_adopt)

        if existing_metadata.get("parent") and not force:
            print_error(f"Branch '{branch_to_adopt}' is already tracked by shortcake")
            typer.echo("Use 'shortcake ls' to see all tracked branches")
            typer.echo("Use --force to update the parent")
            raise typer.Exit(1)

        # Auto-detect parent if not specified
        if parent is None:
            detected_parent = find_best_parent(git, branch_to_adopt)
            if detected_parent:
                if dry_run:
                    typer.echo(f"Auto-detected parent: {detected_parent}")
                parent = detected_parent
            else:
                # Fall back to main/master if no parent detected
                if git.branch_exists("main"):
                    parent = "main"
                elif git.branch_exists("master"):
                    parent = "master"

        # Validate parent if specified
        if parent:
            if not git.branch_exists(parent):
                print_error(f"Parent branch '{parent}' does not exist")
                raise typer.Exit(1)

        if not dry_run:
            # Use origin/main or origin/master for trunk branches to match restack behavior
            parent_ref = (
                (
                    f"origin/{parent}"
                    if git.is_trunk_branch(parent) and git.has_remote("origin")
                    else parent
                )
                if parent
                else None
            )
            update_branch_metadata(
                branch_to_adopt,
                parent=parent,
                parent_revision=git.get_commit_sha(parent_ref) if parent_ref else None,
            )
            if parent:
                if branch_to_adopt == current_branch:
                    try:
                        git.update_commit_trailers(
                            {SHORTCAKE_PARENT_TRAILER: parent},
                            no_verify=True,
                        )
                    except GitError as e:
                        print_warning(f"Failed to add trailers to commit: {e}")
                else:
                    print_warning(
                        f"Trailers not updated for '{branch_to_adopt}' "
                        "(branch is not checked out)."
                    )

        parent_info = f" with parent '{parent}'" if parent else ""
        action = (
            "Would adopt"
            if dry_run
            else ("Updated" if force and existing_metadata.get("parent") else "Adopted")
        )
        typer.echo(f"{action} branch '{branch_to_adopt}'{parent_info}")

    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

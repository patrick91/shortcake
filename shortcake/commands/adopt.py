import json

import typer

from shortcake.git import GitError, GitRepo

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
    main_branches = {"main", "master"}

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
        if candidate not in main_branches:
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
):
    """Adopt an existing branch to be tracked by shortcake.

    Adds shortcake tracking (git notes) to an existing git branch.
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
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    try:
        branch_to_adopt = branch or git.get_current_branch()

        if not git.branch_exists(branch_to_adopt):
            typer.echo(f"Error: Branch '{branch_to_adopt}' does not exist", err=True)

            raise typer.Exit(1)

        if branch_to_adopt in {"main", "master"}:
            typer.echo(f"Error: Cannot adopt '{branch_to_adopt}' branch", err=True)

            raise typer.Exit(1)

        # Check if already tracked
        existing_notes = git.get_notes(branch_to_adopt, "shortcake")

        if existing_notes:
            typer.echo(
                f"Error: Branch '{branch_to_adopt}' is already tracked by shortcake", err=True
            )
            typer.echo("Use 'shortcake ls' to see all tracked branches")

            raise typer.Exit(1)

        # Auto-detect parent if not specified
        if parent is None:
            detected_parent = find_best_parent(git, branch_to_adopt)
            if detected_parent:
                if dry_run:
                    typer.echo(f"Auto-detected parent: {detected_parent}")
                parent = detected_parent

        # Validate parent if specified
        if parent:
            if not git.branch_exists(parent):
                typer.echo(f"Error: Parent branch '{parent}' does not exist", err=True)

                raise typer.Exit(1)

        if not dry_run:
            notes_data = {}
            if parent:
                notes_data["parent"] = parent
                notes_data["parent_revision"] = git.get_commit_sha(parent)
            notes_json = json.dumps(notes_data)
            git.add_notes(notes_json, branch_to_adopt, "shortcake")

        parent_info = f" with parent '{parent}'" if parent else ""
        action = "Would adopt" if dry_run else "Adopted"
        typer.echo(f"{action} branch '{branch_to_adopt}'{parent_info}")

    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

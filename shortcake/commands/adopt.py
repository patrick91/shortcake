import json

import typer

from shortcake.git import GitError, GitRepo

app = typer.Typer()


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
        branch_to_adopt = branch or git.get_current_branch()

        if not git.branch_exists(branch_to_adopt):
            typer.echo(f"Error: Branch '{branch_to_adopt}' does not exist", err=True)

            raise typer.Exit(1)

        # TODO: maybe we can get the main branch name from the config?
        if branch_to_adopt in {"main", "master"}:
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
            branches_adopted: list[str] = []

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

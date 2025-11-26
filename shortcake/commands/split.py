"""Split command for splitting a branch into multiple stacked branches."""

import json
from pathlib import Path

import typer

from shortcake.git import GitError, GitRepo

app = typer.Typer()

# File to store state during split (in .git directory)
SPLIT_STATE_FILE = ".git/shortcake-split-state.json"


def _get_branch_metadata(git: GitRepo, branch: str) -> dict:
    """Get shortcake metadata for a branch from git notes."""
    notes = git.get_notes(branch, "shortcake")
    if notes:
        try:
            return json.loads(notes)
        except json.JSONDecodeError:
            return {}
    return {}


def _get_children(git: GitRepo, branch: str) -> list[str]:
    """Get all branches that have the given branch as their parent."""
    children = []
    for branch_name in git.get_branches():
        metadata = _get_branch_metadata(git, branch_name)
        if metadata.get("parent") == branch:
            children.append(branch_name)
    return children


def _update_branch_metadata(git: GitRepo, branch: str, metadata: dict) -> None:
    """Update shortcake metadata for a branch in git notes."""
    git.update_notes(json.dumps(metadata), branch, "shortcake")


@app.command()
def split(
    by_hunk: bool = typer.Option(
        False, "--by-hunk", "-h", help="Split by selecting hunks interactively"
    ),
    continue_split: bool = typer.Option(
        False, "--continue", help="Continue after staging changes for next branch"
    ),
    abort: bool = typer.Option(False, "--abort", help="Abort the current split operation"),
):
    """Split a branch into multiple stacked branches.

    This command helps you break up a large branch into smaller, focused branches.
    Currently only --by-hunk mode is supported.

    Workflow:
        1. Run 'shortcake split --by-hunk' to start
        2. Use 'git add -p' or 'git add <files>' to stage changes for first branch
        3. Run 'shortcake split --continue' to create the branch
        4. Repeat steps 2-3 until all changes are committed

    Examples:
        shortcake split --by-hunk      # Start interactive split
        shortcake split --continue     # Create branch from staged changes
        shortcake split --abort        # Abort and restore original state
    """
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    state_file = git.working_dir / SPLIT_STATE_FILE

    # Handle --abort
    if abort:
        if not state_file.exists():
            typer.echo("Error: No split in progress", err=True)
            raise typer.Exit(1)

        try:
            state = json.loads(state_file.read_text())
            original_branch = state["original_branch"]
            original_commit = state["original_commit"]
            original_notes = state.get("original_notes", {})

            # Reset back to original state
            git.checkout_branch(original_branch)
            git.repo.git.reset("--hard", original_commit)

            # Restore original notes
            if original_notes:
                git.update_notes(json.dumps(original_notes), original_branch, "shortcake")

            # Clean up state file
            state_file.unlink()

            typer.echo("Split aborted, restored original state")
            return
        except Exception as e:
            typer.echo(f"Error aborting split: {e}", err=True)
            raise typer.Exit(1) from None

    # Handle --continue
    if continue_split:
        if not state_file.exists():
            typer.echo("Error: No split in progress", err=True)
            typer.echo("Start a split with: shortcake split --by-hunk")
            raise typer.Exit(1)

        try:
            state = json.loads(state_file.read_text())
        except json.JSONDecodeError:
            typer.echo("Error: Invalid split state file", err=True)
            raise typer.Exit(1) from None

        # Check if there are staged changes
        if not git.has_staged_changes():
            typer.echo("Error: No staged changes", err=True)
            typer.echo("Stage changes with 'git add -p' or 'git add <files>'")
            raise typer.Exit(1)

        # Get commit message from user
        typer.echo("Enter commit message for this branch (press Enter for editor):")
        message = typer.prompt("Message", default="", show_default=False)

        if not message.strip():
            # Open editor for commit message
            try:
                git.commit()
            except GitError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            try:
                git.commit(message.strip())
            except GitError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1) from None

        # Generate branch name from commit message
        commit_msg = git.get_last_commit_message()
        from shortcake.commands.create import _generate_branch_name

        branch_name = _generate_branch_name(commit_msg)

        # Check if branch name already exists and make unique
        base_name = branch_name
        counter = 1
        while git.branch_exists(branch_name):
            branch_name = f"{base_name}-{counter}"
            counter += 1

        # Rename current branch to the new name
        current_branch = git.get_current_branch()
        git.rename_branch(current_branch, branch_name)

        # Determine parent for this new branch
        created_branches = state.get("created_branches", [])
        if created_branches:
            # Parent is the last created branch
            parent = created_branches[-1]
        else:
            # Parent is the original branch's parent
            parent = state["original_parent"]

        # Add shortcake notes
        notes = {"parent": parent}
        git.add_notes(json.dumps(notes), branch_name, "shortcake")

        # Track this branch
        created_branches.append(branch_name)
        state["created_branches"] = created_branches
        state_file.write_text(json.dumps(state))

        typer.echo(f"Created branch: {branch_name}")

        # Check if there are remaining unstaged changes
        has_unstaged = git.repo.is_dirty(untracked_files=True)

        if has_unstaged:
            # Create a new temporary branch for remaining work
            temp_branch = f"split-wip-{len(created_branches) + 1}"
            git.create_branch(temp_branch, checkout=True)

            typer.echo("")
            typer.echo("Remaining changes detected.")
            typer.echo("Stage changes for the next branch, then run: shortcake split --continue")
            typer.echo("Or finish with: shortcake split --finish")
        else:
            # No more changes, finish up
            _finish_split(git, state, state_file)

        return

    # Handle --by-hunk (start split)
    if by_hunk:
        # Check if split already in progress
        if state_file.exists():
            typer.echo("Error: A split is already in progress", err=True)
            typer.echo("Run 'shortcake split --continue' or 'shortcake split --abort'")
            raise typer.Exit(1)

        current_branch = git.get_current_branch()

        # Check if on main branch
        if current_branch in ("main", "master"):
            typer.echo("Error: Cannot split main/master branch", err=True)
            raise typer.Exit(1)

        # Check if branch is managed by shortcake
        metadata = _get_branch_metadata(git, current_branch)
        if not metadata.get("parent"):
            typer.echo(
                f"Error: Branch '{current_branch}' is not managed by shortcake. "
                "Use 'shortcake adopt' first.",
                err=True,
            )
            raise typer.Exit(1)

        # Check for children - warn user
        children = _get_children(git, current_branch)
        if children:
            typer.echo(f"Warning: Branch '{current_branch}' has children: {', '.join(children)}")
            typer.echo("After split, you'll need to update their parent manually.")
            if not typer.confirm("Continue?"):
                raise typer.Exit(0)

        # Save state
        original_commit = git.get_current_commit()
        state = {
            "original_branch": current_branch,
            "original_commit": original_commit,
            "original_parent": metadata.get("parent"),
            "original_notes": metadata,
            "children": children,
            "created_branches": [],
        }
        state_file.write_text(json.dumps(state))

        # Soft reset to parent (undo commit but keep changes)
        try:
            git.repo.git.reset("--soft", "HEAD^")
        except Exception as e:
            state_file.unlink()
            typer.echo(f"Error: Failed to reset: {e}", err=True)
            raise typer.Exit(1) from None

        # Unstage all changes
        try:
            git.repo.git.reset("HEAD")
        except Exception:
            pass  # May fail if no HEAD, that's ok

        typer.echo(f"Split started for branch '{current_branch}'")
        typer.echo("")
        typer.echo("All changes are now unstaged. To split:")
        typer.echo("  1. Stage changes for the first branch:")
        typer.echo("     git add -p           # Interactive hunk selection")
        typer.echo("     git add <files>      # Or add specific files")
        typer.echo("")
        typer.echo("  2. Create the branch:")
        typer.echo("     shortcake split --continue")
        typer.echo("")
        typer.echo("  3. Repeat until all changes are committed")
        typer.echo("")
        typer.echo("To abort: shortcake split --abort")
        return

    # No mode specified
    typer.echo("Error: Please specify --by-hunk to start a split", err=True)
    typer.echo("")
    typer.echo("Usage: shortcake split --by-hunk")
    raise typer.Exit(1)


@app.command(name="finish")
def finish():
    """Finish a split operation after all changes are committed."""
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    state_file = git.working_dir / SPLIT_STATE_FILE

    if not state_file.exists():
        typer.echo("Error: No split in progress", err=True)
        raise typer.Exit(1)

    try:
        state = json.loads(state_file.read_text())
    except json.JSONDecodeError:
        typer.echo("Error: Invalid split state file", err=True)
        raise typer.Exit(1) from None

    # Check for uncommitted changes
    if git.repo.is_dirty(untracked_files=True):
        typer.echo("Error: You have uncommitted changes", err=True)
        typer.echo("Either commit them with 'shortcake split --continue'")
        typer.echo("or discard them with 'git checkout -- .'")
        raise typer.Exit(1)

    _finish_split(git, state, state_file)


def _finish_split(git: GitRepo, state: dict, state_file: Path) -> None:
    """Complete the split operation."""
    created_branches = state.get("created_branches", [])
    children = state.get("children", [])

    if not created_branches:
        typer.echo("Error: No branches were created during split", err=True)
        typer.echo("Run 'shortcake split --abort' to restore original state")
        raise typer.Exit(1)

    # Delete the original branch (it's been replaced by the split branches)
    try:
        # First, make sure we're not on a temporary branch
        git.checkout_branch(created_branches[-1])

        # Clean up any temporary split branches
        for branch in git.get_branches():
            if branch.startswith("split-wip-"):
                try:
                    git.delete_branch(branch)
                except GitError:
                    pass
    except GitError:
        pass

    # Update children to point to the last created branch
    if children:
        last_branch = created_branches[-1]
        typer.echo(f"\nUpdating child branches to point to '{last_branch}':")
        for child in children:
            try:
                child_metadata = _get_branch_metadata(git, child)
                child_metadata["parent"] = last_branch
                _update_branch_metadata(git, child, child_metadata)
                typer.echo(f"  • {child}: parent → {last_branch}")
            except GitError as e:
                typer.echo(f"  • {child}: Failed to update - {e}", err=True)

    # Clean up state file
    state_file.unlink()

    typer.echo("")
    typer.echo("Split complete! Created branches:")
    for i, branch in enumerate(created_branches):
        parent = state["original_parent"] if i == 0 else created_branches[i - 1]
        typer.echo(f"  • {branch} (parent: {parent})")

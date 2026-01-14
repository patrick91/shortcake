"""Split command for splitting a branch into multiple stacked branches."""

import json
from pathlib import Path

import typer

from shortcake import get_cli_name
from shortcake.git import GitError, GitRepo
from shortcake.metadata import (
    get_branch_metadata,
    get_children,
    update_branch_metadata,
)
from shortcake.output import print_error, print_warning
from shortcake.trailers import SHORTCAKE_PARENT_TRAILER

app = typer.Typer()

# File to store state during split (in .git directory)
SPLIT_STATE_FILE = ".git/shortcake-split-state.json"


@app.command()
def split(
    by_hunk: bool = typer.Option(
        False, "--by-hunk", "-h", help="Split by selecting hunks interactively"
    ),
    continue_split: bool = typer.Option(
        False, "--continue", help="Continue after staging changes for next branch"
    ),
    abort: bool = typer.Option(False, "--abort", help="Abort the current split operation"),
    no_verify: bool = typer.Option(
        False, "--no-verify", "-n", help="Skip pre-commit and commit-msg hooks"
    ),
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
        print_error(str(e))
        raise typer.Exit(1) from None

    state_file = git.working_dir / SPLIT_STATE_FILE

    # Handle --abort
    if abort:
        if not state_file.exists():
            print_error("No split in progress")
            raise typer.Exit(1)

        try:
            state = json.loads(state_file.read_text())
            original_branch = state["original_branch"]
            original_commit = state["original_commit"]
            original_notes = state.get("original_notes", {})

            # Reset back to original state
            git.checkout_branch(original_branch)
            git.repo.git.reset("--hard", original_commit)

            # Restore original metadata
            if original_notes:
                update_branch_metadata(original_branch, **original_notes)

            # Clean up state file
            state_file.unlink()

            typer.echo("Split aborted, restored original state")
            return
        except Exception as e:
            print_error(f"Failed to abort split: {e}")
            raise typer.Exit(1) from None

    # Handle --continue
    if continue_split:
        if not state_file.exists():
            print_error("No split in progress")
            typer.echo("Start a split with: shortcake split --by-hunk")
            raise typer.Exit(1)

        try:
            state = json.loads(state_file.read_text())
        except json.JSONDecodeError:
            print_error("Invalid split state file")
            raise typer.Exit(1) from None

        # Check if there are staged changes
        if not git.has_staged_changes():
            print_error("No staged changes")
            typer.echo("Stage changes with 'git add -p' or 'git add <files>'")
            raise typer.Exit(1)

        original_branch = state["original_branch"]
        original_used = state.get("original_branch_used", False)
        original_message = state.get("original_message", "")

        # Ask if user wants to reuse the original branch (only if not already used)
        if not original_used:
            reuse_original = typer.confirm(
                f"Use original branch '{original_branch}'?", default=True
            )
        else:
            reuse_original = False

        if reuse_original:
            # Use original branch name and offer original message as default
            branch_name = original_branch
            state["original_branch_used"] = True

            # Prompt for commit message with original as default
            message = typer.prompt("Commit message", default=original_message)
        else:
            # Ask for new branch name
            branch_name = typer.prompt("Branch name", default="", show_default=False)

            # Ask for commit message
            message = typer.prompt("Commit message", default="", show_default=False)

        # Commit the changes
        if not message.strip():
            # Open editor for commit message
            try:
                git.commit(no_verify=no_verify)
            except GitError as e:
                print_error(str(e))
                raise typer.Exit(1) from None
        else:
            try:
                git.commit(message.strip(), no_verify=no_verify)
            except GitError as e:
                print_error(str(e))
                raise typer.Exit(1) from None

        # If no branch name was provided, generate from commit message
        if not branch_name.strip():
            commit_msg = git.get_last_commit_message()
            from shortcake.commands.create import _generate_branch_name

            branch_name = _generate_branch_name(commit_msg)

        # Check if branch name already exists (and it's not the original we're reusing)
        if git.branch_exists(branch_name) and branch_name != original_branch:
            # Make unique
            base_name = branch_name
            counter = 1
            while git.branch_exists(f"{base_name}-{counter}"):
                counter += 1
            branch_name = f"{base_name}-{counter}"
            typer.echo(f"Branch '{base_name}' exists, using '{branch_name}'")

        # Rename current branch to the new name
        current_branch = git.get_current_branch()
        if current_branch != branch_name:
            git.rename_branch(current_branch, branch_name)

        # Determine parent for this new branch
        created_branches = state.get("created_branches", [])
        if created_branches:
            # Parent is the last created branch
            parent = created_branches[-1]
        else:
            # Parent is the original branch's parent
            parent = state["original_parent"]

        # Store metadata with parent_revision
        update_branch_metadata(
            branch_name,
            parent=parent,
            parent_revision=git.get_commit_sha(parent),
        )
        try:
            git.update_commit_trailers(
                {SHORTCAKE_PARENT_TRAILER: parent},
                no_verify=True,
            )
        except GitError as e:
            print_warning(f"Failed to add trailers to commit: {e}")

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
            typer.echo("Or discard remaining changes with: git checkout -- .")
        else:
            # No more changes, finish up
            _finish_split(git, state, state_file)

        return

    # Handle --by-hunk (start split)
    if by_hunk:
        # Check if split already in progress
        if state_file.exists():
            print_error("A split is already in progress")
            typer.echo("Run 'shortcake split --continue' or 'shortcake split --abort'")
            raise typer.Exit(1)

        current_branch = git.get_current_branch()
        cli = get_cli_name()

        # Check if on main branch
        if git.is_trunk_branch(current_branch):
            print_error("Cannot split main/master branch")
            raise typer.Exit(1)

        # Check if branch is managed by shortcake
        metadata = get_branch_metadata(current_branch)
        if not metadata.get("parent"):
            typer.echo(
                f"Error: Branch '{current_branch}' is not managed by shortcake. "
                f"Use '{cli} adopt' first.",
                err=True,
            )
            raise typer.Exit(1)

        # Check for children - warn user
        children = get_children(current_branch)
        if children:
            typer.echo(f"Warning: Branch '{current_branch}' has children: {', '.join(children)}")
            typer.echo("After split, you'll need to update their parent manually.")
            if not typer.confirm("Continue?"):
                raise typer.Exit(0)

        # Save state
        original_commit = git.get_current_commit()
        parent_branch = metadata.get("parent")

        # Find the commit where this branch diverged from parent
        # Use parent_revision from metadata, or calculate merge-base
        parent_revision = metadata.get("parent_revision")
        if not parent_revision:
            try:
                parent_revision = git.get_merge_base(current_branch, parent_branch)
            except GitError:
                parent_revision = None

        if not parent_revision:
            print_error(f"Cannot determine where branch diverged from {parent_branch}")
            raise typer.Exit(1)

        # Count commits in this branch
        try:
            commit_count = git.count_commits_between(parent_revision, "HEAD")
        except GitError:
            commit_count = 1

        # Get the original commit message (for when user wants to reuse original branch)
        original_message = git.get_last_commit_message()

        state = {
            "original_branch": current_branch,
            "original_commit": original_commit,
            "original_parent": parent_branch,
            "original_notes": metadata,
            "original_message": original_message,
            "children": children,
            "created_branches": [],
            "parent_revision": parent_revision,
        }
        state_file.write_text(json.dumps(state))

        # Soft reset to parent revision (undo all commits in this branch but keep changes)
        try:
            git.repo.git.reset("--soft", parent_revision)
        except Exception as e:
            state_file.unlink()
            print_error(f"Failed to reset: {e}")
            raise typer.Exit(1) from None

        # Unstage all changes
        try:
            git.repo.git.reset("HEAD")
        except Exception:
            pass  # May fail if no HEAD, that's ok

        typer.echo(f"Split started for branch '{current_branch}' ({commit_count} commit(s))")
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
    print_error("Please specify --by-hunk to start a split")
    typer.echo("")
    typer.echo("Usage: shortcake split --by-hunk")
    raise typer.Exit(1)


def _finish_split(git: GitRepo, state: dict, state_file: Path) -> None:
    """Complete the split operation."""
    created_branches = state.get("created_branches", [])
    children = state.get("children", [])
    original_branch = state["original_branch"]
    original_used = state.get("original_branch_used", False)

    if not created_branches:
        print_error("No branches were created during split")
        typer.echo("Run 'shortcake split --abort' to restore original state")
        raise typer.Exit(1)

    # Clean up any temporary split branches first
    try:
        # Make sure we're on the last created branch
        git.checkout_branch(created_branches[-1])

        for branch in git.get_branches():
            if branch.startswith("split-wip-"):
                try:
                    git.delete_branch(branch)
                except GitError:
                    pass
    except GitError:
        pass

    # Find which branch has the original name (if any) for updating children
    # The original branch name preserves the existing PR
    if original_used:
        # User explicitly chose to use the original branch name for one of the splits
        final_branch = original_branch
    else:
        # Original branch name wasn't used - warn user
        print_warning(
            f"Original branch name '{original_branch}' was not used. "
            "The existing PR (if any) will be orphaned."
        )
        final_branch = created_branches[-1]

    # Update children to point to the last branch in the stack
    if children:
        typer.echo(f"\nUpdating child branches to point to '{final_branch}':")
        for child in children:
            try:
                update_branch_metadata(child, parent=final_branch)
                typer.echo(f"  • {child}: parent → {final_branch}")
            except Exception as e:
                print_warning(f"{child}: Failed to update - {e}")

    # Clean up state file
    state_file.unlink()

    typer.echo("")
    typer.echo("Split complete! Created branches:")
    for i, branch in enumerate(created_branches):
        parent = state["original_parent"] if i == 0 else created_branches[i - 1]
        typer.echo(f"  • {branch} (parent: {parent})")

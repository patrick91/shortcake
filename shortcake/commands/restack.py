"""Restack command for rebasing stacked branches onto updated parent branches."""

import json
from dataclasses import dataclass

import typer

from shortcake.git import GitError, GitRepo

# File to store notes during restack (in .git directory)
RESTACK_STATE_FILE = ".git/shortcake-restack-state.json"

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


def _get_remote_ref(git: GitRepo, branch: str) -> str:
    """Get the remote ref for a branch if it's a trunk branch.

    For main/master, returns origin/main or origin/master to ensure
    we rebase onto the latest remote version.

    Args:
        git: GitRepo instance
        branch: The branch name

    Returns:
        The remote ref (e.g., origin/main) or the original branch name
    """
    if branch in ("main", "master") and git.has_remote("origin"):
        return f"origin/{branch}"
    return branch


def _needs_restack(
    git: GitRepo, branch: str, parent: str, metadata: dict, debug: bool = False
) -> bool:
    """Check if a branch needs to be restacked onto its parent.

    A branch needs restacking if the stored parent_revision doesn't match
    the parent's current HEAD. This is the same approach as Graphite/Charcoal.

    Args:
        git: GitRepo instance
        branch: The branch to check
        parent: The parent branch
        metadata: The branch's shortcake metadata
        debug: If True, print debug information

    Returns:
        True if the branch needs rebasing, False otherwise
    """
    try:
        # Get the stored parent revision from metadata
        stored_parent_rev = metadata.get("parent_revision")

        # Get the current commit of the parent
        parent_commit = git.get_commit_sha(parent)

        if debug:
            typer.echo(f"    DEBUG: branch={branch}, parent={parent}")
            typer.echo(f"    DEBUG: stored_parent_rev={stored_parent_rev}")
            typer.echo(f"    DEBUG: parent_commit={parent_commit}")
            typer.echo(f"    DEBUG: needs_restack={stored_parent_rev != parent_commit}")

        # If we have a stored parent revision, compare it
        if stored_parent_rev:
            return stored_parent_rev != parent_commit

        # Fallback: use merge-base if no stored revision (legacy branches)
        merge_base = git.get_merge_base(branch, parent)
        if not merge_base:
            return True  # Can't determine, assume needs restack

        return merge_base != parent_commit
    except GitError:
        return True  # On error, assume needs restack


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

        # Use remote ref for trunk branches (main/master)
        rebase_target = _get_remote_ref(git, parent)

        branches.append(
            BranchInfo(
                name=branch,
                parent=rebase_target,
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
    debug: bool = typer.Option(False, "--debug", help="Show debug information"),
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

            # Clean up state file
            state_file = git.working_dir / RESTACK_STATE_FILE
            if state_file.exists():
                state_file.unlink()

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

            # Restore notes from saved state
            state_file = git.working_dir / RESTACK_STATE_FILE
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text())
                    saved_notes = state.get("notes", {})
                    current_branch = git.get_current_branch()

                    # Restore notes for the current branch
                    if current_branch in saved_notes:
                        git.update_notes(
                            json.dumps(saved_notes[current_branch]),
                            current_branch,
                            "shortcake",
                        )
                        typer.echo(f"Restored tracking for {current_branch}")

                    # Clean up state file
                    state_file.unlink()
                except (json.JSONDecodeError, KeyError):
                    pass  # Ignore errors reading state

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
        typer.echo("\nWould check the following branches:")
        for branch in all_branches:
            needs = _needs_restack(git, branch.name, branch.parent, branch.notes_data, debug=debug)
            status = "needs restack" if needs else "up to date"
            typer.echo(f"  {branch.name} → {branch.parent} ({status})")
        return

    typer.echo(f"\nChecking {len(all_branches)} branch(es)...")

    # Save notes for all branches before rebasing (SHAs will change)
    saved_notes: dict[str, dict] = {}
    for branch in all_branches:
        saved_notes[branch.name] = branch.notes_data

    # Rebase each branch in order, but only if needed
    restacked_count = 0
    for branch in all_branches:
        # Check if this branch actually needs rebasing
        if not _needs_restack(git, branch.name, branch.parent, branch.notes_data, debug=debug):
            typer.echo(f"  {branch.name} does not need to be restacked")
            continue

        typer.echo(f"  Rebasing {branch.name} onto {branch.parent}...", nl=False)
        restacked_count += 1

        try:
            # Use stored parent_revision as the rebase --from point
            # This is what Charcoal/Graphite do - it's the SHA where the branch
            # was originally based, which may differ from merge-base if parent was rebased
            stored_parent_rev = branch.notes_data.get("parent_revision")

            if stored_parent_rev:
                # Rebase: git rebase --onto parent stored_parent_rev branch
                git.rebase_onto(branch.parent, stored_parent_rev, branch.name)
            else:
                # Fallback for legacy branches without parent_revision: use merge-base
                merge_base = git.get_merge_base(branch.name, branch.parent)
                if merge_base:
                    git.rebase_onto(branch.parent, merge_base, branch.name)
                else:
                    git.checkout_branch(branch.name)
                    git.rebase(branch.parent)

            # Update notes with new parent_revision and re-attach to new commit SHA
            updated_notes = saved_notes[branch.name].copy()
            updated_notes["parent_revision"] = git.get_commit_sha(branch.parent)
            git.update_notes(json.dumps(updated_notes), branch.name, "shortcake")

            typer.echo(" done")

        except GitError as e:
            typer.echo(" CONFLICT")

            # Save state so we can restore notes on --continue
            state_file = git.working_dir / RESTACK_STATE_FILE
            state = {"notes": saved_notes}
            state_file.write_text(json.dumps(state))

            typer.echo(f"\nError: {e}", err=True)
            typer.echo("\nTo resolve:")
            typer.echo("  1. Fix the conflicts in the affected files")
            typer.echo("  2. Stage the resolved files: git add <files>")
            typer.echo("  3. Continue the restack: shortcake restack --continue")
            typer.echo("\nOr abort with:")
            typer.echo("  shortcake restack --abort")
            raise typer.Exit(1) from None

    # Return to the original branch
    try:
        git.checkout_branch(current_branch)
    except GitError:
        pass  # Ignore checkout errors

    if restacked_count > 0:
        typer.echo(f"\nRestack complete! Rebased {restacked_count} branch(es).")
    else:
        typer.echo("\nAll branches are up to date, nothing to restack.")

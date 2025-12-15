"""Restack command for rebasing stacked branches onto updated parent branches."""

from dataclasses import dataclass

import typer

from shortcake import get_cli_name
from shortcake.commands.sync import _is_branch_merged
from shortcake.git import GitError, GitRepo
from shortcake.metadata import (
    delete_branch_metadata,
    get_all_branch_metadata,
    get_branch_metadata,
    get_children,
    update_branch_metadata,
)
from shortcake.output import print_error, print_warning

app = typer.Typer()


@dataclass
class RestackBranchInfo:
    """Information about a branch for restacking."""

    name: str
    parent: str
    metadata: dict


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
    if git.is_trunk_branch(branch) and git.has_remote("origin"):
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


def _get_stack_from_current(current_branch: str) -> list[RestackBranchInfo]:
    """Get the stack of branches from trunk up to current branch.

    Walks up from current branch to find all ancestors in the stack,
    then returns them in order from bottom (closest to trunk) to top.

    Args:
        current_branch: The current branch (top of stack)

    Returns:
        List of RestackBranchInfo from bottom of stack to current branch
    """
    all_metadata = get_all_branch_metadata()
    branches = []
    branch = current_branch

    while branch:
        metadata = all_metadata.get(branch, {})
        parent = metadata.get("parent")

        if not parent:
            break  # Not a shortcake-managed branch or reached trunk

        branches.append(
            RestackBranchInfo(
                name=branch,
                parent=parent,
                metadata=metadata,
            )
        )

        # Check if parent is also a shortcake branch
        parent_metadata = all_metadata.get(parent, {})
        if not parent_metadata.get("parent"):
            break  # Parent is trunk (main/master)

        branch = parent

    # Reverse so branches are ordered from bottom of stack to top
    branches.reverse()
    return branches


def _get_descendant_branches(branch: str) -> list[RestackBranchInfo]:
    """Get all descendant branches (children, grandchildren, etc.) in topological order.

    Args:
        branch: The branch to find descendants of

    Returns:
        List of RestackBranchInfo for all descendants, in topological order (parents before children)
    """
    all_metadata = get_all_branch_metadata()
    result = []
    queue = get_children(branch)

    while queue:
        child = queue.pop(0)
        metadata = all_metadata.get(child, {})
        if metadata.get("parent"):
            result.append(
                RestackBranchInfo(
                    name=child,
                    parent=metadata["parent"],
                    metadata=metadata,
                )
            )
            # Add this child's children to the queue
            queue.extend(get_children(child))

    return result


def _cleanup_merged_branch(git: GitRepo, branch: str, main_branch: str) -> bool:
    """Clean up a merged branch by updating children and deleting it.

    Args:
        git: GitRepo instance
        branch: The merged branch to clean up
        main_branch: The main/trunk branch name

    Returns:
        True if cleanup was successful, False otherwise
    """
    metadata = get_branch_metadata(branch)
    parent = metadata.get("parent", main_branch)

    # Update children to point to the merged branch's parent
    children = get_children(branch)
    for child in children:
        # Get current parent revision from the parent (not the merged branch)
        try:
            parent_ref = f"origin/{parent}" if git.is_trunk_branch(parent) else parent
            parent_sha = git.get_commit_sha(parent_ref)
        except GitError:
            parent_sha = None

        update_branch_metadata(child, parent=parent, parent_revision=parent_sha)
        typer.echo(f"  Updated {child}'s parent: {branch} → {parent}")

    # Check if branch is checked out in a worktree
    worktree_path = git.get_worktree_for_branch(branch)
    if worktree_path:
        try:
            git.checkout_in_worktree(worktree_path, main_branch)
            typer.echo(f"  Switched worktree at {worktree_path} to {main_branch}")
        except GitError as e:
            print_warning(f"Could not switch worktree at {worktree_path}: {e}")
            return False

    # Delete the branch
    try:
        git.delete_branch(branch)
        delete_branch_metadata(branch)
        typer.echo(f"  Deleted merged branch: {branch}")
        return True
    except GitError as e:
        print_warning(f"Could not delete {branch}: {e}")
        return False


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
        print_error(str(e))
        raise typer.Exit(1) from None

    cli = get_cli_name()

    # Handle --abort
    if abort:
        if not git.is_rebase_in_progress():
            print_error("No rebase in progress")
            raise typer.Exit(1)
        try:
            git.rebase_abort()
            typer.echo("Rebase aborted")
            return
        except GitError as e:
            print_error(str(e))
            raise typer.Exit(1) from None

    # Handle --continue
    if continue_rebase:
        if not git.is_rebase_in_progress():
            print_error("No rebase in progress")
            raise typer.Exit(1)
        try:
            git.rebase_continue()
            typer.echo("Rebase continued successfully")

            # Update parent_revision now that rebase is complete
            current_branch = git.get_current_branch()
            metadata = get_branch_metadata(current_branch)
            parent = metadata.get("parent")
            if parent:
                # Use remote ref for trunk branches
                parent_ref = _get_remote_ref(git, parent)
                try:
                    parent_sha = git.get_commit_sha(parent_ref)
                    update_branch_metadata(current_branch, parent_revision=parent_sha)
                except GitError:
                    pass  # Parent ref doesn't exist, skip update

            return
        except GitError as e:
            print_error(str(e))
            raise typer.Exit(1) from None

    # Check for rebase in progress
    if git.is_rebase_in_progress():
        print_error("A rebase is already in progress")
        typer.echo(f"Run '{cli} restack --continue' after resolving conflicts")
        typer.echo(f"Or run '{cli} restack --abort' to abort")
        raise typer.Exit(1)

    current_branch = git.get_current_branch()

    # Check if on main branch
    if git.is_trunk_branch(current_branch):
        print_error("Cannot restack from main/master branch")
        raise typer.Exit(1)

    # Check if current branch is managed by shortcake
    metadata = get_branch_metadata(current_branch)
    if not metadata.get("parent"):
        print_error(
            f"Branch '{current_branch}' is not managed by shortcake. " f"Use '{cli} adopt' first."
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

                # Update local main to origin/main if behind
                main_branch = git.get_main_branch()
                remote_main = f"origin/{main_branch}"
                local_main_sha = git.get_commit_sha(main_branch)
                remote_main_sha = git.get_commit_sha(remote_main)

                if local_main_sha != remote_main_sha:
                    if git.is_ancestor(local_main_sha, remote_main_sha):
                        if current_branch == main_branch:
                            git.merge_ff_only(remote_main)
                        else:
                            git.update_ref(f"refs/heads/{main_branch}", remote_main_sha)
                        typer.echo(f"Updated {main_branch} to latest")
            except GitError as e:
                print_warning(f"Failed to fetch: {e}")

    # Fast-forward branches that are behind their remote counterparts
    # This handles cases where the branch was updated via GitHub UI or another machine
    if git.has_remote("origin"):
        all_metadata = get_all_branch_metadata()
        branches_to_check = [current_branch]
        # Also check parent branches in the stack
        branch = current_branch
        while branch:
            meta = all_metadata.get(branch, {})
            parent = meta.get("parent")
            if parent and not git.is_trunk_branch(parent) and git.branch_exists(parent):
                branches_to_check.append(parent)
                branch = parent
            else:
                break

        for branch_name in branches_to_check:
            remote_ref = f"origin/{branch_name}"
            try:
                remote_sha = git.get_commit_sha(remote_ref)
                local_sha = git.get_commit_sha(branch_name)

                if remote_sha != local_sha and git.is_ancestor(local_sha, remote_sha):
                    if dry_run:
                        typer.echo(f"Would fast-forward {branch_name} to {remote_ref}")
                    else:
                        if branch_name == current_branch:
                            git.merge_ff_only(remote_ref)
                        else:
                            git.update_ref(f"refs/heads/{branch_name}", remote_sha)
                        typer.echo(f"Fast-forwarded {branch_name} to match remote")

                        # Update metadata to reflect the new parent_revision from remote
                        branch_meta = all_metadata.get(branch_name, {})
                        parent = branch_meta.get("parent")
                        if parent:
                            parent_ref = _get_remote_ref(git, parent)
                            try:
                                new_parent_rev = git.get_merge_base(remote_sha, parent_ref)
                                if new_parent_rev:
                                    update_branch_metadata(
                                        branch_name, parent_revision=new_parent_rev
                                    )
                            except GitError:
                                pass
            except GitError:
                continue  # Remote branch doesn't exist

    # Check for merged branches in the stack and prompt for cleanup
    main_branch = git.get_main_branch()
    merge_target = f"origin/{main_branch}" if git.has_remote("origin") else main_branch

    # Check current branch first
    if _is_branch_merged(git, current_branch, merge_target):
        typer.echo(f"\nBranch '{current_branch}' appears to have been merged.")
        if typer.confirm("Delete it and update children?", default=False):
            _cleanup_merged_branch(git, current_branch, main_branch)
            # Switch to main since current branch was deleted
            git.checkout_branch(main_branch)
            typer.echo(f"\nSwitched to {main_branch}")
            return
        else:
            typer.echo("Skipping cleanup. You can run 'sc sync' later to clean up.")

    # Check parent branches in the stack for merged status
    parent = metadata.get("parent")
    if parent and not git.is_trunk_branch(parent) and git.branch_exists(parent):
        if _is_branch_merged(git, parent, merge_target):
            typer.echo(f"\nParent branch '{parent}' appears to have been merged.")
            if typer.confirm("Delete it and update children?", default=False):
                _cleanup_merged_branch(git, parent, main_branch)
                # Refresh metadata since parent changed
                metadata = get_branch_metadata(current_branch)

    # Check if parent branch exists - if not, suggest running sync
    parent = metadata.get("parent")
    if parent and not git.is_trunk_branch(parent):
        if not git.branch_exists(parent):
            print_error(
                f"Parent branch '{parent}' no longer exists. "
                f"This usually means it was merged. Run '{cli} sync' to update parent references."
            )
            raise typer.Exit(1)

    # Get stack from trunk up to current branch
    stack_up = _get_stack_from_current(current_branch)

    # Get descendant branches (children, grandchildren, etc.)
    descendants = _get_descendant_branches(current_branch)

    # Combine: stack up to current + descendants
    all_branches = stack_up + descendants

    if not all_branches:
        typer.echo("No branches to restack")
        return

    # Resolve parent refs (use origin/main for trunk)
    for branch in all_branches:
        branch.parent = _get_remote_ref(git, branch.parent)

    if dry_run:
        typer.echo("\nWould check the following branches:")
        for branch in all_branches:
            needs = _needs_restack(git, branch.name, branch.parent, branch.metadata, debug=debug)
            status = "needs restack" if needs else "up to date"
            typer.echo(f"  {branch.name} → {branch.parent} ({status})")
        return

    # Check for uncommitted changes before rebasing
    if git.has_uncommitted_changes():
        print_error(
            "You have uncommitted changes. Please commit or stash them before restacking.\n"
            "  To stash: git stash\n"
            "  To discard: git checkout -- <file>"
        )
        raise typer.Exit(1)

    typer.echo(f"\nChecking {len(all_branches)} branch(es)...")

    # Rebase each branch in order, but only if needed
    restacked_count = 0
    for branch in all_branches:
        # Check if this branch actually needs rebasing
        if not _needs_restack(git, branch.name, branch.parent, branch.metadata, debug=debug):
            typer.echo(f"  {branch.name} does not need to be restacked")
            continue

        typer.echo(f"  Rebasing {branch.name} onto {branch.parent}...", nl=False)
        restacked_count += 1

        try:
            # Use stored parent_revision as the rebase --from point
            # This is what Charcoal/Graphite do - it's the SHA where the branch
            # was originally based, which may differ from merge-base if parent was rebased
            stored_parent_rev = branch.metadata.get("parent_revision")

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

            # Update metadata with new parent_revision
            update_branch_metadata(branch.name, parent_revision=git.get_commit_sha(branch.parent))

            typer.echo(" done")

        except GitError as e:
            typer.echo(" CONFLICT")

            print_error(str(e))
            typer.echo("\nTo resolve:")
            typer.echo("  1. Fix the conflicts in the affected files")
            typer.echo("  2. Stage the resolved files: git add <files>")
            typer.echo(f"  3. Continue the restack: {cli} restack --continue")
            typer.echo("\nOr abort with:")
            typer.echo(f"  {cli} restack --abort")
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

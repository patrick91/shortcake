"""Sync command for rebasing stacked branches after merges."""

from dataclasses import dataclass

import typer

from shortcake import get_cli_name
from shortcake.git import GitError, GitRepo
from shortcake.github import GitHubClient, GitHubError, get_github_repo_info
from shortcake.metadata import (
    delete_branch_metadata,
    get_all_branch_metadata,
    get_branch_metadata,
    update_branch_metadata,
)
from shortcake.output import print_error, print_warning

app = typer.Typer()


@dataclass
class SyncBranchInfo:
    """Information about a branch for syncing."""

    name: str
    parent: str | None
    commit_sha: str


def _get_tracked_branches(git: GitRepo) -> dict[str, SyncBranchInfo]:
    """Get all shortcake-tracked branches with their metadata.

    Only includes branches that exist locally.

    Returns:
        Dict mapping branch name to SyncBranchInfo.
    """
    branches: dict[str, SyncBranchInfo] = {}
    all_metadata = get_all_branch_metadata()

    for branch_name, metadata in all_metadata.items():
        # Skip branches that don't exist locally (may have been manually deleted)
        if not git.branch_exists(branch_name):
            continue
        branches[branch_name] = SyncBranchInfo(
            name=branch_name,
            parent=metadata.get("parent"),
            commit_sha=git.get_commit_sha(branch_name),
        )

    return branches


def _is_branch_merged(
    git: GitRepo,
    branch: str,
    into: str = "main",
    pr_number: int | None = None,
    github_client: GitHubClient | None = None,
    github_owner: str | None = None,
    github_repo: str | None = None,
) -> bool:
    """Check if a branch has been merged into another branch.

    Handles both regular merges and squash merges:
    0. GitHub API check: if PR number is available, query GitHub directly
    1. Regular/rebase merge: branch is an ancestor of target
    2. Squash merge via tree comparison: branch's file state matches target
    3. Squash merge via cherry: uses git cherry to detect equivalent patches

    Args:
        git: GitRepo instance.
        branch: The branch to check.
        into: The branch to check if merged into (default: main).
        pr_number: Optional PR number associated with this branch.
        github_client: Optional GitHubClient instance.
        github_owner: Optional GitHub repo owner.
        github_repo: Optional GitHub repo name.

    Returns:
        True if branch is merged into target.
    """
    if not git.branch_exists(branch):
        return True  # Branch was deleted, consider it merged

    # Check GitHub API first if we have PR info (most reliable for squash merges)
    if pr_number and github_client and github_owner and github_repo:
        try:
            if github_client.is_pr_merged(github_owner, github_repo, pr_number):
                return True
        except Exception:
            pass  # Fall through to git-based detection

    # Check for regular merge (branch is ancestor of target)
    if git.is_ancestor(branch, into):
        return True

    # Check for squash merge (branch's tree state matches target)
    if git.is_tree_subset(branch, into):
        return True

    # Check for squash merge using git cherry (most reliable)
    return git.is_squash_merged(branch, into)


def _topological_sort(branches: dict[str, SyncBranchInfo]) -> list[str]:
    """Sort branches in topological order (parents before children).

    Args:
        branches: Dict of branch name to SyncBranchInfo.

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
    branches: dict[str, SyncBranchInfo],
    main_branch: str,
    merged_branches: list[str],
) -> str:
    """Find the new parent for a branch whose parent was merged.

    Walks up the parent chain until we find a non-merged branch or main.

    Args:
        branch: The branch that needs a new parent.
        old_parent: The old (merged) parent.
        branches: All tracked branches.
        main_branch: Name of the main branch.
        merged_branches: List of branches that have been merged.

    Returns:
        The name of the new parent branch.
    """
    # Walk up the parent chain until we find a non-merged branch or reach main
    current = old_parent
    while current and current != main_branch:
        if current not in branches:
            return main_branch

        # If current is not merged, it's our new parent
        if current not in merged_branches:
            return current

        # Current is merged, walk up to its parent
        current = branches[current].parent

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
        print_error(str(e))
        raise typer.Exit(1) from None

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
                parent_ref = (
                    f"origin/{parent}"
                    if git.is_trunk_branch(parent) and git.has_remote("origin")
                    else parent
                )
                try:
                    parent_sha = git.get_commit_sha(parent_ref)
                    update_branch_metadata(current_branch, parent_revision=parent_sha)
                except GitError:
                    pass  # Parent ref doesn't exist, skip update

            return
        except GitError as e:
            print_error(str(e))
            raise typer.Exit(1) from None

    cli = get_cli_name()

    # Check for rebase in progress
    if git.is_rebase_in_progress():
        print_error("A rebase is already in progress")
        typer.echo(f"Run '{cli} sync --continue' after resolving conflicts")
        typer.echo(f"Or run '{cli} sync --abort' to abort")
        raise typer.Exit(1)

    try:
        main_branch = git.get_main_branch()
    except GitError as e:
        print_error(str(e))
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
                print_warning(f"Failed to fetch: {e}")

    # Fast-forward main branch if it's behind remote
    main_updated = False
    if git.has_remote("origin"):
        remote_main = f"origin/{main_branch}"
        try:
            remote_sha = git.get_commit_sha(remote_main)
            local_sha = git.get_commit_sha(main_branch)

            if remote_sha != local_sha and git.is_ancestor(local_sha, remote_sha):
                if dry_run:
                    typer.echo(f"Would fast-forward {main_branch} to {remote_main}")
                else:
                    current_branch = git.get_current_branch()
                    if current_branch == main_branch:
                        git.merge_ff_only(remote_main)
                    else:
                        git.update_ref(f"refs/heads/{main_branch}", remote_sha)
                    typer.echo(f"Fast-forwarded {main_branch} to {remote_main}")
                    main_updated = True
        except GitError:
            pass  # Remote main doesn't exist or other issue

    # Get all tracked branches
    branches = _get_tracked_branches(git)

    if not branches:
        if main_updated:
            typer.echo("Sync complete!")
        else:
            typer.echo("All branches are up to date - nothing to sync")
        return

    # Check for branches that are behind their remote and fast-forward them
    current_branch = git.get_current_branch()
    branches_updated: list[str] = []

    for name in branches:
        remote_ref = f"origin/{name}"
        try:
            remote_sha = git.get_commit_sha(remote_ref)
            local_sha = git.get_commit_sha(name)

            if remote_sha == local_sha:
                continue  # Already up to date

            # Check if local is behind remote (remote is ahead)
            if git.is_ancestor(local_sha, remote_sha):
                if dry_run:
                    typer.echo(f"Would fast-forward {name} to {remote_ref}")
                else:
                    # Fast-forward the branch
                    if name == current_branch:
                        # For current branch, use merge --ff-only to update working dir
                        git.merge_ff_only(remote_ref)
                    else:
                        git.update_ref(f"refs/heads/{name}", remote_sha)
                    branches_updated.append(name)

                    # Update metadata to reflect the new parent_revision from remote
                    branch_meta = branches[name]
                    parent = branch_meta.parent
                    if parent:
                        parent_ref = (
                            f"origin/{parent}"
                            if git.is_trunk_branch(parent) and git.has_remote("origin")
                            else parent
                        )
                        try:
                            new_parent_rev = git.get_merge_base(remote_sha, parent_ref)
                            if new_parent_rev:
                                update_branch_metadata(name, parent_revision=new_parent_rev)
                        except GitError:
                            pass
        except GitError:
            continue  # Remote branch doesn't exist

    if branches_updated:
        typer.echo(f"Fast-forwarded {len(branches_updated)} branch(es) to match remote:")
        for name in branches_updated:
            typer.echo(f"  • {name}")
        typer.echo()

        # Refresh branches dict after updates
        branches = _get_tracked_branches(git)

    # Use remote main for merge detection if available (after fetch)
    merge_target = main_branch
    if git.has_remote("origin"):
        remote_main = f"origin/{main_branch}"
        try:
            git.get_commit_sha(remote_main)
            merge_target = remote_main
        except GitError:
            pass  # Remote ref doesn't exist, use local

    # Initialize GitHub client for PR-based merge detection
    github_client: GitHubClient | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    try:
        github_owner, github_repo = get_github_repo_info(git)
        github_client = GitHubClient()
    except (GitHubError, Exception):
        pass  # GitHub not available, fall back to git-based detection

    # Find merged branches
    all_metadata = get_all_branch_metadata()
    merged_branches: list[str] = []
    for name in branches:
        pr_number = all_metadata.get(name, {}).get("pr_number")
        if _is_branch_merged(
            git, name, merge_target, pr_number, github_client, github_owner, github_repo
        ):
            merged_branches.append(name)

    if not merged_branches:
        if main_updated or branches_updated:
            typer.echo("Sync complete!")
        else:
            typer.echo("All branches are up to date - nothing to sync")
        return

    if dry_run:
        typer.echo("Detected merged branches:")
        for name in merged_branches:
            typer.echo(f"  ✓ {name}")
        typer.echo()

    # Find branches that need rebasing (their parent was merged or deleted)
    # rebase_target is origin/main if available, otherwise main
    rebase_target = merge_target  # This is origin/main or main from earlier

    branches_to_rebase: list[tuple[str, str, str]] = []  # (branch, old_parent, new_parent)

    for name, info in branches.items():
        if name in merged_branches:
            continue  # Skip merged branches

        if info.parent:
            # Check if parent was merged OR if parent no longer exists
            parent_merged = info.parent in merged_branches
            parent_missing = (
                info.parent not in branches
                and info.parent != main_branch
                and not git.branch_exists(info.parent)
            )

            if parent_merged or parent_missing:
                # Use rebase_target (origin/main) for rebasing onto trunk
                new_parent = _find_new_parent(
                    name, info.parent, branches, main_branch, merged_branches
                )
                # If new_parent is main, use the rebase_target instead
                if new_parent == main_branch:
                    new_parent = rebase_target
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
                    delete_branch_metadata(name)
                    typer.echo(f"Deleted merged branch: {name}")
                except GitError as e:
                    print_warning(f"Could not delete {name}: {e}")
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

    # Check for uncommitted changes before rebasing
    if git.has_uncommitted_changes():
        print_error(
            "You have uncommitted changes. Please commit or stash them before syncing.\n"
            "  To stash: git stash\n"
            "  To discard: git checkout -- <file>"
        )
        raise typer.Exit(1)

    # Save current branch to return to later
    original_branch = git.get_current_branch()

    # Perform rebases
    typer.echo("Rebasing branches:")
    rebased_branches: list[tuple[str, str]] = []  # (branch, new_parent)

    for branch, old_parent, new_parent in branches_to_rebase_sorted:
        typer.echo(f"  • {branch} onto {new_parent}...", nl=False)
        try:
            # Get the old parent's commit SHA before rebasing
            # First try from branches dict, then from metadata (parent_revision)
            if old_parent in branches:
                old_parent_sha = branches[old_parent].commit_sha
            else:
                # Parent was deleted - try to get parent_revision from metadata
                metadata = get_branch_metadata(branch)
                old_parent_sha = metadata.get("parent_revision")

                if not old_parent_sha:
                    # Fallback: use merge-base with new parent
                    old_parent_sha = git.get_merge_base(branch, new_parent)

            if not old_parent_sha:
                typer.echo(" SKIPPED (cannot determine rebase point)")
                continue

            # Rebase: git rebase --onto new_parent old_parent branch
            git.rebase_onto(new_parent, old_parent_sha, branch)

            rebased_branches.append((branch, new_parent))
            typer.echo(" done")
        except GitError as e:
            typer.echo(" CONFLICT")
            print_error(str(e))
            typer.echo("\nResolve the conflicts, then run:")
            typer.echo(f"  {cli} sync --continue")
            typer.echo("\nOr abort with:")
            typer.echo(f"  {cli} sync --abort")
            raise typer.Exit(1) from None

    # Update metadata for rebased branches
    typer.echo("\nUpdating branch parents:")
    for branch, new_parent in rebased_branches:
        # Store local branch name (main), not origin/main
        parent_name = main_branch if new_parent.startswith("origin/") else new_parent
        update_branch_metadata(
            branch,
            parent=parent_name,
            parent_revision=git.get_commit_sha(new_parent),
        )
        typer.echo(f"  • {branch}: parent → {parent_name}")

    # Delete merged branches
    typer.echo("\nCleaning up merged branches:")
    for name in merged_branches:
        try:
            git.delete_branch(name)
            delete_branch_metadata(name)
            typer.echo(f"  • Deleted: {name}")
        except GitError as e:
            print_warning(f"Could not delete {name}: {e}")

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

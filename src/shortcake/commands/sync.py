from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

import httpx
import typer

from shortcake import _git as git
from shortcake._cache import update_pr_cache
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, get_github_token, get_repo_info
from shortcake.commands.restack import RestackResult, _restack, _restore_trailer


class SyncError(ShortcakeError):
    """Error during sync operation."""

    pass


@dataclass
class SyncResult:
    """Result of sync operation."""

    trunk_updated: bool
    trunk_new_sha: str | None = None
    deleted_branches: list[str] = field(default_factory=list)
    closed_branches: list[str] = field(default_factory=list)
    reparented_branches: dict[str, str] = field(default_factory=dict)
    restack_result: RestackResult | None = None


def _topological_sort_for_deletion(repo: Repo, branches: list[str]) -> list[str]:
    """Sort branches so children come before parents (leaves first).

    This ensures we delete leaf branches before their parents.
    """
    result = []
    remaining = set(branches)

    while remaining:
        # Find branches with no children in remaining set
        leaves = []
        for branch in remaining:
            children = git.get_branch_children(repo, branch)
            # Check if any children are in remaining set
            if not any(child in remaining for child in children):
                leaves.append(branch)

        if not leaves:  # pragma: no cover
            # No leaves found - this shouldn't happen with valid data
            # Fall back to arbitrary order
            leaves = [next(iter(remaining))]

        for leaf in sorted(leaves):  # Sort for deterministic order
            result.append(leaf)
            remaining.remove(leaf)

    return result


def _reparent_branch(repo: Repo, child: str, new_parent: str) -> bool:
    """Rebase a branch onto a new parent, updating the Shortcake-Parent trailer.

    Uses git rebase --onto to properly rebase file content onto the new parent,
    then updates the trailer. This ensures the branch's tree is consistent with
    the new parent (unlike the old approach that preserved stale tree snapshots).

    Returns True on success, False on failure (rebase conflict or error).
    """
    from shortcake.commands.reorder import _update_branch_trailer

    all_branches = set(git.get_all_local_branches(repo))
    parent_info = git.get_branch_parent_info(repo, child, all_branches)
    if parent_info is None:
        return True  # Not tracked, nothing to do

    _, merge_base = parent_info
    if merge_base is None:
        return True  # Orphan commit, nothing to do

    # Rebase the branch onto the new parent, properly handling file content.
    # This takes commits from merge_base..child and replays them onto new_parent.
    result = git.rebase_branch(repo, child, new_parent, merge_base.decode())

    if not result.success:
        # Abort the failed rebase so we can continue with other branches
        if git.is_rebase_in_progress(repo):
            git.rebase_abort(repo)
        return False

    # Update the trailer to point to the new parent
    _update_branch_trailer(repo, child, new_parent)

    # If trailer was lost during rebase (--empty=drop), restore it
    all_branches = set(git.get_all_local_branches(repo))
    if git.get_branch_parent(repo, child, all_branches) is None:
        _restore_trailer(repo, child, new_parent)

    return True


def _delete_and_reparent(
    repo: Repo,
    branch: str,
    trunk: str,
    current_branch: str | None,
    skip_branches: set[str],
    result: SyncResult,
) -> str | None:
    """Delete a branch and reparent its children.

    Returns the (possibly updated) current branch name.
    """
    children = git.get_branch_children(repo, branch)
    all_branches = set(git.get_all_local_branches(repo))
    branch_parent = git.get_branch_parent(repo, branch, all_branches)
    grandparent = branch_parent if branch_parent else trunk

    # If grandparent was deleted earlier in this sync loop, fall back to trunk
    if grandparent != trunk and not git.branch_exists(repo, grandparent):
        grandparent = trunk

    if branch == current_branch:
        git.switch_branch(repo, trunk)
        current_branch = trunk

    for child in children:
        if child not in skip_branches:
            success = _reparent_branch(repo, child, grandparent)
            if success:
                result.reparented_branches[child] = grandparent
                typer.echo(f"Reparented {child} to {grandparent}")
            else:
                typer.echo(
                    f"Warning: Could not reparent '{child}' to '{grandparent}' "
                    f"due to conflicts. Run 'sc restack' manually after resolving.",
                    err=True,
                )

    git.delete_branch(repo, branch)
    return current_branch


@dataclass
class _GitHubBranchStatus:
    """Branches detected via GitHub API as needing cleanup."""

    merged: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)


def _detect_github_stale_branches(
    repo: Repo, tracked_branches: list[str], exclude: list[str]
) -> _GitHubBranchStatus:
    """Detect tracked branches with merged or closed PRs on GitHub.

    Checks the GitHub API for branches that have no open PR and have
    either a merged or closed PR. This catches squash-merges and other
    cases that local git detection misses.

    Returns branches grouped by merged vs closed status, excluding
    branches in the exclude list.
    """
    token = get_github_token()
    repo_info = get_repo_info(repo)

    if not token or not repo_info:
        return _GitHubBranchStatus()

    owner, repo_name = repo_info
    exclude_set = set(exclude)
    result = _GitHubBranchStatus()

    try:
        with GitHubClient(token, owner, repo_name) as gh:
            for branch in tracked_branches:
                if branch in exclude_set:
                    continue
                try:
                    pr = gh.get_pr_for_branch(branch)
                    if pr:
                        continue  # Open PR, skip
                    closed_num, is_merged = gh.get_closed_pr_info(branch)
                    if not closed_num:
                        continue
                    pr_url = f"https://github.com/{owner}/{repo_name}/pull/{closed_num}"
                    if is_merged:
                        result.merged.append(branch)
                        update_pr_cache(
                            repo, branch, closed_num, is_merged=True, url=pr_url
                        )
                    else:
                        result.closed.append(branch)
                        update_pr_cache(
                            repo, branch, closed_num, is_closed=True, url=pr_url
                        )
                except Exception:
                    continue
    except Exception:
        pass

    return result


def _resolve_deleted_parent(repo: Repo, parent: str) -> str | None:
    """Check if a deleted parent branch was merged on GitHub.

    Returns the branch it was merged into, or None if not found/not merged.
    """
    token = get_github_token()
    repo_info = get_repo_info(repo)
    if not token or not repo_info:
        return None

    owner, repo_name = repo_info
    try:
        with GitHubClient(token, owner, repo_name) as gh:
            return gh.get_merged_pr_base(parent)
    except (httpx.HTTPStatusError, httpx.RequestError, Exception):
        return None


def _resolve_existing_parent(
    repo: Repo,
    parent: str,
    local_branches: set[str] | None = None,
) -> str | None:
    """Resolve deleted-parent chains until a local branch is found.

    A stacked branch can point at a parent that was merged into another branch
    that was also later deleted locally. Sync needs to follow that chain until
    it lands on an existing local branch, otherwise reparenting will target
    another missing branch and leave the stack orphaned.
    """
    if local_branches is None:
        local_branches = set(git.get_all_local_branches(repo))

    current = parent
    seen: set[str] = set()

    while current not in local_branches:
        if current in seen:
            return None
        seen.add(current)

        merged_target = _resolve_deleted_parent(repo, current)
        if not merged_target:
            return None
        current = merged_target

    return current


def _sync(
    repo: Repo,
    force: bool = False,
    dry_run: bool = False,
    prompt_fn: Callable[[str, str], bool] | None = None,
) -> SyncResult:
    """
    Sync with remote: update trunk, clean up merged branches, restack.

    Args:
        repo: The git repository
        force: Skip delete confirmations
        dry_run: Preview what would happen
        prompt_fn: Function to prompt user (for testing)

    Returns:
        SyncResult with details of what was done
    """
    # Check preconditions
    if git.has_uncommitted_changes(repo):
        raise SyncError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise SyncError("Git rebase in progress. Complete or abort it first.")

    # Get trunk (default branch)
    trunk = git.get_default_branch(repo)
    if trunk is None:
        raise SyncError("Cannot determine default branch (main/master).")

    current_branch = git.get_current_branch(repo)
    result = SyncResult(trunk_updated=False)

    # 1. Fetch and fast-forward trunk
    typer.echo(f"Pulling {trunk} from remote...")
    success, new_sha = git.fetch_and_fast_forward_trunk(repo, trunk)

    if not success:
        typer.echo(f"Warning: Could not fast-forward {trunk} from remote.", err=True)
    elif new_sha:
        result.trunk_updated = True
        result.trunk_new_sha = new_sha
        typer.echo(f"{trunk} fast-forwarded to {new_sha}...")

    # 2. Detect merged branches
    typer.echo("Checking for merged branches...")
    tracked_branches = git.get_tracked_branches(repo)
    merged_branches = [
        b for b in git.get_merged_branches(repo, tracked_branches, trunk) if b != trunk
    ]

    # All branches to skip reparenting into (merged + closed)
    all_removing: set[str] = set(merged_branches)

    if merged_branches:
        # Sort for deletion (leaves first)
        sorted_merged = _topological_sort_for_deletion(repo, merged_branches)

        # 3. Prompt and delete merged branches
        for branch in sorted_merged:
            if dry_run:
                typer.echo(f"Would delete merged branch '{branch}'")
                continue

            should_delete = force
            if not force and prompt_fn:
                should_delete = prompt_fn(branch, trunk)
            elif not force:
                response = typer.prompt(
                    f"'{branch}' is merged into {trunk}. Delete it? [y/n]",
                    default="n",
                )
                should_delete = response.lower() in ("y", "yes")

            if should_delete:
                current_branch = _delete_and_reparent(
                    repo, branch, trunk, current_branch, all_removing, result
                )
                result.deleted_branches.append(branch)
                typer.echo(f"Deleted branch {branch}")

    # 3b. Check GitHub API for merged/closed PRs that local git missed
    github_status = _detect_github_stale_branches(
        repo, tracked_branches, merged_branches
    )
    all_removing.update(github_status.merged)
    all_removing.update(github_status.closed)

    # Handle GitHub-detected merged branches (e.g. squash merges)
    if github_status.merged:
        sorted_gh_merged = _topological_sort_for_deletion(repo, github_status.merged)
        for branch in sorted_gh_merged:
            if dry_run:
                typer.echo(f"Would delete merged branch '{branch}'")
                continue

            should_delete = force
            if not force and prompt_fn:
                should_delete = prompt_fn(branch, trunk)
            elif not force:
                response = typer.prompt(
                    f"'{branch}' is merged into {trunk}. Delete it? [y/n]",
                    default="n",
                )
                should_delete = response.lower() in ("y", "yes")

            if should_delete:
                current_branch = _delete_and_reparent(
                    repo, branch, trunk, current_branch, all_removing, result
                )
                result.deleted_branches.append(branch)
                typer.echo(f"Deleted branch {branch}")

    # Handle closed (not merged) PR branches
    if github_status.closed:
        sorted_closed = _topological_sort_for_deletion(repo, github_status.closed)
        for branch in sorted_closed:
            if dry_run:
                typer.echo(f"Would delete closed branch '{branch}'")
                continue

            should_delete = force
            if not force and prompt_fn:
                should_delete = prompt_fn(branch, "closed")
            elif not force:
                response = typer.prompt(
                    f"'{branch}' has a closed PR. Delete it? [y/n]",
                    default="n",
                )
                should_delete = response.lower() in ("y", "yes")

            if should_delete:
                current_branch = _delete_and_reparent(
                    repo, branch, trunk, current_branch, all_removing, result
                )
                result.closed_branches.append(branch)
                typer.echo(f"Deleted branch {branch}")

    # 3c. Reparent branches whose parent was deleted (merged elsewhere)
    # Re-fetch tracked branches since some may have been deleted above
    remaining_tracked = git.get_tracked_branches(repo)
    all_local = set(git.get_all_local_branches(repo))
    branch_heads = {b: git.get_branch_head(repo, b) for b in all_local}
    trunk_head = branch_heads.get(trunk)
    for branch in remaining_tracked:
        parent = git.get_branch_parent(
            repo, branch, all_local, branch_heads, trunk_head
        )
        if parent and parent not in all_local:
            # Parent doesn't exist locally - check if it was merged on GitHub
            resolved_parent = _resolve_existing_parent(repo, parent, all_local)
            if resolved_parent:
                if dry_run:
                    typer.echo(
                        f"Would reparent '{branch}' from "
                        f"'{parent}' to '{resolved_parent}'"
                    )
                else:
                    success = _reparent_branch(repo, branch, resolved_parent)
                    if success:
                        result.reparented_branches[branch] = resolved_parent
                        typer.echo(
                            f"Reparented '{branch}' to '{resolved_parent}' "
                            f"('{parent}' was merged)"
                        )
                    else:
                        typer.echo(
                            f"Warning: Could not reparent '{branch}' to "
                            f"'{resolved_parent}' due to conflicts. "
                            f"Run 'sc restack' manually after resolving.",
                            err=True,
                        )

    # 4. Restack remaining branches if current is tracked
    current_branch = git.get_current_branch(repo)
    if current_branch and not dry_run:
        all_branches = set(git.get_all_local_branches(repo))
        if git.get_branch_parent(repo, current_branch, all_branches) is not None:
            try:
                restack_result = _restack(repo)
                result.restack_result = restack_result
                if restack_result.restacked_branches:
                    for branch in restack_result.restacked_branches:
                        typer.echo(f"Restacked {branch}.")
            except Exception as e:  # pragma: no cover
                typer.echo(f"Warning: Could not restack: {e}", err=True)

    return result


# Typer command


def sync(
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Auto-confirm branch deletions")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Preview what would happen")
    ] = False,
) -> None:
    """Sync with remote: update trunk, clean up merged branches, restack."""
    repo = git.open_repo()

    try:
        result = _sync(repo, force=yes, dry_run=dry_run)
    except SyncError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.restack_result and result.restack_result.conflict_branch:
        raise typer.Exit(1)

    # Summary
    no_deletions = (
        not result.deleted_branches
        and not result.closed_branches
        and not result.trunk_updated
    )
    no_restacks = (
        not result.restack_result or not result.restack_result.restacked_branches
    )
    if no_deletions and no_restacks:
        typer.echo("Everything up to date.")

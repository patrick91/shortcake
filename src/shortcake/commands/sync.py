import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import httpx
import typer

from shortcake import _git as git
from shortcake._cache import update_pr_cache
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, get_github_token, get_repo_info
from shortcake._output import ShortcakeRichToolkit, get_rich_toolkit
from shortcake.commands._sync_review import (
    CLOSED,
    MERGED,
    SQUASH_MERGED,
    StaleBranch,
    pick_cleanup,
    selected_branches,
)
from shortcake.commands.restack import (
    RestackResult,
    _restack,
    _restore_trailer,
    _trailer_lost,
)


class SyncError(ShortcakeError):
    """Error during sync operation."""

    pass


def _stdin_is_interactive() -> bool:
    """Whether stdin can answer prompts (a real terminal)."""
    return sys.stdin.isatty()


@dataclass
class WorktreeCleanup:
    """Worktree cleanup result for a branch being deleted."""

    branch: str
    path: str
    error: str | None = None


@dataclass
class SyncResult:
    """Result of sync operation."""

    trunk_updated: bool
    trunk_new_sha: str | None = None
    deleted_branches: list[str] = field(default_factory=list)
    closed_branches: list[str] = field(default_factory=list)
    reparented_branches: dict[str, str] = field(default_factory=dict)
    removed_worktrees: list[WorktreeCleanup] = field(default_factory=list)
    skipped_worktrees: list[WorktreeCleanup] = field(default_factory=list)
    restack_result: RestackResult | None = None


@dataclass(frozen=True)
class _DeleteBranchResult:
    """Result of attempting to delete a branch during sync."""

    current_branch: str | None
    deleted: bool


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
    if _trailer_lost(repo, child, new_parent):
        _restore_trailer(repo, child, new_parent)

    return True


def _restore_current_branch(repo: Repo, current_branch: str | None) -> None:
    """Restore the branch sync intends to leave checked out."""
    if current_branch is None:
        return
    if git.get_current_branch(repo) != current_branch:
        git.switch_branch(repo, current_branch, ignore_other_worktrees=True)


def _other_worktrees_for_branch(repo: Repo, branch: str) -> list[Path]:
    """Return non-current worktree paths for branch."""
    current_path = Path(repo.workdir).resolve()
    paths: list[Path] = []
    for path in git.get_branch_worktrees(repo).get(branch, []):
        worktree_path = Path(str(path))
        if worktree_path.resolve() != current_path:
            paths.append(worktree_path)
    return sorted(paths, key=str)  # type: ignore[invalid-return-type]


def _remove_branch_worktrees(repo: Repo, branch: str, result: SyncResult) -> bool:
    """Remove all clean non-current worktrees for branch."""
    all_removed = True
    for path in _other_worktrees_for_branch(repo, branch):
        display_path = git.format_worktree_path(path)
        success, error = git.remove_worktree(repo, path)
        if success:
            result.removed_worktrees.append(WorktreeCleanup(branch, display_path))
            typer.echo(f"Removed worktree {display_path}")
            continue

        all_removed = False
        result.skipped_worktrees.append(WorktreeCleanup(branch, display_path, error))
        typer.echo(
            f"Warning: Could not remove worktree '{display_path}' for branch "
            f"'{branch}': {error}. Branch was not deleted.",
            err=True,
        )
    return all_removed


def _delete_and_reparent(
    repo: Repo,
    branch: str,
    trunk: str,
    current_branch: str | None,
    skip_branches: set[str],
    result: SyncResult,
) -> _DeleteBranchResult:
    """Delete a branch and reparent its children.

    Returns whether deletion happened and the possibly updated current branch name.
    """
    children = git.get_branch_children(repo, branch)
    all_branches = set(git.get_all_local_branches(repo))
    branch_parent = git.get_branch_parent(repo, branch, all_branches)
    grandparent = branch_parent if branch_parent else trunk

    # If grandparent was deleted earlier in this sync loop, fall back to trunk
    if grandparent != trunk and not git.branch_exists(repo, grandparent):
        grandparent = trunk

    if not _remove_branch_worktrees(repo, branch, result):
        return _DeleteBranchResult(current_branch=current_branch, deleted=False)

    if branch == current_branch:
        git.switch_branch(repo, trunk, ignore_other_worktrees=True)
        current_branch = trunk

    failed_children: list[str] = []
    for child in children:
        if child not in skip_branches:
            success = _reparent_branch(repo, child, grandparent)
            if success:
                result.reparented_branches[child] = grandparent
                typer.echo(f"Reparented {child} to {grandparent}")
            else:
                failed_children.append(child)

    if failed_children:
        # Deleting the branch anyway would orphan the children (their
        # trailers would point at a branch that no longer exists), so keep
        # it and let the user resolve the rebase themselves.
        names = ", ".join(f"'{c}'" for c in failed_children)
        typer.echo(
            f"Warning: Keeping '{branch}': could not reparent {names} onto "
            f"'{grandparent}' due to conflicts. "
            f"Run 'sc move <child> -p {grandparent}', resolve the conflicts, "
            f"then re-run 'sc sync'.",
            err=True,
        )
        _restore_current_branch(repo, current_branch)
        return _DeleteBranchResult(current_branch=current_branch, deleted=False)

    _restore_current_branch(repo, current_branch)
    git.delete_branch(repo, branch)
    return _DeleteBranchResult(current_branch=current_branch, deleted=True)


@dataclass
class _GitHubBranchStatus:
    """Branches detected via GitHub API as needing cleanup."""

    merged: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    pr_numbers: dict[str, int] = field(default_factory=dict)
    """Branch -> PR number, so the review can name the PR it came from."""


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
                    result.pr_numbers[branch] = closed_num
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


def _collect_stale(
    repo: Repo, trunk: str, tracked_branches: list[str]
) -> list[StaleBranch]:
    """Every deletion candidate, gathered before anything is asked or deleted.

    The old flow interleaved detection with three separate `[y/n]` loops, so
    you answered about one branch without seeing the others. Gathering first is
    what makes a single review possible.
    """
    local_merged = [
        b for b in git.get_merged_branches(repo, tracked_branches, trunk) if b != trunk
    ]
    github = _detect_github_stale_branches(repo, tracked_branches, local_merged)

    def build(branch: str, reason: str) -> StaleBranch:
        return StaleBranch(
            branch=branch,
            reason=reason,
            pr=github.pr_numbers.get(branch),
            worktrees=[
                git.format_worktree_path(path)
                for path in _other_worktrees_for_branch(repo, branch)
            ],
            # Trustworthy only because fetch prunes; a stale remote-tracking
            # ref would report a deleted branch as still pushed.
            pushed=git.get_remote_ref(repo, f"origin/{branch}") is not None,
        )

    stale = [
        build(b, MERGED) for b in _topological_sort_for_deletion(repo, local_merged)
    ]
    stale += [
        build(b, SQUASH_MERGED)
        for b in _topological_sort_for_deletion(repo, github.merged)
    ]
    stale += [
        build(b, CLOSED) for b in _topological_sort_for_deletion(repo, github.closed)
    ]
    return stale


def _movers_for(repo: Repo, going: list[str]) -> list[str]:
    """Surviving tracked branches whose parent is being deleted."""
    doomed = set(going)
    all_local = set(git.get_all_local_branches(repo))
    branch_heads = {b: git.get_branch_head(repo, b) for b in all_local}
    out = []
    for branch in git.get_tracked_branches(repo):
        if branch in doomed:
            continue
        parent = git.get_branch_parent(repo, branch, all_local, branch_heads)
        if parent in doomed:
            out.append(branch)
    return out


def _ask_cleanup(
    repo: Repo,
    toolkit: ShortcakeRichToolkit,
    stale: list[StaleBranch],
    trunk: str,
    trunk_note: str | None,
) -> list[str]:
    """Run the review and return the branches to delete."""
    repo_info = get_repo_info(repo)
    target = "/".join(repo_info) if repo_info else None
    scope = pick_cleanup(
        toolkit.console,
        stale,
        _movers_for(repo, [s.branch for s in stale]),
        trunk=trunk,
        target=target,
        trunk_note=trunk_note,
    )
    if scope == "cancel":
        raise SyncCancelled
    return selected_branches(stale, scope)


class SyncCancelled(Exception):
    """The user chose Cancel in the review."""


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
    toolkit: ShortcakeRichToolkit | None = None,
) -> SyncResult:
    """
    Sync with remote: update trunk, clean up merged branches, restack.

    Args:
        repo: The git repository
        force: Skip delete confirmations
        dry_run: Preview what would happen
        prompt_fn: Function to prompt user (for testing)
        toolkit: Output toolkit; built if not supplied

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
    toolkit = toolkit or get_rich_toolkit()

    # 1. Fetch and fast-forward trunk
    toolkit.echo(f"Pulling {trunk} from remote...")
    success, new_sha = git.fetch_and_fast_forward_trunk(repo, trunk)

    trunk_note: str | None = None
    if not success:
        toolkit.echo(f"Warning: Could not fast-forward {trunk} from remote.", err=True)
    elif new_sha:
        result.trunk_updated = True
        result.trunk_new_sha = new_sha
        trunk_note = f"fast-forwarded to {new_sha}"
        toolkit.echo(f"{trunk} fast-forwarded to {new_sha}...")

    # 2. Detect merged branches
    # _collect_stale makes one GitHub call per branch, so say something first
    # rather than sitting silent through it.
    toolkit.echo("Checking for merged branches...")
    tracked_branches = git.get_tracked_branches(repo)
    stale = _collect_stale(repo, trunk, tracked_branches)

    # Reparenting skips anything that is going away, whichever scope is chosen.
    all_removing: set[str] = {item.branch for item in stale}

    if dry_run:
        for item in stale:
            toolkit.echo(f"Would delete {item.reason} branch '{item.branch}'")
            for path in item.worktrees:
                toolkit.echo(
                    f"Would remove worktree '{path}' for branch '{item.branch}'"
                )
    elif stale:
        if force:
            going = [item.branch for item in stale]
        elif prompt_fn:
            going = [
                item.branch
                for item in stale
                if prompt_fn(item.branch, trunk if item.reason != CLOSED else "closed")
            ]
        elif not _stdin_is_interactive() or not toolkit.console.is_terminal:
            # A pipe or CI cannot answer, so nothing is deleted and the hint
            # points at --yes rather than hanging on a prompt.
            for item in stale:
                toolkit.echo(
                    f"Keeping '{item.branch}' (non-interactive; use --yes to delete)"
                )
            going = []
        else:
            going = _ask_cleanup(repo, toolkit, stale, trunk, trunk_note)

        # Only what is actually being deleted should be skipped when
        # reparenting; a kept branch is still a valid parent.
        all_removing = set(going)
        for branch in going:
            delete_result = _delete_and_reparent(
                repo, branch, trunk, current_branch, all_removing, result
            )
            current_branch = delete_result.current_branch
            if delete_result.deleted:
                item = next(s for s in stale if s.branch == branch)
                if item.reason == CLOSED:
                    result.closed_branches.append(branch)
                else:
                    result.deleted_branches.append(branch)
                toolkit.echo(f"Deleted branch {branch}")

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
                    toolkit.echo(
                        f"Would reparent '{branch}' from "
                        f"'{parent}' to '{resolved_parent}'"
                    )
                else:
                    success = _reparent_branch(repo, branch, resolved_parent)
                    _restore_current_branch(repo, current_branch)
                    if success:
                        result.reparented_branches[branch] = resolved_parent
                        toolkit.echo(
                            f"Reparented '{branch}' to '{resolved_parent}' "
                            f"('{parent}' was merged)"
                        )
                    else:
                        toolkit.echo(
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
                        toolkit.echo(f"Restacked {branch}.")
            except Exception as e:  # pragma: no cover
                toolkit.echo(f"Warning: Could not restack: {e}", err=True)

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

    toolkit = get_rich_toolkit()
    try:
        result = _sync(repo, force=yes, dry_run=dry_run, toolkit=toolkit)
    except SyncCancelled:
        toolkit.echo("Cancelled · nothing deleted")
        return
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
        and not result.removed_worktrees
        and not result.skipped_worktrees
    )
    no_restacks = (
        not result.restack_result or not result.restack_result.restacked_branches
    )
    if no_deletions and no_restacks:
        typer.echo("Everything up to date.")

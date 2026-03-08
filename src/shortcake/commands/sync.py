from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

import httpx
import typer
from dulwich.objects import Commit
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._cache import update_pr_cache
from shortcake._exceptions import ShortcakeError
from shortcake._github import GitHubClient, get_github_token, get_repo_info
from shortcake._trailers import Trailers
from shortcake.commands.restack import RestackResult, _restack


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


def _replay_commits(repo: Repo, commits: list[bytes], base: bytes) -> bytes:
    """Replay commits on top of a new base, return final SHA."""
    current_base = base
    # Commits are newest-first, so reverse to replay in order
    for commit_sha in reversed(commits):
        old_commit = repo[commit_sha]
        old_message = old_commit.message.decode()
        new_sha = git.amend_commit_message(repo, commit_sha, old_message)
        # Update the parent to point to current_base
        new_commit = repo[new_sha]

        fixed_commit = Commit()
        fixed_commit.tree = old_commit.tree
        fixed_commit.parents = [current_base]
        fixed_commit.author = old_commit.author
        fixed_commit.committer = old_commit.committer
        fixed_commit.author_time = old_commit.author_time
        fixed_commit.author_timezone = old_commit.author_timezone
        fixed_commit.commit_time = new_commit.commit_time
        fixed_commit.commit_timezone = old_commit.commit_timezone
        fixed_commit.encoding = old_commit.encoding
        fixed_commit.message = old_commit.message

        repo.object_store.add_object(fixed_commit)
        current_base = fixed_commit.id

    return current_base


def _reparent_branch(repo: Repo, child: str, new_parent: str) -> None:
    """Update a branch's Shortcake-Parent trailer to point to new parent.

    This rewrites the first commit (the one with the trailer) and replays
    subsequent commits on top of it.
    """
    all_branches = set(git.get_all_local_branches(repo))
    parent_info = git.get_branch_parent_info(repo, child, all_branches)
    if parent_info is None:
        return  # Not tracked, nothing to do

    _, merge_base = parent_info
    if merge_base is None:
        return  # Orphan commit, nothing to do

    # Get the new parent's head as the base for commits
    new_parent_head = git.get_branch_head(repo, new_parent)

    # Get commits on child branch relative to merge base.
    # Use merge_base (parent of the first commit with the trailer) instead
    # of old_parent_head, because the old parent branch may have diverged
    # (e.g., been rebased) since the child was created.
    child_head = git.get_branch_head(repo, child)
    commits = git.get_commits_between(repo, child_head, merge_base)

    if not commits:  # pragma: no cover
        return

    # First commit is last in list (walker returns newest first)
    first_commit_sha = commits[-1]

    # Update trailer in first commit
    message = git.get_commit_message(repo, first_commit_sha)

    # Strip existing trailer and add new one
    # Find where trailer block starts and rebuild message
    lines = message.rstrip().split("\n")
    body_lines = []
    for line in lines:
        if line.startswith("Shortcake-Parent: "):
            continue
        body_lines.append(line)

    # Remove trailing empty lines from body
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    new_message = "\n".join(body_lines)
    new_trailers = Trailers(parent_branch=new_parent)
    new_message = new_trailers.apply_to(new_message)

    new_first_sha = git.amend_commit_message(repo, first_commit_sha, new_message)

    # Fix parent of first commit to point to new_parent_head
    old_first = repo[first_commit_sha]
    fixed_first = Commit()
    fixed_first.tree = old_first.tree
    fixed_first.parents = [new_parent_head]
    fixed_first.author = old_first.author
    fixed_first.committer = old_first.committer
    fixed_first.author_time = old_first.author_time
    fixed_first.author_timezone = old_first.author_timezone
    fixed_first.commit_time = repo[new_first_sha].commit_time
    fixed_first.commit_timezone = old_first.commit_timezone
    fixed_first.encoding = old_first.encoding
    fixed_first.message = new_message.encode()

    repo.object_store.add_object(fixed_first)
    new_first_sha = fixed_first.id

    # Replay remaining commits
    if len(commits) > 1:
        new_head = _replay_commits(repo, commits[:-1], new_first_sha)
    else:
        new_head = new_first_sha

    # Update branch ref
    git.update_branch(repo, child, new_head.decode())


def _update_parent_trailer(repo: Repo, child: str, new_parent: str) -> None:
    """Rewrite only the Shortcake-Parent trailer without changing tree or parents.

    Unlike _reparent_branch which also grafts the commit onto the new parent's
    head (preserving the old tree), this only updates the trailer message. This
    is used when the old parent was deleted and a proper rebase is needed after.
    """
    all_branches = set(git.get_all_local_branches(repo))
    parent_info = git.get_branch_parent_info(repo, child, all_branches)
    if parent_info is None:
        return

    _, merge_base = parent_info
    if merge_base is None:
        return

    child_head = git.get_branch_head(repo, child)
    commits = git.get_commits_between(repo, child_head, merge_base)

    if not commits:  # pragma: no cover
        return

    first_commit_sha = commits[-1]
    message = git.get_commit_message(repo, first_commit_sha)

    lines = message.rstrip().split("\n")
    body_lines = []
    for line in lines:
        if line.startswith("Shortcake-Parent: "):
            continue
        body_lines.append(line)

    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    new_message = "\n".join(body_lines)
    new_trailers = Trailers(parent_branch=new_parent)
    new_message = new_trailers.apply_to(new_message)

    # Rewrite first commit with new message but SAME parent and tree
    old_first = repo[first_commit_sha]
    fixed_first = Commit()
    fixed_first.tree = old_first.tree
    fixed_first.parents = list(old_first.parents)  # Keep original parents
    fixed_first.author = old_first.author
    fixed_first.committer = old_first.committer
    fixed_first.author_time = old_first.author_time
    fixed_first.author_timezone = old_first.author_timezone
    fixed_first.commit_time = old_first.commit_time
    fixed_first.commit_timezone = old_first.commit_timezone
    fixed_first.encoding = old_first.encoding
    fixed_first.message = new_message.encode()

    repo.object_store.add_object(fixed_first)

    # Replay remaining commits on top
    if len(commits) > 1:
        new_head = _replay_commits(repo, commits[:-1], fixed_first.id)
    else:
        new_head = fixed_first.id

    git.update_branch(repo, child, new_head.decode())


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

    if branch == current_branch:
        git.switch_branch(repo, trunk)
        current_branch = trunk

    for child in children:
        if child not in skip_branches:
            _reparent_branch(repo, child, grandparent)
            result.reparented_branches[child] = grandparent
            typer.echo(f"Reparented {child} to {grandparent}")

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
    merged_branches = git.get_merged_branches(repo, tracked_branches, trunk)

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
    for branch in remaining_tracked:
        parent = git.get_branch_parent(repo, branch, all_local)
        if parent and parent not in all_local:
            # Parent doesn't exist locally - check if it was merged on GitHub
            merged_target = _resolve_deleted_parent(repo, parent)
            if merged_target:
                if dry_run:
                    typer.echo(
                        f"Would reparent '{branch}' from "
                        f"'{parent}' to '{merged_target}'"
                    )
                else:
                    # Only update the trailer - don't graft the tree.
                    # The subsequent restack step will do a proper rebase.
                    _update_parent_trailer(repo, branch, merged_target)
                    result.reparented_branches[branch] = merged_target
                    typer.echo(
                        f"Reparented '{branch}' to '{merged_target}' "
                        f"('{parent}' was merged)"
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

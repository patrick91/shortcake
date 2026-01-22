import subprocess
from dataclasses import dataclass
from typing import Annotated

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep


class RestackError(ShortcakeError):
    """Error during restack operation."""

    pass


@dataclass
class RestackResult:
    """Result of restack operation."""

    restacked_branches: list[str]
    conflict_branch: str | None = None


def _needs_restack(repo: Repo, branch: str, parent: str) -> bool:
    """Check if branch needs to be rebased onto parent.

    Returns True if parent has commits that are not in branch.
    """
    branch_head = git.get_branch_head(repo, branch)
    parent_head = git.get_branch_head(repo, parent)
    merge_base = git.get_merge_base(repo, branch_head, parent_head)
    return merge_base != parent_head


def _get_stack_in_order(repo: Repo, start: str) -> list[str]:
    """Get tracked branches in topological order (parents before children).

    Starting from the given branch, walks up to find the stack root,
    then returns all tracked branches in BFS order. The root (trunk) branch
    is not included since it's untracked.
    """
    all_branches = set(git.get_all_local_branches(repo))

    # Walk up to find root (trunk or untracked branch)
    root = start
    trunk: str | None = None
    while True:
        parent = git.get_branch_parent(repo, root, all_branches)
        if parent is None:
            # root is untracked, it's the trunk
            trunk = root
            break
        if parent not in all_branches:
            # parent exists but not as a local branch
            trunk = None
            break
        root = parent

    # If root is the trunk (untracked), start from its children
    if trunk is not None:
        # Get children of trunk as starting points
        children = git.get_branch_children(repo, trunk)
        queue = children[:]
    else:
        queue = [root]

    # BFS to get topological order
    order = []
    visited: set[str] = set()

    while queue:
        branch = queue.pop(0)
        if branch in visited:
            continue
        visited.add(branch)
        order.append(branch)
        children = git.get_branch_children(repo, branch)
        queue.extend(children)

    return order


def _plan_restack(repo: Repo, branches: list[str]) -> list[RestackStep]:
    """Build restack plan for branches that need it.

    Returns list of RestackStep in the order they should be executed.
    When a branch needs rebasing, all its descendants also need rebasing
    (because their parent will move).
    """
    all_branches = set(git.get_all_local_branches(repo))
    plan = []
    needs_restack_set: set[str] = set()

    for branch in branches:
        parent = git.get_branch_parent(repo, branch, all_branches)
        if parent is None:
            continue

        if not git.branch_exists(repo, parent):
            continue

        # A branch needs rebasing if:
        # 1. Its parent has diverged (merge_base != parent_head)
        # 2. Its parent is in the needs_restack set (will move)
        if _needs_restack(repo, branch, parent) or parent in needs_restack_set:
            branch_head = git.get_branch_head(repo, branch)
            parent_head = git.get_branch_head(repo, parent)
            merge_base = git.get_merge_base(repo, branch_head, parent_head)
            if merge_base is not None:
                plan.append(
                    RestackStep(
                        branch=branch,
                        onto=parent,
                        # dulwich returns SHA as 40 ASCII hex bytes, decode to string
                        merge_base=merge_base.decode(),
                    )
                )
                needs_restack_set.add(branch)

    return plan


def _rebase_branch(repo_path: str, branch: str, onto: str, merge_base: str) -> bool:
    """Rebase branch onto target. Returns True if successful, False if conflict."""
    result = subprocess.run(
        ["git", "rebase", "--onto", onto, merge_base, branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _get_conflict_files(repo_path: str) -> list[str]:
    """Get list of files with conflicts."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().split("\n") if result.stdout.strip() else []


def _show_conflict_message(branch: str, onto: str, conflict_files: list[str]) -> None:
    """Display conflict resolution instructions."""
    typer.echo(f"\nConflict while rebasing '{branch}' onto '{onto}'.\n")

    if conflict_files:
        typer.echo("Fix conflicts in the following files:")
        for f in conflict_files:
            typer.echo(f"  {f}")
        typer.echo()

    typer.echo("Then:")
    typer.echo("  1. Stage your changes:     git add <files>")
    typer.echo("  2. Continue the restack:   sc continue")
    typer.echo()
    typer.echo("Or abort with: sc abort")


def _check_remote_divergence(repo: Repo, branches: list[str]) -> list[str]:
    """Return branches that have diverged from their remote."""
    diverged = []
    for branch in branches:
        remote_ref = f"origin/{branch}"
        remote_sha = git.get_remote_ref(repo, remote_ref)
        if remote_sha is None:
            continue

        local_sha = git.get_branch_head(repo, branch)
        # Check if diverged: not equal and not simply behind (fast-forward)
        if local_sha != remote_sha and not git.is_ancestor(repo, local_sha, remote_sha):
            diverged.append(branch)
    return diverged


def _fetch_remote(repo_path: str) -> bool:
    """Fetch from origin. Returns True if successful."""
    result = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _fast_forward_branch(repo_path: str, branch: str) -> bool:
    """Fast-forward branch to origin. Returns True if successful."""
    result = subprocess.run(
        ["git", "fetch", "origin", f"{branch}:{branch}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _restack(repo: Repo, dry_run: bool = False, sync: bool = False) -> RestackResult:
    """
    Restack current branch's stack.

    Raises RestackError on failure, returns RestackResult on success.
    """
    repo_path = repo.path

    # Check preconditions
    current_branch = git.get_current_branch(repo)
    if current_branch is None:
        raise RestackError("Cannot restack in detached HEAD state")

    if git.has_uncommitted_changes(repo):
        raise RestackError("You have uncommitted changes. Commit or stash them first.")

    if RestackState.exists(repo):
        raise RestackError(
            "Restack already in progress. Use 'sc continue' or 'sc abort'."
        )

    if git.is_rebase_in_progress(repo):
        raise RestackError("Git rebase in progress. Complete or abort it first.")

    # Optional sync with remote
    if sync:
        typer.echo("Fetching from origin...")
        _fetch_remote(repo_path)

    # Get stack in topological order
    stack_branches = _get_stack_in_order(repo, current_branch)

    # Check for divergence
    diverged = _check_remote_divergence(repo, stack_branches)
    if diverged:
        typer.echo(
            f"Warning: Branches diverged from remote: {', '.join(diverged)}", err=True
        )
        typer.echo("Run 'git pull --rebase' on each diverged branch first.", err=True)
        typer.echo(
            "Or use 'sc restack --sync' to auto-fetch and fast-forward.", err=True
        )
        raise RestackError("Cannot restack with diverged branches")

    # Build restack plan
    plan = _plan_restack(repo, stack_branches)

    if not plan:
        return RestackResult(restacked_branches=[])

    # Dry run - just show plan
    if dry_run:
        typer.echo(f"Would restack {len(plan)} branch(es):")
        for step in plan:
            typer.echo(f"  {step.branch} onto {step.onto}")
        return RestackResult(restacked_branches=[])

    # Save original refs for rollback
    original_refs = {}
    for step in plan:
        # dulwich returns SHA as 40 ASCII hex bytes, decode to string
        original_refs[step.branch] = git.get_branch_head(repo, step.branch).decode()

    # Save initial state
    state = RestackState(
        version=STATE_VERSION,
        original_branch=current_branch,
        plan=plan,
        current_index=0,
        original_refs=original_refs,
    )
    state.save(repo)

    # Execute restack
    restacked = []
    for i, step in enumerate(plan):
        state.current_index = i
        state.save(repo)

        typer.echo(f"Rebasing '{step.branch}' onto '{step.onto}'...")
        success = _rebase_branch(repo_path, step.branch, step.onto, step.merge_base)

        if not success:
            conflict_files = _get_conflict_files(repo_path)
            _show_conflict_message(step.branch, step.onto, conflict_files)
            return RestackResult(
                restacked_branches=restacked, conflict_branch=step.branch
            )

        restacked.append(step.branch)

    # Success - clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, current_branch)

    return RestackResult(restacked_branches=restacked)


# Typer command


def restack(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Preview what would happen")
    ] = False,
    sync: Annotated[
        bool, typer.Option("--sync", "-s", help="Fetch and fast-forward first")
    ] = False,
) -> None:
    """Restack current branch's stack."""
    repo = git.open_repo()

    try:
        result = _restack(repo, dry_run=dry_run, sync=sync)
    except RestackError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.conflict_branch:
        raise typer.Exit(1)

    if not result.restacked_branches:
        if not dry_run:
            typer.echo("Everything up to date.")
    else:
        if not dry_run:
            typer.echo(
                f"Restacked {len(result.restacked_branches)} branch(es) successfully."
            )

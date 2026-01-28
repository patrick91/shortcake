from dataclasses import dataclass
from pathlib import Path
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
    current_branch_untracked: bool = False
    skipped_empty_commits: bool = False


def _needs_restack(repo: Repo, branch: str, parent: str) -> bool:
    """Check if branch needs to be rebased onto parent.

    Returns True if parent has commits that are not in branch.
    """
    branch_head = git.get_branch_head(repo, branch)
    parent_head = git.get_branch_head(repo, parent)
    merge_base = git.get_merge_base(repo, branch_head, parent_head)
    return merge_base != parent_head


def _get_stack_in_order(repo: Repo, start: str) -> list[str]:
    """Get tracked branches in the current stack in topological order.

    Starting from the given branch, walks up to find the stack root (first
    tracked branch whose parent is untracked/trunk), then returns all branches
    in that stack via BFS. Only includes branches in the same stack as start,
    not sibling stacks under the same trunk.
    """
    all_branches = set(git.get_all_local_branches(repo))

    # Check if start itself is untracked (trunk)
    if git.get_branch_parent(repo, start, all_branches) is None:
        return []

    # Walk up to find stack root (first tracked branch whose parent is untracked)
    stack_root = start
    while True:
        parent = git.get_branch_parent(repo, stack_root, all_branches)
        if parent is None:  # pragma: no cover
            # stack_root's parent has no trailer - stack_root is the stack root
            # Note: This is defensive code. If we reach here, it means the
            # parent changed between the check at line 51 and now.
            break
        if parent not in all_branches:
            # parent exists in trailer but not as a local branch
            # stack_root is the root of our stack
            break
        # Check if parent is the trunk (has no parent trailer itself)
        parent_parent = git.get_branch_parent(repo, parent, all_branches)
        if parent_parent is None:
            # parent is trunk, so stack_root is the stack root
            break
        # Parent is tracked, continue walking up
        stack_root = parent

    # BFS from stack_root to get topological order
    order = []
    visited: set[str] = set()
    queue = [stack_root]

    while queue:
        branch = queue.pop(0)
        if branch in visited:  # pragma: no cover
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
        # Get parent info which includes the correct merge base
        # (parent of the first commit with Shortcake-Parent trailer)
        parent_info = git.get_branch_parent_info(repo, branch, all_branches)
        if parent_info is None:
            continue

        parent, merge_base = parent_info

        if not git.branch_exists(repo, parent):
            continue

        # Check for orphan commits (no common history with parent)
        if merge_base is None:
            raise RestackError(
                f"Cannot restack '{branch}': no common history with parent "
                f"'{parent}'. The branches may have unrelated histories."
            )

        # A branch needs rebasing if:
        # 1. Its parent has diverged (merge_base != parent_head)
        # 2. Its parent is in the needs_restack set (will move)
        if _needs_restack(repo, branch, parent) or parent in needs_restack_set:
            plan.append(
                RestackStep(
                    branch=branch,
                    onto=parent,
                    # merge_base is the parent of the first commit with trailer
                    merge_base=merge_base.decode(),
                )
            )
            needs_restack_set.add(branch)

    return plan


def _rebase_branch(
    repo: Repo, branch: str, onto: str, merge_base: str
) -> git.RebaseResult:
    """Rebase branch onto target."""
    return git.rebase_branch(repo, branch, onto, merge_base)


def _get_conflict_files(repo: Repo | str) -> list[str]:
    """Get list of files with conflicts."""
    try:
        if isinstance(repo, Repo):
            return git.get_conflict_files(repo)
        return git.get_conflict_files(git.open_repo(Path(repo)))
    except (*git.DULWICH_IO_ERRORS, ValueError):
        return []


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


def _show_rebase_error(branch: str, onto: str, error_output: str) -> None:
    """Display rebase error message (non-conflict failure)."""
    typer.echo(f"\nFailed to rebase '{branch}' onto '{onto}'.\n", err=True)
    if error_output:
        typer.echo("Git error:", err=True)
        for line in error_output.strip().split("\n"):
            typer.echo(f"  {line}", err=True)
        typer.echo()
    typer.echo("Abort with: sc abort", err=True)


def _restack(repo: Repo, dry_run: bool = False) -> RestackResult:
    """
    Restack current branch's stack.

    Raises RestackError on failure, returns RestackResult on success.
    """
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

    # Check if current branch is tracked (has Shortcake-Parent trailer)
    all_branches = set(git.get_all_local_branches(repo))
    current_branch_parent = git.get_branch_parent(repo, current_branch, all_branches)
    is_current_untracked = current_branch_parent is None

    # Get stack in topological order
    stack_branches = _get_stack_in_order(repo, current_branch)

    # Build restack plan
    plan = _plan_restack(repo, stack_branches)

    if not plan:
        return RestackResult(
            restacked_branches=[], current_branch_untracked=is_current_untracked
        )

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
    any_skipped_empty = False
    for i, step in enumerate(plan):
        state.current_index = i
        state.save(repo)

        typer.echo(f"Rebasing '{step.branch}' onto '{step.onto}'...")
        result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)

        if not result.success:
            # Check if this is a conflict or other error
            if git.is_rebase_in_progress(repo):
                conflict_files = _get_conflict_files(repo)
                _show_conflict_message(step.branch, step.onto, conflict_files)
            else:
                _show_rebase_error(step.branch, step.onto, result.error_output)
            return RestackResult(
                restacked_branches=restacked, conflict_branch=step.branch
            )

        if result.skipped_empty:
            typer.echo(f"  Skipped empty commit (changes already in '{step.onto}')")
            any_skipped_empty = True

        restacked.append(step.branch)

    # Success - clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, current_branch, force=True)

    return RestackResult(
        restacked_branches=restacked, skipped_empty_commits=any_skipped_empty
    )


# Typer command


def restack(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Preview what would happen")
    ] = False,
) -> None:
    """Restack current branch's stack."""
    repo = git.open_repo()

    try:
        result = _restack(repo, dry_run=dry_run)
    except RestackError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.conflict_branch:
        raise typer.Exit(1)

    if not result.restacked_branches:
        if not dry_run:
            if result.current_branch_untracked:
                typer.echo(
                    "Current branch is not tracked (no Shortcake-Parent trailer). "
                    "Nothing to restack."
                )
            else:
                typer.echo("Everything up to date.")
    else:
        if not dry_run:
            typer.echo(
                f"Restacked {len(result.restacked_branches)} branch(es) successfully."
            )

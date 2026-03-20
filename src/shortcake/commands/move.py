"""Move a tracked branch to a new parent."""

from dataclasses import dataclass, field
from typing import Annotated

import httpx
import typer

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, get_github_token, get_repo_info
from shortcake._pr_stack import _sync_pr_descriptions_for_branches
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake.commands.reorder import _update_branch_trailer
from shortcake.commands.restack import (
    _get_conflict_files,
    _rebase_branch,
    _restore_trailer,
    _show_conflict_message,
    _show_rebase_error,
)


class MoveError(ShortcakeError):
    """Error during move operation."""

    pass


@dataclass
class MoveResult:
    """Result of move operation."""

    branch: str
    old_parent: str
    new_parent: str
    restacked_children: list[str] = field(default_factory=list)
    conflict_branch: str | None = None


def _sync_prs_after_move(
    repo: Repo,
    branch: str,
    old_parent: str,
    new_parent: str,
) -> None:
    """Best-effort PR base/body sync after a successful move."""
    if not git.has_remote(repo, "origin"):
        return

    token = get_github_token()
    if not token:
        return

    repo_info = get_repo_info(repo)
    if not repo_info:
        return

    owner, repo_name = repo_info

    try:
        with GitHubClient(token, owner, repo_name) as gh:
            _sync_pr_descriptions_for_branches(
                repo,
                gh,
                owner,
                [old_parent, branch, new_parent],
                sync_bases=True,
            )
    except (httpx.HTTPStatusError, httpx.RequestError):
        # Keep move as a local-first operation even if GitHub sync fails.
        pass


def _move(
    repo: Repo,
    branch: str | None = None,
    parent: str | None = None,
) -> MoveResult:
    """
    Move a tracked branch to a new parent.

    Rebases the branch onto the new parent, updates the trailer,
    and restacks any children.

    Args:
        repo: The git repository.
        branch: Branch to move (default: current branch).
        parent: New parent branch (required).

    Raises MoveError on failure, returns MoveResult on success.
    """
    # Validate preconditions
    current_branch = git.get_current_branch(repo)
    if current_branch is None:
        raise MoveError("Cannot move in detached HEAD state")

    if git.has_uncommitted_changes(repo):
        raise MoveError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise MoveError("Git rebase in progress. Complete or abort it first.")

    if RestackState.exists(repo):
        raise MoveError("Restack already in progress. Use 'sc continue' or 'sc abort'.")

    if parent is None:
        raise MoveError("--parent is required")

    # Resolve branch
    if branch is None:
        branch = current_branch

    # Validate branch is tracked
    all_branches = set(git.get_all_local_branches(repo))
    parent_info = git.get_branch_parent_info(repo, branch, all_branches)
    if parent_info is None:
        raise MoveError(f"Branch '{branch}' is not tracked by Shortcake")

    old_parent, merge_base = parent_info

    # Validate new parent exists
    if not git.branch_exists(repo, parent):
        raise MoveError(f"Parent branch '{parent}' not found")

    # Can't move onto self
    if parent == branch:
        raise MoveError(f"Cannot move '{branch}' onto itself")

    # No-op if same parent
    if parent == old_parent:
        return MoveResult(branch=branch, old_parent=old_parent, new_parent=parent)

    # Check for circular dependency: new parent must not be a descendant of branch
    branch_head = git.get_branch_head(repo, branch)
    parent_head = git.get_branch_head(repo, parent)
    if git.is_ancestor(repo, branch_head, parent_head):
        raise MoveError(
            f"Cannot move '{branch}' onto '{parent}': "
            f"'{parent}' is a descendant of '{branch}' (would create a cycle)"
        )

    # Validate merge_base
    if merge_base is None:  # pragma: no cover
        raise MoveError(
            f"Cannot move '{branch}': no common history with parent '{old_parent}'"
        )

    # Get children of branch (before modifications)
    children = git.get_branch_children(repo, branch)

    # Build plan
    plan: list[RestackStep] = []

    # Step 1: rebase branch onto new parent
    plan.append(
        RestackStep(
            branch=branch,
            onto=parent,
            merge_base=merge_base.decode(),
            new_parent_trailer=parent,
        )
    )

    # Steps 2+: rebase each child onto branch (their parent ref doesn't change,
    # but they need rebasing because the branch they sit on has moved)
    for child in children:
        child_info = git.get_branch_parent_info(repo, child, all_branches)
        if child_info is None:
            continue  # pragma: no cover
        _, child_merge_base = child_info
        if child_merge_base is None:
            continue  # pragma: no cover
        plan.append(
            RestackStep(
                branch=child,
                onto=branch,
                merge_base=child_merge_base.decode(),
            )
        )

    # Save original refs for rollback
    original_refs: dict[str, str] = {}
    for step in plan:
        original_refs[step.branch] = git.get_branch_head(repo, step.branch).decode()

    # Save state for conflict recovery
    state = RestackState(
        version=STATE_VERSION,
        original_branch=current_branch,
        plan=plan,
        current_index=0,
        original_refs=original_refs,
    )
    state.save(repo)

    # Execute plan
    restacked_children: list[str] = []
    for i, step in enumerate(plan):
        state.current_index = i
        state.save(repo)

        typer.echo(f"Rebasing '{step.branch}' onto '{step.onto}'...")
        result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)

        if not result.success:
            if git.is_rebase_in_progress(repo):
                conflict_files = _get_conflict_files(repo)
                _show_conflict_message(step.branch, step.onto, conflict_files)
            else:  # pragma: no cover
                _show_rebase_error(step.branch, step.onto, result.error_output)
            return MoveResult(
                branch=branch,
                old_parent=old_parent,
                new_parent=parent,
                restacked_children=restacked_children,
                conflict_branch=step.branch,
            )

        # Update trailer if needed (only the moved branch needs trailer update)
        if step.new_parent_trailer is not None:
            _update_branch_trailer(repo, step.branch, step.new_parent_trailer)

        # Check if trailer survived the rebase (--empty=drop may have dropped it)
        all_branches = set(git.get_all_local_branches(repo))
        if (
            git.get_branch_parent(repo, step.branch, all_branches) is None
        ):  # pragma: no cover
            _restore_trailer(repo, step.branch, step.onto)

        if i > 0:
            restacked_children.append(step.branch)

    # Success - clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, current_branch, force=True)

    _sync_prs_after_move(repo, branch, old_parent, parent)

    return MoveResult(
        branch=branch,
        old_parent=old_parent,
        new_parent=parent,
        restacked_children=restacked_children,
    )


# Typer command


def move(
    branch: Annotated[str | None, typer.Argument()] = None,
    parent: Annotated[
        str | None,
        typer.Option("--parent", "-p", help="New parent branch"),
    ] = None,
) -> None:
    """Move a tracked branch to a new parent."""
    repo = git.open_repo()

    try:
        result = _move(repo, branch, parent)
    except MoveError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.conflict_branch:
        raise typer.Exit(1)

    if result.old_parent == result.new_parent:
        typer.echo(
            f"Branch '{result.branch}' already has parent '{result.new_parent}'. "
            f"Nothing to do."
        )
    else:
        typer.echo(
            f"Moved '{result.branch}' from '{result.old_parent}' "
            f"to '{result.new_parent}'."
        )
        if result.restacked_children:
            typer.echo(f"Restacked {len(result.restacked_children)} child branch(es).")

"""Reorder branches within a stack."""

from dataclasses import dataclass, field
from typing import Annotated

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._editor import open_editor
from shortcake._exceptions import ShortcakeError
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake._trailers import Trailers
from shortcake.commands.adopt import _replay_commits
from shortcake.commands.restack import (
    _get_conflict_files,
    _rebase_branch,
    _show_conflict_message,
    _show_rebase_error,
)


class ReorderError(ShortcakeError):
    """Error during reorder operation."""

    pass


@dataclass
class ReorderResult:
    """Result of reorder operation."""

    reordered_branches: list[str] = field(default_factory=list)
    conflict_branch: str | None = None


def _get_linear_stack(repo: Repo, current_branch: str) -> tuple[str, list[str]]:
    """Get the full linear stack containing current_branch.

    Returns (trunk, [branch_bottom, ..., branch_top]).
    Raises ReorderError if the stack has forks (multiple children).
    """
    all_branches = set(git.get_all_local_branches(repo))

    # Check if current branch is tracked
    if git.get_branch_parent(repo, current_branch, all_branches) is None:
        raise ReorderError(f"Branch '{current_branch}' is not tracked by Shortcake")

    # Walk UP from current to find trunk
    trunk = None
    branch = current_branch
    stack_up: list[str] = [branch]
    while True:
        parent = git.get_branch_parent(repo, branch, all_branches)
        if parent is None:
            # branch has no parent trailer - shouldn't happen since we checked above
            raise ReorderError(
                f"Branch '{branch}' is not tracked by Shortcake"
            )  # pragma: no cover
        if parent not in all_branches:  # pragma: no cover
            # parent doesn't exist as local branch
            trunk = parent
            break
        parent_parent = git.get_branch_parent(repo, parent, all_branches)
        if parent_parent is None:
            # parent is untracked (trunk)
            trunk = parent
            break
        # parent is tracked, continue walking up
        stack_up.append(parent)
        branch = parent

    # stack_up is [current, ..., root] - reverse to get [root, ..., current]
    stack_up.reverse()

    # Check for forks in the upward path: each branch (and trunk) should have
    # at most one child on the path. Check that branches walked through don't
    # have multiple children.
    for b in stack_up:
        children = git.get_branch_children(repo, b)
        if len(children) > 1:
            raise ReorderError(
                f"Branch '{b}' has multiple children ({', '.join(children)}). "
                f"Reorder only works on linear stacks without forks."
            )

    # Walk DOWN from current to find leaf
    branch = current_branch
    stack_down: list[str] = []
    while True:
        children = git.get_branch_children(repo, branch)
        if not children:
            break
        if len(children) > 1:
            raise ReorderError(
                f"Branch '{branch}' has multiple children ({', '.join(children)}). "
                f"Reorder only works on linear stacks without forks."
            )
        child = children[0]
        stack_down.append(child)
        branch = child

    # Combine: [root, ..., current, ..., leaf]
    full_stack = stack_up + stack_down

    if len(full_stack) < 2:
        raise ReorderError("Stack has only one branch. Nothing to reorder.")

    return trunk, full_stack


def _update_branch_trailer(repo: Repo, branch: str, new_parent: str) -> None:
    """Update the Shortcake-Parent trailer on a branch after it has been rebased.

    The branch is already sitting on new_parent's head, so we use the new_parent
    head as the base for get_commits_between.
    """
    branch_head = git.get_branch_head(repo, branch)
    new_parent_head = git.get_branch_head(repo, new_parent)

    # Get all commits on this branch (newest-first)
    commits = git.get_commits_between(repo, branch_head, new_parent_head)
    if not commits:
        return  # pragma: no cover

    # The oldest commit (last in list) has the trailer
    first_commit_sha = commits[-1]
    first_commit_message = git.get_commit_message(repo, first_commit_sha)

    # Remove old trailer and add new one
    trailers = Trailers.from_message(first_commit_message)
    clean_message = trailers.remove_from(first_commit_message)
    new_trailers = Trailers(parent_branch=new_parent)
    new_message = new_trailers.apply_to(clean_message)

    # Amend the first commit's message
    new_first_sha = git.amend_commit_message(repo, first_commit_sha, new_message)

    # Replay any commits above it
    if len(commits) > 1:
        new_head = _replay_commits(repo, commits[:-1], new_first_sha)
    else:
        new_head = new_first_sha

    # Update branch ref
    git.update_branch(repo, branch, new_head.decode())


def _build_editor_content(trunk: str, branches: list[str]) -> str:
    """Generate editor file content with branch names and instructions."""
    lines = []
    for branch in branches:
        lines.append(branch)
    lines.append("")
    lines.append(
        f"# Reorder the branches above (bottom-to-top, closest to '{trunk}' first)."
    )
    lines.append("# Lines starting with '#' are ignored.")
    lines.append("# Delete all lines or save an empty file to abort.")
    return "\n".join(lines)


def _parse_editor_result(content: str, original_branches: list[str]) -> list[str]:
    """Parse editor output and validate the result.

    Raises ReorderError if the result is invalid.
    """
    # Filter out comment lines and empty lines
    lines = [
        line.strip()
        for line in content.strip().split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]

    if not lines:
        raise ReorderError("Aborted: empty result from editor.")

    original_set = set(original_branches)
    seen: set[str] = set()

    for branch in lines:
        if branch not in original_set:
            raise ReorderError(
                f"Unknown branch '{branch}'. "
                f"Only branches in the current stack can be used."
            )
        if branch in seen:
            raise ReorderError(f"Duplicate branch '{branch}'.")
        seen.add(branch)

    missing = original_set - seen
    if missing:
        raise ReorderError(
            f"Missing branch(es): {', '.join(sorted(missing))}. "
            f"All branches must be included."
        )

    return lines


def _reorder(repo: Repo, new_order: list[str] | None = None) -> ReorderResult:
    """Reorder branches in the current stack.

    Args:
        repo: The git repository.
        new_order: Desired order (bottom-to-top). If None, opens editor.

    Raises ReorderError on failure, returns ReorderResult on success.
    """
    # Preconditions
    current_branch = git.get_current_branch(repo)
    if current_branch is None:
        raise ReorderError("Cannot reorder in detached HEAD state")

    if git.has_uncommitted_changes(repo):
        raise ReorderError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise ReorderError("Git rebase in progress. Complete or abort it first.")

    if RestackState.exists(repo):
        raise ReorderError(
            "Restack already in progress. Use 'sc continue' or 'sc abort'."
        )

    # Get the full linear stack
    trunk, current_order = _get_linear_stack(repo, current_branch)

    # Interactive editor mode
    if new_order is None:
        content = _build_editor_content(trunk, current_order)
        result = open_editor(content)
        if result is None:
            raise ReorderError("Aborted: editor returned no content.")
        new_order = _parse_editor_result(result, current_order)

    # Validate new_order
    if set(new_order) != set(current_order):
        unknown = set(new_order) - set(current_order)
        missing = set(current_order) - set(new_order)
        parts = []
        if unknown:
            parts.append(f"unknown: {', '.join(sorted(unknown))}")
        if missing:
            parts.append(f"missing: {', '.join(sorted(missing))}")
        raise ReorderError(
            f"Invalid reorder: {'; '.join(parts)}. "
            f"Must be a permutation of the current stack."
        )

    if len(new_order) != len(set(new_order)):  # pragma: no cover
        raise ReorderError("Duplicate branches in the new order.")

    # No-op check
    if new_order == current_order:
        return ReorderResult(reordered_branches=[])

    # Pre-compute merge bases for ALL branches
    all_branches = set(git.get_all_local_branches(repo))
    merge_bases: dict[str, str] = {}
    for branch in current_order:
        parent_info = git.get_branch_parent_info(repo, branch, all_branches)
        if parent_info is None:
            raise ReorderError(
                f"Branch '{branch}' has no parent info"
            )  # pragma: no cover
        _, merge_base = parent_info
        if merge_base is None:
            raise ReorderError(
                f"Branch '{branch}' has no merge base (orphan commit)"
            )  # pragma: no cover
        merge_bases[branch] = merge_base.decode()

    # Save original refs for rollback
    original_refs: dict[str, str] = {}
    for branch in current_order:
        original_refs[branch] = git.get_branch_head(repo, branch).decode()

    # Build plan: for each branch in new_order, determine new parent
    # Include all branches whose parent changed + all branches after first change
    plan: list[RestackStep] = []
    first_changed = None
    for i, branch in enumerate(new_order):
        new_parent = trunk if i == 0 else new_order[i - 1]
        old_parent_idx = current_order.index(branch)
        old_parent = trunk if old_parent_idx == 0 else current_order[old_parent_idx - 1]

        if new_parent != old_parent or first_changed is not None:
            if first_changed is None:
                first_changed = i
            plan.append(
                RestackStep(
                    branch=branch,
                    onto=new_parent,
                    merge_base=merge_bases[branch],
                    new_parent_trailer=new_parent,
                )
            )

    if not plan:  # pragma: no cover
        return ReorderResult(reordered_branches=[])

    # Save state for conflict recovery
    state = RestackState(
        version=STATE_VERSION,
        original_branch=current_branch,
        plan=plan,
        current_index=0,
        original_refs=original_refs,
    )
    state.save(repo)

    # Execute plan bottom-to-top
    reordered: list[str] = []
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
            return ReorderResult(
                reordered_branches=reordered, conflict_branch=step.branch
            )

        # Update the trailer
        _update_branch_trailer(repo, step.branch, step.new_parent_trailer)

        reordered.append(step.branch)

    # Success - clean up state
    state.delete(repo)

    # Return to original branch
    git.switch_branch(repo, current_branch, force=True)

    return ReorderResult(reordered_branches=reordered)


# Typer command


def reorder(
    order: Annotated[
        list[str] | None,
        typer.Argument(
            help="New branch order (bottom-to-top). Omit for interactive editor."
        ),
    ] = None,
) -> None:
    """Reorder branches in the current stack."""
    repo = git.open_repo()

    try:
        result = _reorder(repo, new_order=order if order else None)
    except ReorderError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if result.conflict_branch:
        raise typer.Exit(1)

    if not result.reordered_branches:
        typer.echo("Stack is already in the requested order.")
    else:
        typer.echo(
            f"Reordered {len(result.reordered_branches)} branch(es) successfully."
        )

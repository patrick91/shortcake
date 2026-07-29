from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from rich.style import Style
from rich.text import Text

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._output import ShortcakeRichToolkit, get_rich_toolkit
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake._stack_view import DIM, RowState, StackRow
from shortcake._trailers import Trailers


class RestackError(ShortcakeError):
    """Error during restack operation."""

    pass


@dataclass
class RestackResult:
    """Result of restack operation."""

    restacked_branches: list[str]
    conflict_branch: str | None = None
    planned: list[tuple[str, str]] = field(default_factory=list)
    orphaned_parent: str | None = None
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

    # Precompute branch heads for efficient parent lookups
    branch_heads = {b: git.get_branch_head(repo, b) for b in all_branches}

    # Check if start itself is untracked (trunk)
    if git.get_branch_parent(repo, start, all_branches, branch_heads) is None:
        return []

    # Walk up to find stack root (first tracked branch whose parent is untracked)
    stack_root = start
    while True:
        parent = git.get_branch_parent(repo, stack_root, all_branches, branch_heads)
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
        parent_parent = git.get_branch_parent(repo, parent, all_branches, branch_heads)
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


def _trailer_lost(repo: Repo, branch: str, onto: str) -> bool:
    """Whether a rebase left the branch without its Shortcake-Parent trailer.

    Two shapes: the trailer commit was dropped but other commits remain (no
    trailer found walking the branch), or every commit was dropped and the
    branch is parked exactly at its parent's head — the trailer walk can't
    tell that apart from the parent's own history, so check head equality
    explicitly.
    """
    if git.branch_exists(repo, onto) and git.get_branch_head(
        repo, branch
    ) == git.get_branch_head(repo, onto):
        return True
    all_branches = set(git.get_all_local_branches(repo))
    return git.get_branch_parent(repo, branch, all_branches) is None


def _restore_trailer(repo: Repo, branch: str, parent: str) -> None:
    """Restore Shortcake-Parent trailer if it was lost during rebase.

    When --empty=drop drops the commit carrying the trailer (because its file
    changes are already in the new parent), the branch becomes untracked.
    This re-adds the trailer to the first commit of the branch, or creates
    a new empty commit with the trailer if all commits were dropped.
    """
    import time

    import pygit2

    from shortcake.commands.adopt import _replay_commits

    branch_head = git.get_branch_head(repo, branch)
    parent_head = git.get_branch_head(repo, parent)
    commits = git.get_commits_between(repo, branch_head, parent_head)

    if not commits:
        # All commits were dropped — create a new empty commit with the trailer
        new_trailers = Trailers(parent_branch=parent)
        message = new_trailers.apply_to(f"chore: track {branch}")

        parent_commit = repo.get(parent_head.decode())
        now = int(time.time())
        new_oid = repo.create_commit(
            None,  # don't update any ref
            parent_commit.author,
            pygit2.Signature(
                parent_commit.committer.name,
                parent_commit.committer.email,
                now,
                parent_commit.committer.offset,
            ),
            message,
            parent_commit.tree_id,
            [pygit2.Oid(hex=parent_head.decode())],
        )
        git.update_branch(repo, branch, str(new_oid))
        return

    # The oldest commit is last in list (walker returns newest-first)
    first_commit_sha = commits[-1]
    message = git.get_commit_message(repo, first_commit_sha)

    # Add the trailer to the first commit
    new_trailers = Trailers(parent_branch=parent)
    new_message = new_trailers.apply_to(message)
    new_first_sha = git.amend_commit_message(repo, first_commit_sha, new_message)

    # Replay any commits above the first one
    if len(commits) > 1:
        new_head = _replay_commits(repo, commits[:-1], new_first_sha)
    else:
        new_head = new_first_sha

    # Update branch ref
    git.update_branch(repo, branch, new_head.decode())


def _get_conflict_files(repo: Repo | str) -> list[str]:
    """Get list of files with conflicts."""
    try:
        if isinstance(repo, str):
            return git.get_conflict_files(git.open_repo(Path(repo)))
        return git.get_conflict_files(repo)
    except (*git.DULWICH_IO_ERRORS, ValueError):
        return []


def _show_conflict_message(
    branch: str,
    onto: str,
    conflict_files: list[str],
    toolkit: ShortcakeRichToolkit | None = None,
) -> None:
    """Display conflict resolution instructions."""
    toolkit = toolkit or get_rich_toolkit()
    # The view already closed with a blank line, so this one only needs its own
    # trailing gap.
    toolkit.echo(f"Conflict while rebasing '{branch}' onto '{onto}'.\n")

    if conflict_files:
        toolkit.echo("Fix conflicts in the following files:")
        for f in conflict_files:
            toolkit.echo(f"  {f}")
        toolkit.echo()

    toolkit.echo("Then:")
    toolkit.echo("  1. Stage your changes:     git add <files>")
    toolkit.echo("  2. Continue the restack:   sc continue")
    toolkit.echo()
    toolkit.echo("Or abort with: sc abort")


def _show_rebase_error(
    branch: str,
    onto: str,
    error_output: str,
    toolkit: ShortcakeRichToolkit | None = None,
) -> None:
    """Display rebase error message (non-conflict failure)."""
    toolkit = toolkit or get_rich_toolkit()
    toolkit.echo(f"\nFailed to rebase '{branch}' onto '{onto}'.\n", err=True)
    if error_output:
        toolkit.echo("Git error:", err=True)
        for line in error_output.strip().split("\n"):
            toolkit.echo(f"  {line}", err=True)
        toolkit.echo()
    toolkit.echo("Abort with: sc abort", err=True)


def _restack(
    repo: Repo,
    dry_run: bool = False,
    toolkit: ShortcakeRichToolkit | None = None,
    branches: list[str] | None = None,
) -> RestackResult:
    """
    Restack the current branch's stack, or a caller-provided branch subset.

    Raises RestackError on failure, returns RestackResult on success.
    """
    toolkit = toolkit or get_rich_toolkit()
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

    # A trailer pointing at a deleted branch can't be restacked — surface it
    # instead of reporting "everything up to date".
    orphaned_parent = None
    if current_branch_parent is not None and current_branch_parent not in all_branches:
        orphaned_parent = current_branch_parent

    # Get stack in topological order. Callers such as partial submit may limit
    # this to the dependency prefix that must be current before it is pushed.
    stack_branches = (
        list(branches)
        if branches is not None
        else _get_stack_in_order(repo, current_branch)
    )

    # Build restack plan
    plan = _plan_restack(repo, stack_branches)

    if not plan:
        return RestackResult(
            restacked_branches=[],
            current_branch_untracked=is_current_untracked,
            orphaned_parent=orphaned_parent,
        )

    # Dry run - just show plan
    if dry_run:
        toolkit.echo(f"Would restack {len(plan)} branch(es):")
        for step in plan:
            toolkit.echo(f"  {step.branch} onto {step.onto}")
        return RestackResult(
            restacked_branches=[],
            planned=[(step.branch, step.onto) for step in plan],
        )

    # Save original refs for rollback
    original_refs = {}
    for step in plan:
        # SHA is stored as 40 ASCII hex bytes, decode to string
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

    # Execute restack — same stack view submit uses, so a rebase reads like a
    # rebase of the *stack* rather than a flat list of branch names.
    rows = [StackRow(step.branch, parent=step.onto, label=Text("")) for step in plan]
    if plan:
        rows.insert(
            0,
            StackRow(
                plan[0].onto, state=RowState.BASE, label=Text("(base)", style=DIM)
            ),
        )
    by_branch = {row.branch: row for row in rows}
    noun = "branch" if len(plan) == 1 else "branches"
    view, _ = toolkit.stack_view(rows, f"Restacking {len(plan)} {noun}")

    restacked = []
    any_skipped_empty = False
    view.__enter__()
    for i, step in enumerate(plan):
        state.current_index = i
        state.save(repo)

        row = by_branch[step.branch]
        row.state = RowState.ACTIVE
        row.label = Text(f"rebasing onto {step.onto}…", style=Style(color="cyan"))
        view.sync()
        result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)

        if not result.success:
            row.state = RowState.FAILED
            row.label = Text(
                "conflict" if git.is_rebase_in_progress(repo) else "failed",
                style=Style(color="red"),
            )
            view.sync()
            view.__exit__(None, None, None)
            # Check if this is a conflict or other error
            if git.is_rebase_in_progress(repo):
                conflict_files = _get_conflict_files(repo)
                _show_conflict_message(step.branch, step.onto, conflict_files, toolkit)
            else:
                _show_rebase_error(step.branch, step.onto, result.error_output, toolkit)
            return RestackResult(
                restacked_branches=restacked, conflict_branch=step.branch
            )

        row.state = RowState.DONE
        if result.skipped_empty:
            row.label = Text("empty, already applied", style=DIM)
            any_skipped_empty = True
        else:
            row.label = Text("rebased", style=Style(color="green"))

        # Check if trailer survived the rebase (--empty=drop may have dropped it)
        if _trailer_lost(repo, step.branch, step.onto):
            _restore_trailer(repo, step.branch, step.onto)

        restacked.append(step.branch)
        view.sync()

    head = Text("✓ ", style=Style(color="green"))
    head.append(f"{len(restacked)} {noun} restacked")
    view.finish([head])
    view.sync()
    view.__exit__(None, None, None)

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
    json_output: Annotated[
        bool, typer.Option("--json", help="Output the result as JSON")
    ] = False,
) -> None:
    """Restack current branch's stack."""
    repo = git.open_repo()
    toolkit = get_rich_toolkit(json_output=json_output)

    try:
        result = _restack(repo, dry_run=dry_run, toolkit=toolkit)
    except RestackError as e:
        toolkit.fail("restack_failed", str(e))

    if json_output:
        conflict = None
        if result.conflict_branch:
            conflict = {
                "branch": result.conflict_branch,
                "files": _get_conflict_files(repo),
                "resolve": "Stage the fixed files with 'git add', then run "
                "'sc continue' (or 'sc abort' to roll back)",
            }
        toolkit.success(
            {
                "restacked": result.restacked_branches,
                "planned": [
                    {"branch": branch, "onto": onto} for branch, onto in result.planned
                ],
                "current_branch_untracked": result.current_branch_untracked,
                "orphaned_parent": result.orphaned_parent,
                "conflict": conflict,
            }
        )
        if result.conflict_branch:
            raise typer.Exit(1)
        return

    if result.conflict_branch:
        raise typer.Exit(1)

    if not result.restacked_branches and not dry_run:
        if result.current_branch_untracked:
            typer.echo(
                "Current branch is not tracked (no Shortcake-Parent trailer). "
                "Nothing to restack. "
                "Use 'sc adopt --parent <parent>' to track it."
            )
        elif result.orphaned_parent:
            typer.echo(
                f"Parent branch '{result.orphaned_parent}' no longer exists. "
                "Re-parent with 'sc adopt -f -p <new-parent>', "
                "then run 'sc restack' again."
            )
        else:
            typer.echo("Everything up to date.")
    # Nothing to print on success: the stack view already rendered the tree and
    # its "✓ N branches restacked" footer.

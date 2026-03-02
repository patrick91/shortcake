"""Fold (absorb) one branch into another, removing it from the stack."""

import contextlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._trailers import Trailers
from shortcake.commands.adopt import _replay_commits
from shortcake.commands.move_lines import (
    _get_tracked_branches_in_order,
    _git_apply,
    _stage_patch_files,
)
from shortcake.commands.restack import _plan_restack, _rebase_branch


class FoldError(ShortcakeError):
    """Error during fold operation."""

    pass


@dataclass
class FoldResult:
    source_branch: str
    target_branch: str
    reparented_children: list[str] = field(default_factory=list)
    restacked_branches: list[str] = field(default_factory=list)


def _get_branch_diff(repo_path: Path, merge_base: str, head: str) -> str:
    """Get the full diff of a branch relative to its merge base."""
    result = subprocess.run(
        ["git", "diff", f"{merge_base}..{head}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:  # pragma: no cover
        raise FoldError(f"Failed to get branch diff: {result.stderr.strip()}")
    return result.stdout


def _reparent_branch(repo: Repo, branch: str, new_parent: str) -> None:
    """Update a branch's Shortcake-Parent trailer to point to new_parent.

    Finds the commit with the trailer, updates it, and replays any commits
    above it.
    """
    all_branches = set(git.get_all_local_branches(repo))
    branch_head = git.get_branch_head(repo, branch)

    # Find all commits on this branch
    parent_info = git.get_branch_parent_info(repo, branch, all_branches)
    if parent_info is None:  # pragma: no cover
        raise FoldError(f"Branch '{branch}' is not tracked by Shortcake")

    old_parent = parent_info[0]
    old_parent_head = git.get_branch_head(repo, old_parent)

    # Get all commits on this branch (newest-first)
    commits = git.get_commits_between(repo, branch_head, old_parent_head)
    if not commits:  # pragma: no cover
        raise FoldError(f"No commits found on branch '{branch}'")

    # The first commit (oldest) has the trailer
    first_commit_sha = commits[-1]
    first_commit_message = git.get_commit_message(repo, first_commit_sha)

    # Remove old trailer and add new one
    trailers = Trailers.from_message(first_commit_message)
    clean_message = trailers.remove_from(first_commit_message)
    new_trailers = Trailers(parent_branch=new_parent)
    new_message = new_trailers.apply_to(clean_message)

    # Amend the first commit's message
    new_first_sha = git.amend_commit_message(repo, first_commit_sha, new_message)

    # Replay any commits above the first one
    if len(commits) > 1:
        new_head = _replay_commits(repo, commits[:-1], new_first_sha)
    else:
        new_head = new_first_sha

    # Update branch ref
    git.update_branch(repo, branch, new_head.decode())


def _fold(
    repo: Repo, into: str | None = None, no_verify: bool = False
) -> FoldResult:
    """Fold the current branch into a target branch.

    Takes the current branch's full diff, applies it to the target, amends
    the target's commit, re-parents children, restacks, and deletes the
    source branch.

    Args:
        repo: The git repository.
        into: Target branch to fold into (default: parent of current branch).
        no_verify: Skip pre-commit hooks.

    Raises FoldError on failure (with rollback), returns FoldResult on success.
    """
    repo_path = Path(repo.path)

    # --- Preconditions ---
    source_branch = git.get_current_branch(repo)
    if source_branch is None:
        raise FoldError("Cannot fold in detached HEAD state")

    if git.has_uncommitted_changes(repo):
        raise FoldError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise FoldError("Git rebase in progress. Complete or abort it first.")

    all_branches = set(git.get_all_local_branches(repo))
    source_parent = git.get_branch_parent(repo, source_branch, all_branches)
    if source_parent is None:
        raise FoldError(f"Branch '{source_branch}' is not tracked by Shortcake")

    # Resolve target
    target_branch = source_parent if into is None else into

    if target_branch == source_branch:
        raise FoldError("Cannot fold a branch into itself")

    if not git.branch_exists(repo, target_branch):
        raise FoldError(f"Branch '{target_branch}' does not exist")

    # --- Get source branch diff ---
    source_head = git.get_branch_head(repo, source_branch)

    # Use get_branch_parent_info to find the correct diff base: the git parent
    # of the source branch's first commit (the one with the trailer). This is
    # stable even when the parent branch was rebased/restacked, unlike
    # git merge-base which can return an ancestor that's too old.
    parent_info = git.get_branch_parent_info(repo, source_branch, all_branches)
    if parent_info is None:  # pragma: no cover
        raise FoldError(
            f"No common history between '{source_branch}' and '{source_parent}'"
        )
    diff_base = parent_info[1]
    if diff_base is None:  # pragma: no cover
        raise FoldError(f"Branch '{source_branch}' has no parent commit (orphan)")

    branch_diff = _get_branch_diff(repo_path, diff_base.decode(), source_head.decode())

    # --- Get children before we modify anything ---
    children = git.get_branch_children(repo, source_branch)

    # --- Save state for rollback ---
    all_tracked = _get_tracked_branches_in_order(repo)
    original_refs: dict[str, str] = {}
    for b in all_tracked:
        original_refs[b] = git.get_branch_head(repo, b).decode()
    # Save source ref too (may not be in tracked order)
    original_refs[source_branch] = git.get_branch_head(repo, source_branch).decode()

    def _rollback() -> None:  # pragma: no cover
        """Restore all modified branch refs, recreate source if deleted."""
        if git.is_rebase_in_progress(repo):
            with contextlib.suppress(Exception):
                git.rebase_abort(repo)
        for b, sha in original_refs.items():
            with contextlib.suppress(Exception):
                if not git.branch_exists(repo, b):
                    git.create_branch(repo, b, sha)
                else:
                    git.update_branch(repo, b, sha)
        with contextlib.suppress(Exception):
            git.switch_branch(repo, source_branch, force=True)

    try:
        # --- Step 1: Apply diff to target ---
        git.switch_branch(repo, target_branch)

        if branch_diff.strip():
            _git_apply(repo_path, branch_diff, reverse=False, three_way=True)
            _stage_patch_files(repo_path, branch_diff)
            target_head = git.get_branch_head(repo, target_branch)
            target_message = git.get_commit_message(repo, target_head)
            git.amend_commit(repo, target_message, no_verify=no_verify)

        # --- Step 2: Re-parent children ---
        reparented: list[str] = []
        for child in children:
            _reparent_branch(repo, child, source_parent)
            reparented.append(child)

        # --- Step 3: Delete source branch ---
        git.delete_branch(repo, source_branch)

        # --- Step 4: Restack ---
        # Re-fetch tracked branches after deletion
        all_tracked_after = _get_tracked_branches_in_order(repo)
        restacked: list[str] = []
        plan = _plan_restack(repo, all_tracked_after)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:  # pragma: no cover
                _rollback()
                raise FoldError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked.append(step.branch)

        # --- Step 5: Switch to target ---
        git.switch_branch(repo, target_branch, force=True)

        return FoldResult(
            source_branch=source_branch,
            target_branch=target_branch,
            reparented_children=reparented,
            restacked_branches=restacked,
        )

    except FoldError:  # pragma: no cover
        raise
    except Exception as e:
        _rollback()
        raise FoldError(f"Unexpected error: {e}") from e


# Typer command


def fold(
    into: Annotated[
        str | None,
        typer.Option("--into", "-i", help="Target branch to fold into"),
    ] = None,
    no_verify: Annotated[
        bool,
        typer.Option("--no-verify", "-n", help="Skip pre-commit hooks"),
    ] = False,
) -> None:
    """Fold current branch into another branch (default: parent)."""
    repo = git.open_repo()

    try:
        result = _fold(repo, into=into, no_verify=no_verify)
    except FoldError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Folded '{result.source_branch}' into '{result.target_branch}'")
    if result.reparented_children:
        children_str = ", ".join(f"'{c}'" for c in result.reparented_children)
        typer.echo(f"Re-parented {children_str} to '{result.target_branch}'")
    if result.restacked_branches:
        typer.echo(f"Restacked {len(result.restacked_branches)} branch(es).")

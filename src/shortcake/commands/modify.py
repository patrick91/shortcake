import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake._editor import open_editor
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._trailers import Trailers, strip_trailers
from shortcake.commands.move_lines import (
    _get_tracked_branches_in_order,
    _git_apply,
    _stage_patch_files,
    _stash_pop,
    _stash_push,
)
from shortcake.commands.restack import _plan_restack, _rebase_branch


class ModifyError(ShortcakeError):
    """Error during modify operation."""

    pass


@dataclass
class ModifyResult:
    old_sha: bytes
    new_sha: bytes
    message: str
    is_amend: bool
    target_branch: str | None = None
    restacked_branches: list[str] = field(default_factory=list)


def _modify_amend(repo: Repo, message: str, no_verify: bool = False) -> ModifyResult:
    """Amend HEAD commit, preserving Shortcake-Parent trailer.

    Args:
        repo: The git repository
        message: New commit message (without trailers)
        no_verify: Skip pre-commit hooks

    Returns:
        ModifyResult with old/new SHAs and final message
    """
    old_sha = str(repo.head.target).encode()
    old_message = git.get_commit_message(repo, old_sha)

    # Preserve trailer from old commit
    trailers = Trailers.from_message(old_message)
    if trailers.parent_branch is not None:
        message = trailers.apply_to(message)

    new_sha = git.amend_commit(repo, message, no_verify=no_verify)
    return ModifyResult(
        old_sha=old_sha, new_sha=new_sha, message=message, is_amend=True
    )


def _modify_with_new_commit(
    repo: Repo, message: str, no_verify: bool = False
) -> ModifyResult:
    """Create new commit, preserving Shortcake-Parent trailer from HEAD.

    Args:
        repo: The git repository
        message: Commit message (without trailers)
        no_verify: Skip pre-commit hooks

    Returns:
        ModifyResult with old/new SHAs and final message
    """
    old_sha = str(repo.head.target).encode()
    old_message = git.get_commit_message(repo, old_sha)

    # Preserve trailer from old commit
    trailers = Trailers.from_message(old_message)
    if trailers.parent_branch is not None:
        message = trailers.apply_to(message)

    new_sha = git.create_commit(repo, message, no_verify=no_verify)
    return ModifyResult(
        old_sha=old_sha, new_sha=new_sha, message=message, is_amend=False
    )


def _modify_target(
    repo: Repo, target_branch: str, no_verify: bool = False
) -> ModifyResult:
    """Fold staged changes into another branch's commit.

    Takes the currently staged changes, removes them from the current working
    tree, applies them to the target branch's commit, amends it, and restacks
    downstream branches.

    Raises ModifyError on any failure (with rollback of modified refs).
    """
    repo_path = Path(repo.workdir)

    # --- Preconditions ---
    current = git.get_current_branch(repo)
    if current is None:  # pragma: no cover
        raise ModifyError("Cannot modify in detached HEAD state")

    if target_branch == current:
        raise ModifyError("Target branch cannot be the current branch")

    if not git.has_staged_changes(repo):
        raise ModifyError("No staged changes to fold")

    if not git.branch_exists(repo, target_branch):
        raise ModifyError(f"Branch '{target_branch}' does not exist")

    all_branches = set(git.get_all_local_branches(repo))
    target_parent = git.get_branch_parent(repo, target_branch, all_branches)
    if target_parent is None:
        raise ModifyError(f"Branch '{target_branch}' is not tracked by Shortcake")

    if git.is_rebase_in_progress(repo):
        raise ModifyError("Git rebase in progress. Complete or abort it first.")

    # --- Get staged diff ---
    staged_diff = git.get_staged_diff(repo)
    if not staged_diff.strip():  # pragma: no cover
        raise ModifyError("No staged changes to fold")

    # --- Save state for rollback ---
    all_tracked = _get_tracked_branches_in_order(repo)
    original_refs: dict[str, str] = {}
    for b in all_tracked:
        original_refs[b] = git.get_branch_head(repo, b).decode()
    stashed = False

    def _rollback() -> None:
        """Restore all modified branch refs, abort rebase, switch back, pop stash."""
        if git.is_rebase_in_progress(repo):
            with contextlib.suppress(Exception):
                git.rebase_abort(repo)
        for b, sha in original_refs.items():
            with contextlib.suppress(Exception):
                git.update_branch(repo, b, sha)
        with contextlib.suppress(Exception):
            git.switch_branch(repo, current, force=True)
        if stashed:  # pragma: no cover
            _stash_pop(repo_path)

    try:
        # --- Step 1: Unstage changes ---
        git.unstage_all(repo)

        # --- Step 2: Reverse-apply patch on working tree ---
        # This removes only the staged changes from the working tree,
        # leaving other working tree changes intact.
        _git_apply(repo_path, staged_diff, reverse=True)

        # --- Step 3: Stash remaining working tree changes ---
        stashed = _stash_push(repo_path)

        # --- Step 4: Switch to target branch ---
        git.switch_branch(repo, target_branch)

        # --- Step 5: Forward-apply the patch on target ---
        # Use --3way so git falls back to three-way merge when context
        # lines differ (e.g. an intermediate branch modified the file).
        _git_apply(repo_path, staged_diff, three_way=True)

        # --- Step 6: Stage and amend target commit ---
        _stage_patch_files(repo_path, staged_diff)
        old_sha = git.get_branch_head(repo, target_branch)
        target_message = git.get_commit_message(repo, old_sha)
        new_sha = git.amend_commit(repo, target_message, no_verify=no_verify)

        # --- Step 7: Restack downstream branches ---
        restacked: list[str] = []
        plan = _plan_restack(repo, all_tracked)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:
                _rollback()
                raise ModifyError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked.append(step.branch)

        # --- Step 8: Switch back and pop stash ---
        git.switch_branch(repo, current, force=True)
        if stashed:
            _stash_pop(repo_path)
            stashed = False

        return ModifyResult(
            old_sha=old_sha,
            new_sha=new_sha,
            message=target_message,
            is_amend=True,
            target_branch=target_branch,
            restacked_branches=restacked,
        )

    except ModifyError:
        with contextlib.suppress(Exception):
            _git_apply(repo_path, staged_diff, index=True)
        raise
    except Exception as e:  # pragma: no cover
        _rollback()
        with contextlib.suppress(Exception):
            _git_apply(repo_path, staged_diff, index=True)
        raise ModifyError(f"Unexpected error: {e}") from e


def modify(
    message: Annotated[str | None, typer.Option("--message", "-m")] = None,
    edit: Annotated[bool, typer.Option("--edit", "-e")] = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", "-n")] = False,
    target: Annotated[
        str | None,
        typer.Option(
            "--target", "-t", help="Fold staged changes into this branch's commit."
        ),
    ] = None,
) -> None:
    """Modify the current commit or create a new one.

    Without flags: amend with staged changes (keeps existing message).
    Use -e/--edit to amend and edit the message.
    Use -m/--message to create a new commit with the given message.
    Use -t/--target to fold staged changes into another branch's commit.
    """
    repo = git.open_repo()

    # Check we're on a branch
    current = git.get_current_branch(repo)
    if current is None:
        typer.echo("Error: Cannot modify in detached HEAD state", err=True)
        raise typer.Exit(1)

    # Validate options
    if message and edit:
        typer.echo("Error: Cannot use both -m and -e", err=True)
        raise typer.Exit(1)

    if target and message:
        typer.echo("Error: Cannot use both -t and -m", err=True)
        raise typer.Exit(1)

    if target and edit:
        typer.echo("Error: Cannot use both -t and -e", err=True)
        raise typer.Exit(1)

    # Handle --target mode
    if target:
        # Check for staged changes and run hooks if needed
        has_staged = git.has_staged_changes(repo)
        if (  # pragma: no cover
            not no_verify and has_staged and git.has_precommit_hook(repo)
        ):
            typer.echo("Running pre-commit hooks...")
            success, error = git.run_precommit_hook(repo)
            if not success:
                typer.echo(f"Error: Pre-commit hook failed:\n{error}", err=True)
                raise typer.Exit(1)

        try:
            result = _modify_target(repo, target, no_verify=True)
        except ModifyError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None

        typer.echo(f"Folded staged changes into '{target}'")
        if result.restacked_branches:
            typer.echo(f"Restacked {len(result.restacked_branches)} branch(es).")
        return

    # Check for staged changes and run hooks if needed
    has_staged = git.has_staged_changes(repo)
    hooks_already_ran = False
    if not no_verify and has_staged and git.has_precommit_hook(repo):
        typer.echo("Running pre-commit hooks...")
        success, error = git.run_precommit_hook(repo)
        if not success:
            typer.echo(f"Error: Pre-commit hook failed:\n{error}", err=True)
            raise typer.Exit(1)
        hooks_already_ran = True

    # Skip hooks in commit if we already ran them manually (to avoid duplicate output)
    skip_hooks = no_verify or hooks_already_ran

    if edit:
        # Amend with editor
        old_sha = str(repo.head.target).encode()
        old_message = git.get_commit_message(repo, old_sha)
        editor_content = strip_trailers(old_message)

        new_message = open_editor(editor_content)
        if not new_message:
            typer.echo("Aborted: empty message.", err=True)
            raise typer.Exit(1)

        _modify_amend(repo, new_message, no_verify=skip_hooks)
        typer.echo(f"Amended commit on '{current}'")
    elif message:
        # New commit with -m message
        if not has_staged:
            typer.echo("Error: No staged changes to commit", err=True)
            raise typer.Exit(1)

        _modify_with_new_commit(repo, message, no_verify=skip_hooks)
        typer.echo(f"Created commit on '{current}'")
    else:
        # Amend with staged changes, keep existing message
        if not has_staged:
            typer.echo("Error: No staged changes to amend", err=True)
            raise typer.Exit(1)

        old_sha = str(repo.head.target).encode()
        old_message = git.get_commit_message(repo, old_sha)
        # Strip and reapply trailers to preserve them
        clean_message = strip_trailers(old_message)

        _modify_amend(repo, clean_message, no_verify=skip_hooks)
        typer.echo(f"Amended commit on '{current}'")

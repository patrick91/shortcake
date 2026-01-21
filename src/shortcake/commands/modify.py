from dataclasses import dataclass
from typing import Annotated

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._editor import open_editor
from shortcake._exceptions import ShortcakeError
from shortcake._trailers import Trailers, strip_trailers


class ModifyError(ShortcakeError):
    """Error during modify operation."""

    pass


@dataclass
class ModifyResult:
    old_sha: bytes
    new_sha: bytes
    message: str
    is_amend: bool


def _modify_amend(repo: Repo, message: str, no_verify: bool = False) -> ModifyResult:
    """Amend HEAD commit, preserving Shortcake-Parent trailer.

    Args:
        repo: The git repository
        message: New commit message (without trailers)
        no_verify: Skip pre-commit hooks

    Returns:
        ModifyResult with old/new SHAs and final message
    """
    old_sha = repo.head()
    old_message = git.get_commit_message(repo, old_sha)

    # Preserve trailer from old commit
    trailers = Trailers.from_message(old_message)
    if trailers.parent_branch is not None:
        message = trailers.apply_to(message)

    new_sha = git.amend_commit(repo, message, no_verify=no_verify)
    return ModifyResult(
        old_sha=old_sha, new_sha=new_sha, message=message, is_amend=True
    )


def _modify_new(repo: Repo, message: str, no_verify: bool = False) -> ModifyResult:
    """Create new commit, preserving Shortcake-Parent trailer from HEAD.

    Args:
        repo: The git repository
        message: Commit message (without trailers)
        no_verify: Skip pre-commit hooks

    Returns:
        ModifyResult with old/new SHAs and final message
    """
    old_sha = repo.head()
    old_message = git.get_commit_message(repo, old_sha)

    # Preserve trailer from old commit
    trailers = Trailers.from_message(old_message)
    if trailers.parent_branch is not None:
        message = trailers.apply_to(message)

    new_sha = git.create_commit(repo, message, no_verify=no_verify)
    return ModifyResult(
        old_sha=old_sha, new_sha=new_sha, message=message, is_amend=False
    )


def modify(
    message: Annotated[str | None, typer.Option("--message", "-m")] = None,
    edit: Annotated[bool, typer.Option("--edit", "-e")] = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", "-n")] = False,
) -> None:
    """Modify the current commit or create a new one.

    Use -m/--message to create a new commit with the given message.
    Without flags (or with -e/--edit), amends the current commit (opens editor).
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

    # Check for staged changes and run hooks if needed
    has_staged = git.has_staged_changes(repo)
    if not no_verify and has_staged and git.has_precommit_hook(repo):
        typer.echo("Running pre-commit hooks...")
        success, error = git.run_precommit_hook(repo)
        if not success:
            typer.echo(f"Error: Pre-commit hook failed:\n{error}", err=True)
            raise typer.Exit(1)

    if edit or not message:
        # Amend: open editor with current message
        old_sha = repo.head()
        old_message = git.get_commit_message(repo, old_sha)
        editor_content = strip_trailers(old_message)

        message = open_editor(editor_content)
        if not message:
            typer.echo("Aborted: empty message.", err=True)
            raise typer.Exit(1)

        _modify_amend(repo, message, no_verify=no_verify)
        typer.echo(f"Amended commit on '{current}'")
    else:
        # New commit with -m message
        if not has_staged:
            typer.echo("Error: No staged changes to commit", err=True)
            raise typer.Exit(1)

        _modify_new(repo, message, no_verify=no_verify)  # type: ignore
        typer.echo(f"Created commit on '{current}'")

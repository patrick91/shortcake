import re
from dataclasses import dataclass
from typing import Annotated

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._editor import open_editor
from shortcake._exceptions import ShortcakeError
from shortcake._gitmoji import pick_gitmoji
from shortcake._trailers import Trailers


class CreateError(ShortcakeError):
    """Error during create operation."""

    pass


class EmptyBranchNameError(CreateError):
    """Raised when branch name cannot be generated from message."""

    pass


class BranchExistsError(CreateError):
    """Raised when branch name already exists."""

    def __init__(self, branch: str) -> None:
        self.branch = branch
        super().__init__(f"Branch '{branch}' already exists")


@dataclass
class CreateResult:
    branch: str
    parent: str
    message: str


def _slugify(message: str) -> str:
    """Convert commit message to branch name."""
    # Take first line only
    first_line = message.split("\n")[0]
    slug = first_line.lower()
    # Replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    # Max 50 characters
    return slug[:50]


def _validate_branch_name(repo: Repo, branch: str) -> None:
    """Validate branch name.

    Raises:
        EmptyBranchNameError: If branch name is empty
        BranchExistsError: If branch already exists
    """
    if not branch:
        raise EmptyBranchNameError("Cannot generate branch name from message")

    if git.branch_exists(repo, branch):
        raise BranchExistsError(branch)


def _create(repo: Repo, message: str, branch_name: str) -> CreateResult:
    """Create new tracked branch with commit.

    Assumes caller has verified we're not in detached HEAD state
    and branch_name is valid.
    """
    parent = git.get_current_branch(repo)
    assert parent is not None  # Caller should check this

    head_sha = git.get_branch_head(repo, parent)
    git.create_branch(repo, branch_name, head_sha)
    git.checkout_branch(repo, branch_name)

    trailers = Trailers(parent_branch=parent)
    full_message = trailers.apply_to(message)
    git.create_commit(repo, full_message, no_verify=True)

    return CreateResult(branch=branch_name, parent=parent, message=message)


def create(
    message: Annotated[str | None, typer.Option("--message", "-m")] = None,
    gitmoji: Annotated[bool, typer.Option("--gitmoji", "--gm")] = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", "-n")] = False,
    allow_empty: Annotated[bool, typer.Option("--allow-empty")] = False,
) -> None:
    """Create new tracked branch with commit."""
    repo = git.open_repo()

    # Check we're on a branch
    parent = git.get_current_branch(repo)
    if parent is None:
        typer.echo("Error: Cannot create in detached HEAD state", err=True)
        raise typer.Exit(1)

    # Check for staged changes
    has_staged = git.has_staged_changes(repo)
    if not has_staged and not allow_empty:
        typer.echo(
            "Error: No staged changes. Use --allow-empty to create anyway.", err=True
        )
        raise typer.Exit(1)

    # Run pre-commit hooks FIRST (before user writes message)
    # We handle hooks ourselves, dulwich always skips them
    if not no_verify and has_staged and git.has_precommit_hook(repo):
        typer.echo("Running pre-commit hooks...")
        success, error = git.run_precommit_hook(repo)
        if not success:
            typer.echo(f"Error: Pre-commit hook failed:\n{error}", err=True)
            raise typer.Exit(1)

    # Get message (interactive or from -m)
    if message is None:
        prefix = ""
        if gitmoji:
            selected = pick_gitmoji()
            if selected is None:
                typer.echo("Cancelled.", err=True)
                raise typer.Exit(1)
            prefix = f"{selected.emoji} "

        message = open_editor(prefix)
        if not message:
            typer.echo("Aborted: empty message.", err=True)
            raise typer.Exit(1)

    # Get valid branch name (loop until we have one)
    branch_name = _slugify(message)
    while True:
        try:
            _validate_branch_name(repo, branch_name)
            break
        except EmptyBranchNameError:
            user_input = typer.prompt("Could not generate branch name. Enter a name")
            branch_name = _slugify(user_input)
            if not branch_name:
                typer.echo("Error: Invalid branch name", err=True)
                raise typer.Exit(1) from None
        except BranchExistsError as e:
            user_input = typer.prompt(
                f"Branch '{e.branch}' already exists. Enter a name"
            )
            branch_name = _slugify(user_input)
            if not branch_name:
                typer.echo("Error: Invalid branch name", err=True)
                raise typer.Exit(1) from None

    # Create the branch
    try:
        result = _create(repo, message, branch_name)
    except CreateError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Created branch '{result.branch}' from '{result.parent}'")

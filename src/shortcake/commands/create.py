import re
import subprocess
from dataclasses import dataclass, field
from typing import Annotated

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._editor import open_editor
from shortcake._exceptions import ShortcakeError
from shortcake._gitmoji import pick_gitmoji
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
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


class InsertError(CreateError):
    """Error during insert-before or insert-after operation."""

    pass


@dataclass
class CreateResult:
    branch: str
    parent: str
    message: str
    inserted_before: str | None = None
    inserted_after: str | None = None
    rebased_branches: list[str] = field(default_factory=list)
    conflict_branch: str | None = None


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
    git.set_head_to_branch(repo, branch_name)

    trailers = Trailers(parent_branch=parent)
    full_message = trailers.apply_to(message)
    git.create_commit(repo, full_message, no_verify=True)

    return CreateResult(branch=branch_name, parent=parent, message=message)


def _create_insert_before(repo: Repo, message: str, branch_name: str) -> CreateResult:
    """Insert a new branch before the current branch.

    Given stack main → A → B → C, on B: inserts NEW between A and B.
    Result: main → A → NEW → B → C

    The new branch is created at the current branch's parent's HEAD,
    then the current branch is rebased onto the new branch.
    """
    from shortcake.commands.reorder import _update_branch_trailer
    from shortcake.commands.restack import (
        _get_conflict_files,
        _rebase_branch,
        _show_conflict_message,
        _show_rebase_error,
    )

    current_branch = git.get_current_branch(repo)
    assert current_branch is not None

    all_branches = set(git.get_all_local_branches(repo))

    # Current branch must be tracked (has Shortcake-Parent trailer)
    parent_info = git.get_branch_parent_info(repo, current_branch, all_branches)
    if parent_info is None:
        raise InsertError(
            f"Branch '{current_branch}' is not tracked by Shortcake. "
            f"Cannot insert before an untracked branch."
        )

    parent, merge_base = parent_info

    # Create new branch at the parent's HEAD
    parent_head = git.get_branch_head(repo, parent)

    # If there are staged changes, commit them temporarily on the current
    # branch so we can cleanly switch to the new branch. Then we copy the
    # exact staged file contents onto the new branch (no 3-way merge needed).
    has_staged = git.has_staged_changes(repo)
    staged_files = git.get_staged_files(repo) if has_staged else []
    original_head = None
    temp_sha = None

    if has_staged:
        original_head = git.get_branch_head(repo, current_branch)
        temp_sha = git.create_commit(
            repo, "shortcake: temp commit for insert", no_verify=True
        )

    git.create_branch(repo, branch_name, parent_head)
    git.switch_branch(repo, branch_name)

    # Build the commit message with trailer
    trailers = Trailers(parent_branch=parent)
    full_message = trailers.apply_to(message)

    if temp_sha:
        # Reset current branch to drop the temp commit
        git.update_branch(repo, current_branch, original_head.decode())

        # Copy the exact staged file contents from the temp commit onto
        # the new branch. This avoids cherry-pick's 3-way merge which can
        # falsely conflict when files differ between parent and current.
        subprocess.run(
            ["git", "checkout", temp_sha.decode(), "--", *staged_files],
            cwd=repo.path,
            capture_output=True,
            check=True,
        )
        git.create_commit(repo, full_message, no_verify=True)
    else:
        git.create_commit(repo, full_message, no_verify=True)

    # Now rebase current branch onto new branch
    # merge_base is the parent of the first commit with trailer on current branch
    if merge_base is None:  # pragma: no cover
        raise InsertError(
            f"Branch '{current_branch}' has no merge base with parent '{parent}'. "
            f"Cannot insert before it."
        )

    # Save original refs for rollback
    original_refs = {
        current_branch: git.get_branch_head(repo, current_branch).decode(),
    }

    # Build restack plan
    plan = [
        RestackStep(
            branch=current_branch,
            onto=branch_name,
            merge_base=merge_base.decode(),
            new_parent_trailer=branch_name,
        )
    ]

    # Save state for conflict recovery
    state = RestackState(
        version=STATE_VERSION,
        original_branch=branch_name,
        plan=plan,
        current_index=0,
        original_refs=original_refs,
    )
    state.save(repo)

    # Execute rebase
    typer.echo(f"Rebasing '{current_branch}' onto '{branch_name}'...")
    result = _rebase_branch(repo, current_branch, branch_name, merge_base.decode())

    if not result.success:
        if git.is_rebase_in_progress(repo):
            conflict_files = _get_conflict_files(repo)
            _show_conflict_message(current_branch, branch_name, conflict_files)
        else:  # pragma: no cover
            _show_rebase_error(current_branch, branch_name, result.error_output)
        return CreateResult(
            branch=branch_name,
            parent=parent,
            message=message,
            inserted_before=current_branch,
            conflict_branch=current_branch,
        )

    # Update the current branch's trailer to point to new branch
    _update_branch_trailer(repo, current_branch, branch_name)

    # Clean up state
    state.delete(repo)

    # Switch back to new branch
    git.switch_branch(repo, branch_name, force=True)

    return CreateResult(
        branch=branch_name,
        parent=parent,
        message=message,
        inserted_before=current_branch,
        rebased_branches=[current_branch],
    )


def _create_insert_after(repo: Repo, message: str, branch_name: str) -> CreateResult:
    """Insert a new branch after the current branch.

    Given stack main → A → B → C, on B: inserts NEW between B and C.
    Result: main → A → B → NEW → C

    If current branch has no children, this is equivalent to normal create.
    If it has multiple children, raises InsertError.
    """
    from shortcake.commands.reorder import _update_branch_trailer
    from shortcake.commands.restack import (
        _get_conflict_files,
        _rebase_branch,
        _show_conflict_message,
        _show_rebase_error,
    )

    current_branch = git.get_current_branch(repo)
    assert current_branch is not None

    children = git.get_branch_children(repo, current_branch)

    if len(children) > 1:
        raise InsertError(
            f"Branch '{current_branch}' has multiple children "
            f"({', '.join(children)}). "
            f"Use '--before' on a specific child branch instead."
        )

    # Create new branch at current branch's HEAD
    current_head = git.get_branch_head(repo, current_branch)
    git.create_branch(repo, branch_name, current_head)
    git.set_head_to_branch(repo, branch_name)

    # Commit with trailer pointing to current branch
    trailers = Trailers(parent_branch=current_branch)
    full_message = trailers.apply_to(message)
    git.create_commit(repo, full_message, no_verify=True)

    # If no children, we're done (equivalent to normal create)
    if not children:
        return CreateResult(
            branch=branch_name,
            parent=current_branch,
            message=message,
            inserted_after=current_branch,
        )

    # Rebase the single child onto the new branch
    child = children[0]
    all_branches = set(git.get_all_local_branches(repo))
    child_parent_info = git.get_branch_parent_info(repo, child, all_branches)

    if child_parent_info is None or child_parent_info[1] is None:  # pragma: no cover
        raise InsertError(f"Branch '{child}' has no merge base. Cannot rebase it.")

    _, child_merge_base = child_parent_info

    # Save original refs for rollback
    original_refs = {
        child: git.get_branch_head(repo, child).decode(),
    }

    # Build restack plan
    plan = [
        RestackStep(
            branch=child,
            onto=branch_name,
            merge_base=child_merge_base.decode(),
            new_parent_trailer=branch_name,
        )
    ]

    # Save state for conflict recovery
    state = RestackState(
        version=STATE_VERSION,
        original_branch=branch_name,
        plan=plan,
        current_index=0,
        original_refs=original_refs,
    )
    state.save(repo)

    # Execute rebase
    typer.echo(f"Rebasing '{child}' onto '{branch_name}'...")
    result = _rebase_branch(repo, child, branch_name, child_merge_base.decode())

    if not result.success:
        if git.is_rebase_in_progress(repo):
            conflict_files = _get_conflict_files(repo)
            _show_conflict_message(child, branch_name, conflict_files)
        else:  # pragma: no cover
            _show_rebase_error(child, branch_name, result.error_output)
        return CreateResult(
            branch=branch_name,
            parent=current_branch,
            message=message,
            inserted_after=current_branch,
            conflict_branch=child,
        )

    # Update the child's trailer to point to new branch
    _update_branch_trailer(repo, child, branch_name)

    # Clean up state
    state.delete(repo)

    # Switch back to new branch
    git.switch_branch(repo, branch_name, force=True)

    return CreateResult(
        branch=branch_name,
        parent=current_branch,
        message=message,
        inserted_after=current_branch,
        rebased_branches=[child],
    )


def create(
    message: Annotated[str | None, typer.Option("--message", "-m")] = None,
    gitmoji: Annotated[bool, typer.Option("--gitmoji", "--gm")] = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", "-n")] = False,
    allow_empty: Annotated[bool, typer.Option("--allow-empty")] = False,
    before: Annotated[
        bool,
        typer.Option(
            "--before",
            help="Insert new branch before the current branch in the stack.",
        ),
    ] = False,
    after: Annotated[
        bool,
        typer.Option(
            "--after",
            help="Insert new branch after the current branch in the stack.",
        ),
    ] = False,
) -> None:
    """Create new tracked branch with commit."""
    repo = git.open_repo()

    # Validate mutual exclusion of --before and --after
    if before and after:
        typer.echo("Error: Cannot use both --before and --after.", err=True)
        raise typer.Exit(1)

    insert_mode = before or after

    # Check we're on a branch
    parent = git.get_current_branch(repo)
    if parent is None:
        typer.echo("Error: Cannot create in detached HEAD state", err=True)
        raise typer.Exit(1)

    # For insert mode, check additional preconditions
    if insert_mode:
        if git.is_rebase_in_progress(repo):
            typer.echo(
                "Error: Git rebase in progress. Complete or abort it first.",
                err=True,
            )
            raise typer.Exit(1)

        if RestackState.exists(repo):
            typer.echo(
                "Error: Restack already in progress. Use 'sc continue' or 'sc abort'.",
                err=True,
            )
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

    # Dispatch to the right create function
    if before:
        try:
            result = _create_insert_before(repo, message, branch_name)
        except InsertError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
    elif after:
        try:
            result = _create_insert_after(repo, message, branch_name)
        except InsertError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
    else:
        result = _create(repo, message, branch_name)

    # Display output
    typer.echo(f"Created branch '{result.branch}' from '{result.parent}'")

    if result.rebased_branches:
        for b in result.rebased_branches:
            typer.echo(f"Rebased '{b}' onto '{result.branch}'")

    if result.conflict_branch:
        raise typer.Exit(1)

"""Submit command - push branches and create/update GitHub PRs."""

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Annotated

import httpx
import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._github import GitHubClient, get_github_token, get_repo_info, push_branch
from shortcake.commands.restack import _get_stack_in_order

# Markers for stack section in PR body
STACK_START_MARKER = "<!-- shortcake:start -->"
STACK_END_MARKER = "<!-- shortcake:end -->"


class PRAction(Enum):
    """Action taken for a branch during submit."""

    CREATED = auto()
    UPDATED = auto()
    PUSHED = auto()
    SKIPPED = auto()


@dataclass
class BranchSubmitResult:
    """Result of submitting a single branch."""

    branch: str
    action: PRAction
    pr_number: int | None = None
    pr_url: str | None = None
    error: str | None = None


@dataclass
class SubmitResult:
    """Result of submit operation."""

    branch_results: list[BranchSubmitResult] = field(default_factory=list)
    stack_branches: list[str] = field(default_factory=list)


class SubmitError(ShortcakeError):
    """Error during submit operation."""

    pass


def _get_commit_title(repo: Repo, branch: str) -> str:
    """Get the first line of the first commit message on a branch."""
    sha = git.get_branch_head(repo, branch)
    message = git.get_commit_message(repo, sha)
    first_line = message.partition("\n")[0].strip()
    return first_line


def _build_stack_section(
    stack_branches: list[str],
    current_branch: str,
    pr_numbers: dict[str, int],
    owner: str,
) -> str:
    """Build the stack visualization markdown section.

    Args:
        stack_branches: Branches in topological order (bottom to top).
        current_branch: The branch this section is being built for.
        pr_numbers: Map of branch name to PR number.
        owner: GitHub repo owner for PR links.

    Returns:
        Markdown string with stack visualization.
    """
    lines = [STACK_START_MARKER, "## Stack", ""]

    # Show stack in reverse order (top to bottom) for readability
    for branch in reversed(stack_branches):
        pr_num = pr_numbers.get(branch)
        pr_ref = f"#{pr_num}" if pr_num else "(no PR)"

        if branch == current_branch:
            lines.append(f"- **{pr_ref}** (`{branch}`) <-- this PR")
        else:
            lines.append(f"- {pr_ref} (`{branch}`)")

    lines.append(STACK_END_MARKER)
    return "\n".join(lines)


def _update_pr_body_with_stack(
    existing_body: str,
    stack_section: str,
) -> str:
    """Update PR body with stack section.

    If markers exist, replace content between them.
    Otherwise, prepend markers + stack section to existing body.
    """
    # Check if markers already exist
    if STACK_START_MARKER in existing_body and STACK_END_MARKER in existing_body:
        # Replace content between markers
        pattern = re.escape(STACK_START_MARKER) + r".*?" + re.escape(STACK_END_MARKER)
        return re.sub(pattern, stack_section, existing_body, flags=re.DOTALL)
    else:
        # Prepend stack section to existing body
        if existing_body.strip():
            return f"{stack_section}\n\n{existing_body}"
        else:
            return stack_section


def _submit(
    repo: Repo,
    dry_run: bool = False,
    draft: bool = False,
) -> SubmitResult:
    """Submit current stack to GitHub.

    Pushes branches and creates/updates PRs.

    Args:
        repo: The repository.
        dry_run: If True, preview without making changes.
        draft: If True, create draft PRs.

    Returns:
        SubmitResult with per-branch results.

    Raises:
        SubmitError: On precondition failures.
    """
    result = SubmitResult()

    # Check preconditions
    current_branch = git.get_current_branch(repo)
    if current_branch is None:
        raise SubmitError("Cannot submit in detached HEAD state")

    if git.has_uncommitted_changes(repo):
        typer.echo("Warning: You have uncommitted changes.", err=True)

    if not git.has_remote(repo, "origin"):
        raise SubmitError("No origin remote configured")

    # Get GitHub token
    token = get_github_token()
    if not token:
        raise SubmitError(
            "No GitHub token found. "
            "Run 'gh auth login' or set GH_TOKEN environment variable."
        )

    # Get repo info
    repo_info = get_repo_info(repo)
    if not repo_info:
        raise SubmitError(
            "Cannot determine GitHub repo from origin URL. "
            "Expected format: git@github.com:owner/repo.git or https://github.com/owner/repo.git"
        )
    owner, repo_name = repo_info

    # Get stack in order (bottom to top)
    stack_branches = _get_stack_in_order(repo, current_branch)
    if not stack_branches:
        # Current branch is untracked
        raise SubmitError(
            f"Branch '{current_branch}' is not tracked by shortcake. "
            "Use 'sc adopt' to track it first."
        )
    result.stack_branches = stack_branches

    if dry_run:
        typer.echo(f"Would submit {len(stack_branches)} branch(es):")
        for branch in stack_branches:
            typer.echo(f"  {branch}")
        return result

    # Track PR numbers for stack visualization
    pr_numbers: dict[str, int] = {}
    all_branches = set(git.get_all_local_branches(repo))

    with GitHubClient(token, owner, repo_name) as gh:
        # Phase 1: Push all branches and create/identify PRs
        for branch in stack_branches:
            branch_result = BranchSubmitResult(branch=branch, action=PRAction.SKIPPED)

            # Push branch
            typer.echo(f"Pushing '{branch}'...")
            if not push_branch(repo, branch):  # pragma: no cover
                branch_result.error = "Failed to push"
                result.branch_results.append(branch_result)
                continue

            # Get parent branch for PR base
            parent = git.get_branch_parent(repo, branch, all_branches)
            if parent is None:  # pragma: no cover
                branch_result.error = "No parent branch found"
                result.branch_results.append(branch_result)
                continue

            try:
                # Check if PR exists
                existing_pr = gh.get_pr_for_branch(branch)

                if existing_pr:
                    pr_numbers[branch] = existing_pr.number
                    branch_result.pr_number = existing_pr.number
                    branch_result.pr_url = existing_pr.url

                    # Update PR base if changed
                    if existing_pr.base != parent:
                        typer.echo(
                            f"  Updating PR #{existing_pr.number} base: "
                            f"{existing_pr.base} -> {parent}"
                        )
                        gh.update_pr(existing_pr.number, base=parent)
                    branch_result.action = PRAction.UPDATED
                else:
                    # Create new PR
                    title = _get_commit_title(repo, branch)
                    typer.echo(f"  Creating PR for '{branch}'...")
                    pr = gh.create_pr(
                        head=branch,
                        base=parent,
                        title=title,
                        body="",
                        draft=draft,
                    )
                    pr_numbers[branch] = pr.number
                    branch_result.pr_number = pr.number
                    branch_result.pr_url = pr.url
                    branch_result.action = PRAction.CREATED
                    typer.echo(f"  Created PR #{pr.number}: {pr.url}")

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise SubmitError(
                        "GitHub authentication failed. "
                        "Re-run 'gh auth login' or check your token."
                    ) from None
                elif e.response.status_code == 403:
                    # Check if rate limited
                    if "rate limit" in e.response.text.lower():
                        raise SubmitError(
                            "GitHub API rate limit exceeded. Please wait and try again."
                        ) from None
                    raise SubmitError(
                        f"GitHub API forbidden: {e.response.text}"
                    ) from None
                elif e.response.status_code == 422:
                    # PR may already exist or validation error
                    branch_result.error = f"GitHub API error: {e.response.text}"
                else:
                    branch_result.error = f"GitHub API error: {e.response.status_code}"
            except httpx.RequestError as e:
                branch_result.error = f"Network error: {e}"

            result.branch_results.append(branch_result)

        # Phase 2: Update all PR bodies with stack visualization
        for branch in stack_branches:
            pr_num = pr_numbers.get(branch)
            if not pr_num:
                continue

            try:
                existing_pr = gh.get_pr_for_branch(branch)
                if existing_pr:
                    stack_section = _build_stack_section(
                        stack_branches, branch, pr_numbers, owner
                    )
                    new_body = _update_pr_body_with_stack(
                        existing_pr.body, stack_section
                    )
                    gh.update_pr(pr_num, body=new_body)
            except (httpx.HTTPStatusError, httpx.RequestError):
                # Non-fatal: stack visualization update failed
                pass

    return result


def submit(
    draft: Annotated[
        bool,
        typer.Option("--draft", "-d", help="Create draft PRs"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview without making changes"),
    ] = False,
) -> None:
    """Push branches and create/update GitHub PRs for the current stack."""
    repo = git.open_repo()

    try:
        result = _submit(repo, dry_run=dry_run, draft=draft)
    except SubmitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if dry_run:
        return

    # Summary
    created = sum(1 for r in result.branch_results if r.action == PRAction.CREATED)
    updated = sum(1 for r in result.branch_results if r.action == PRAction.UPDATED)
    errors = sum(1 for r in result.branch_results if r.error)

    if created or updated:
        typer.echo()
        if created:
            typer.echo(f"Created {created} PR(s)")
        if updated:
            typer.echo(f"Updated {updated} PR(s)")

    if errors:
        typer.echo(f"{errors} error(s) occurred", err=True)
        for r in result.branch_results:
            if r.error:
                typer.echo(f"  {r.branch}: {r.error}", err=True)
        raise typer.Exit(1)

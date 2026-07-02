"""Submit command - push branches and create/update GitHub PRs."""

import contextlib
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Annotated

import httpx
import typer

from shortcake import _git as git
from shortcake._cache import update_pr_cache
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, get_github_token, get_repo_info, push_branch
from shortcake._pr_stack import (
    _parse_all_prs_from_body,
    _parse_merged_prs_from_body,
    _parse_stack_order_from_body,
    _sync_pr_descriptions_for_branches,
    _sync_stack_pr_descriptions,
)
from shortcake.commands.restack import RestackError, _get_stack_in_order, _restack


class PRAction(Enum):
    """Action taken for a branch during submit."""

    CREATED = auto()
    UPDATED = auto()
    PUSHED = auto()
    SKIPPED = auto()


@dataclass
class BranchPlan:
    """Planned action for a branch."""

    branch: str
    action: PRAction
    existing_pr_number: int | None = None
    existing_pr_url: str | None = None
    existing_pr_base: str | None = None
    parent: str | None = None


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


def _submit(
    repo: Repo,
    dry_run: bool = False,
    draft: bool = False,
    force: bool = False,
) -> SubmitResult:
    """Submit current stack to GitHub.

    Pushes branches and creates/updates PRs.

    Args:
        repo: The repository.
        dry_run: If True, preview without making changes.
        draft: If True, create draft PRs.
        force: If True, force push without lease check.

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

    # Restack before pushing (ensures branches are up-to-date with parents)
    if not dry_run:
        try:
            restack_result = _restack(repo)
            if restack_result.conflict_branch:
                raise SubmitError(
                    f"Conflict while restacking '{restack_result.conflict_branch}'. "
                    "Resolve conflicts and run 'sc continue', then re-run 'sc submit'."
                )
            if restack_result.restacked_branches:
                for branch in restack_result.restacked_branches:
                    typer.echo(f"Restacked {branch}.")
        except RestackError as e:
            raise SubmitError(str(e)) from None

    # Get stack in order (bottom to top)
    stack_branches = _get_stack_in_order(repo, current_branch)
    if not stack_branches:
        # Current branch is untracked
        raise SubmitError(
            f"Branch '{current_branch}' is not tracked by shortcake. "
            "Use 'sc adopt' to track it first."
        )
    result.stack_branches = stack_branches
    all_branches = set(git.get_all_local_branches(repo))

    # Phase 1: Build plan - check GitHub for existing PRs
    plans: list[BranchPlan] = []
    pr_numbers: dict[str, int] = {}
    stack_branches_set = set(stack_branches)

    with GitHubClient(token, owner, repo_name) as gh:
        for branch in stack_branches:
            parent = git.get_branch_parent(repo, branch, all_branches)

            # If parent was deleted locally (merged + cleaned up), resolve
            # to the branch it was merged into so we don't try to set the
            # PR base to a non-existent remote branch.
            if (
                parent
                and parent not in all_branches
                and parent not in stack_branches_set
            ):
                try:
                    merged_base = gh.get_merged_pr_base(parent)
                    if merged_base:
                        typer.echo(
                            f"Parent '{parent}' was merged into "
                            f"'{merged_base}', using as base."
                        )
                        parent = merged_base
                except (httpx.HTTPStatusError, httpx.RequestError):
                    pass

            try:
                existing_pr = gh.get_pr_for_branch(branch)
                if existing_pr:
                    plans.append(
                        BranchPlan(
                            branch=branch,
                            action=PRAction.UPDATED,
                            existing_pr_number=existing_pr.number,
                            existing_pr_url=existing_pr.url,
                            existing_pr_base=existing_pr.base,
                            parent=parent,
                        )
                    )
                    pr_numbers[branch] = existing_pr.number
                elif gh.has_merged_pr(branch):
                    plans.append(
                        BranchPlan(
                            branch=branch, action=PRAction.SKIPPED, parent=parent
                        )
                    )
                else:
                    plans.append(
                        BranchPlan(
                            branch=branch, action=PRAction.CREATED, parent=parent
                        )
                    )
            except httpx.HTTPStatusError as e:
                # Handle fatal errors during planning
                if e.response.status_code == 401:
                    raise SubmitError(
                        "GitHub authentication failed. "
                        "Re-run 'gh auth login' or check your token."
                    ) from None
                elif e.response.status_code == 403:
                    if "rate limit" in e.response.text.lower():
                        raise SubmitError(
                            "GitHub API rate limit exceeded. Please wait and try again."
                        ) from None
                    raise SubmitError(
                        f"GitHub API forbidden: {e.response.text}"
                    ) from None
                # For other errors, assume create
                plans.append(
                    BranchPlan(branch=branch, action=PRAction.CREATED, parent=parent)
                )
            except httpx.RequestError:
                # Network errors during planning - assume create
                plans.append(
                    BranchPlan(branch=branch, action=PRAction.CREATED, parent=parent)
                )

        # Collect historical PR info and stack order from existing open PRs
        # This preserves PR info even after branches are deleted locally
        historical_merged_prs: dict[str, int] = {}
        historical_prs: dict[str, int] = {}  # All PRs (including non-merged)
        historical_stack_order: list[str] = []
        for plan in plans:
            if plan.action == PRAction.UPDATED and plan.existing_pr_number:
                try:
                    existing_pr = gh.get_pr_for_branch(plan.branch)
                    if existing_pr and existing_pr.body:
                        # Parse merged PRs from this PR's body
                        parsed_merged = _parse_merged_prs_from_body(existing_pr.body)
                        for branch_name, pr_num in parsed_merged.items():
                            if branch_name not in historical_merged_prs:
                                historical_merged_prs[branch_name] = pr_num

                        # Parse all PRs (including non-merged) from this PR's body
                        parsed_all = _parse_all_prs_from_body(existing_pr.body)
                        for branch_name, pr_num in parsed_all.items():
                            if branch_name not in historical_prs:
                                historical_prs[branch_name] = pr_num

                        # Parse stack order (only if we don't have one yet)
                        if not historical_stack_order:
                            historical_stack_order = _parse_stack_order_from_body(
                                existing_pr.body
                            )
                except (httpx.HTTPStatusError, httpx.RequestError):
                    # Non-fatal: continue without historical data
                    pass

        # For historical branches not in local repo, try to look up their PRs
        # Skip branches that are already known to be merged
        local_branches = set(stack_branches)
        for hist_branch in historical_stack_order:
            if (
                hist_branch not in local_branches
                and hist_branch not in pr_numbers
                and hist_branch not in historical_merged_prs
            ):
                # First check if we already have the PR number from parsing
                if hist_branch in historical_prs:
                    pr_numbers[hist_branch] = historical_prs[hist_branch]
                else:
                    # Try to look up on GitHub
                    try:
                        existing_pr = gh.get_pr_for_branch(hist_branch)
                        if existing_pr:
                            pr_numbers[hist_branch] = existing_pr.number
                    except (httpx.HTTPStatusError, httpx.RequestError):
                        pass

        # Dry run: show plan and return
        if dry_run:
            typer.echo(f"Would submit {len(stack_branches)} branch(es):")
            for plan in plans:
                if plan.action == PRAction.UPDATED:
                    typer.echo(
                        f"  {plan.branch} (update PR #{plan.existing_pr_number})"
                    )
                elif plan.action == PRAction.SKIPPED:
                    typer.echo(f"  {plan.branch} (skip - already merged)")
                else:
                    typer.echo(f"  {plan.branch} (create new PR)")
            return result

        # Phase 2: Execute plan - push and create/update PRs
        for plan in plans:
            branch_result = BranchSubmitResult(
                branch=plan.branch, action=PRAction.SKIPPED
            )

            # Push branch
            typer.echo(f"Pushing '{plan.branch}'...")
            success, error = push_branch(repo, plan.branch, force_with_lease=not force)
            if not success:
                branch_result.error = error or "Failed to push"
                result.branch_results.append(branch_result)
                continue

            if plan.parent is None:  # pragma: no cover
                branch_result.error = "No parent branch found"
                result.branch_results.append(branch_result)
                continue

            try:
                if plan.action == PRAction.UPDATED:
                    # Update existing PR - use info from planning
                    pr_numbers[plan.branch] = plan.existing_pr_number
                    branch_result.pr_number = plan.existing_pr_number
                    branch_result.pr_url = plan.existing_pr_url

                    # Update PR base if changed
                    if plan.existing_pr_base != plan.parent:
                        typer.echo(
                            f"  Updating PR #{plan.existing_pr_number} base: "
                            f"{plan.existing_pr_base} -> {plan.parent}"
                        )
                        gh.update_pr(plan.existing_pr_number, base=plan.parent)
                    branch_result.action = PRAction.UPDATED

                    # Update cache with existing PR info
                    existing_pr = gh.get_pr_for_branch(plan.branch)
                    if existing_pr:
                        update_pr_cache(
                            repo,
                            plan.branch,
                            existing_pr.number,
                            is_draft=existing_pr.is_draft,
                            url=existing_pr.url,
                        )

                elif plan.action == PRAction.SKIPPED:
                    typer.echo(
                        f"  Skipping '{plan.branch}' - already has a merged PR. "
                        f"Run 'sc sync' to clean up merged branches."
                    )
                    branch_result.action = PRAction.SKIPPED
                    result.branch_results.append(branch_result)
                    continue

                else:  # PRAction.CREATED
                    # Create new PR
                    title = _get_commit_title(repo, plan.branch)
                    typer.echo(f"  Creating PR for '{plan.branch}'...")
                    pr = gh.create_pr(
                        head=plan.branch,
                        base=plan.parent,
                        title=title,
                        body="",
                        draft=draft,
                    )
                    pr_numbers[plan.branch] = pr.number
                    branch_result.pr_number = pr.number
                    branch_result.pr_url = pr.url
                    branch_result.action = PRAction.CREATED
                    typer.echo(f"  Created PR #{pr.number}: {pr.url}")

                    # Update cache with new PR
                    update_pr_cache(
                        repo, plan.branch, pr.number, is_draft=pr.is_draft, url=pr.url
                    )

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
                    error_text = e.response.text
                    if "was not found" in error_text and plan.parent:
                        branch_result.error = (
                            f"Base branch '{plan.parent}' not found on GitHub. "
                            f"Run 'sc sync' to clean up merged branches."
                        )
                    else:
                        branch_result.error = f"GitHub API error: {error_text}"
                else:
                    branch_result.error = f"GitHub API error: {e.response.status_code}"
            except httpx.RequestError as e:
                branch_result.error = f"Network error: {e}"

            result.branch_results.append(branch_result)

        # Phase 3: Update PR bodies for the submitted stack.
        with contextlib.suppress(httpx.HTTPStatusError, httpx.RequestError):
            _sync_stack_pr_descriptions(
                repo,
                gh,
                owner,
                stack_branches,
                pr_numbers=pr_numbers,
            )

        # Phase 4: Update PRs of branches that moved away from this stack.
        # If the old stack section listed branches that are no longer in the
        # current stack (e.g., after sc move/reorder), update those PRs with
        # their new base branch and stack visualization.
        current_stack_set = set(stack_branches)
        all_local = set(git.get_all_local_branches(repo))
        moved_away = [
            b
            for b in historical_stack_order
            if b not in current_stack_set
            and b in all_local
            and b not in historical_merged_prs
        ]

        with contextlib.suppress(httpx.HTTPStatusError, httpx.RequestError):
            _sync_pr_descriptions_for_branches(
                repo,
                gh,
                owner,
                moved_away,
                sync_bases=True,
            )

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
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force push, ignoring remote changes"),
    ] = False,
) -> None:
    """Push branches and create/update GitHub PRs for the current stack."""
    repo = git.open_repo()

    try:
        result = _submit(repo, dry_run=dry_run, draft=draft, force=force)
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

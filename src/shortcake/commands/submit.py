"""Submit command - push branches and create/update GitHub PRs."""

import re
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
from shortcake.commands.restack import RestackError, _get_stack_in_order, _restack

# Markers for stack section in PR body
STACK_START_MARKER = "<!-- shortcake:start -->"
STACK_END_MARKER = "<!-- shortcake:end -->"

# Regex patterns for parsing stack sections
# Matches: - #42 (merged) (`branch-name`)
_MERGED_PR_PATTERN = re.compile(r"-\s*#(\d+)\s*\(merged\)\s*\(`([^`]+)`\)")
# Matches any branch in stack: - #42 (`branch`) or - **#42** (`branch`)
# or - (no PR) (`branch`)
_STACK_BRANCH_PATTERN = re.compile(
    r"-\s*(?:\*\*)?(?:#\d+|#\d+\s*\(merged\)|\(no PR\))(?:\*\*)?\s*\(`([^`]+)`\)"
)
# Matches any PR number with branch: - #42 (`branch`) or - **#42** (`branch`)
# Excludes (no PR) and (merged) entries
_ALL_PR_PATTERN = re.compile(
    r"-\s*\*{0,2}#(\d+)\*{0,2}\s*\(`([^`]+)`\)(?:\s*<-- this PR)?"
)


def _parse_merged_prs_from_body(body: str) -> dict[str, int]:
    """Extract merged PR info from existing stack section.

    Parses lines like: - #42 (merged) (`branch-name`)

    Args:
        body: The PR body text.

    Returns:
        Dict mapping branch name to merged PR number.
    """
    # Extract the stack section first
    if STACK_START_MARKER not in body or STACK_END_MARKER not in body:
        return {}

    start_idx = body.index(STACK_START_MARKER)
    end_idx = body.index(STACK_END_MARKER) + len(STACK_END_MARKER)
    stack_section = body[start_idx:end_idx]

    merged_prs: dict[str, int] = {}
    for match in _MERGED_PR_PATTERN.finditer(stack_section):
        pr_number = int(match.group(1))
        branch_name = match.group(2)
        merged_prs[branch_name] = pr_number

    return merged_prs


def _parse_all_prs_from_body(body: str) -> dict[str, int]:
    """Extract all PR numbers from existing stack section.

    Parses lines like:
    - #42 (`branch-name`)
    - **#42** (`branch-name`) <-- this PR

    Args:
        body: The PR body text.

    Returns:
        Dict mapping branch name to PR number.
    """
    if STACK_START_MARKER not in body or STACK_END_MARKER not in body:
        return {}

    start_idx = body.index(STACK_START_MARKER)
    end_idx = body.index(STACK_END_MARKER) + len(STACK_END_MARKER)
    stack_section = body[start_idx:end_idx]

    all_prs: dict[str, int] = {}
    for match in _ALL_PR_PATTERN.finditer(stack_section):
        pr_number = int(match.group(1))
        branch_name = match.group(2)
        all_prs[branch_name] = pr_number

    return all_prs


def _parse_stack_order_from_body(body: str) -> list[str]:
    """Extract branch order from existing stack section.

    Returns branches in display order (top to bottom as shown in PR).

    Args:
        body: The PR body text.

    Returns:
        List of branch names in display order.
    """
    # Extract the stack section first
    if STACK_START_MARKER not in body or STACK_END_MARKER not in body:
        return []

    start_idx = body.index(STACK_START_MARKER)
    end_idx = body.index(STACK_END_MARKER) + len(STACK_END_MARKER)
    stack_section = body[start_idx:end_idx]

    branches: list[str] = []
    for match in _STACK_BRANCH_PATTERN.finditer(stack_section):
        branch_name = match.group(1)
        branches.append(branch_name)

    return branches


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


def _build_stack_section(
    stack_branches: list[str],
    current_branch: str,
    pr_numbers: dict[str, int],
    owner: str,
    merged_pr_numbers: dict[str, int] | None = None,
) -> str:
    """Build the stack visualization markdown section.

    Args:
        stack_branches: Branches in topological order (bottom to top).
        current_branch: The branch this section is being built for.
        pr_numbers: Map of branch name to open PR number.
        owner: GitHub repo owner for PR links.
        merged_pr_numbers: Map of branch name to merged PR number.

    Returns:
        Markdown string with stack visualization.
    """
    if merged_pr_numbers is None:
        merged_pr_numbers = {}

    lines = [STACK_START_MARKER, "## Stack", ""]

    # Show stack in reverse order (top to bottom) for readability
    for branch in reversed(stack_branches):
        pr_num = pr_numbers.get(branch)
        merged_num = merged_pr_numbers.get(branch)

        if pr_num:
            pr_ref = f"#{pr_num}"
        elif merged_num:
            pr_ref = f"#{merged_num} (merged)"
        else:
            pr_ref = "(no PR)"

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
                    pr_numbers[plan.branch] = plan.existing_pr_number  # type: ignore
                    branch_result.pr_number = plan.existing_pr_number
                    branch_result.pr_url = plan.existing_pr_url

                    # Update PR base if changed
                    if plan.existing_pr_base != plan.parent:
                        typer.echo(
                            f"  Updating PR #{plan.existing_pr_number} base: "
                            f"{plan.existing_pr_base} -> {plan.parent}"
                        )
                        gh.update_pr(plan.existing_pr_number, base=plan.parent)  # type: ignore
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

        # Phase 2: Collect merged PR numbers for stack visualization
        # Start with historical merged PRs (from existing PR bodies)
        merged_pr_numbers: dict[str, int] = dict(historical_merged_prs)
        # Also check GitHub API for merged PRs of local branches
        for branch in stack_branches:
            if branch not in pr_numbers and branch not in merged_pr_numbers:
                try:
                    merged_num = gh.get_merged_pr_number(branch)
                    if merged_num:
                        merged_pr_numbers[branch] = merged_num
                except (httpx.HTTPStatusError, httpx.RequestError):
                    pass

        # Phase 3: Update all PR bodies with stack visualization
        # Merge historical stack order with current local branches
        # Historical order is in display order (top to bottom), need to reverse
        # for our internal representation (bottom to top)
        full_stack_branches = list(stack_branches)  # Start with local branches
        if historical_stack_order:
            # Historical order is display order (top to bottom)
            # Reverse to get bottom to top order
            historical_bottom_to_top = list(reversed(historical_stack_order))
            # Add historical branches that are no longer local (merged and deleted)
            for hist_branch in historical_bottom_to_top:
                if hist_branch not in full_stack_branches:
                    # Find position based on historical order
                    # Insert at the position it would have been
                    inserted = False
                    for i, local_branch in enumerate(full_stack_branches):
                        if local_branch in historical_bottom_to_top:
                            local_pos = historical_bottom_to_top.index(local_branch)
                            hist_pos = historical_bottom_to_top.index(hist_branch)
                            if hist_pos < local_pos:
                                full_stack_branches.insert(i, hist_branch)
                                inserted = True
                                break
                    if not inserted:
                        # Append at the end if no better position found
                        full_stack_branches.append(hist_branch)

        for branch in stack_branches:
            pr_num = pr_numbers.get(branch)
            if not pr_num:
                continue

            try:
                existing_pr = gh.get_pr_for_branch(branch)
                if existing_pr:
                    stack_section = _build_stack_section(
                        full_stack_branches,
                        branch,
                        pr_numbers,
                        owner,
                        merged_pr_numbers,
                    )
                    new_body = _update_pr_body_with_stack(
                        existing_pr.body, stack_section
                    )
                    gh.update_pr(pr_num, body=new_body)
            except (httpx.HTTPStatusError, httpx.RequestError):
                # Non-fatal: stack visualization update failed
                pass

        # Phase 4: Update PRs of branches that moved away from this stack.
        # If the old stack section listed branches that are no longer in the
        # current stack (e.g., after sc move/reorder), update those PRs with
        # their new stack visualization.
        current_stack_set = set(stack_branches)
        all_local = set(git.get_all_local_branches(repo))
        moved_away = [
            b
            for b in historical_stack_order
            if b not in current_stack_set
            and b in all_local
            and b not in merged_pr_numbers
        ]

        for branch in moved_away:
            try:
                existing_pr = gh.get_pr_for_branch(branch)
                if not existing_pr:  # pragma: no cover
                    continue

                # Compute this branch's new stack
                new_stack = _get_stack_in_order(repo, branch)
                if not new_stack:  # pragma: no cover
                    # Branch is now untracked — clear the stack section
                    new_body = _update_pr_body_with_stack(
                        existing_pr.body,
                        f"{STACK_START_MARKER}\n{STACK_END_MARKER}",
                    )
                    gh.update_pr(existing_pr.number, body=new_body)
                    continue

                # Collect PR numbers for the new stack
                new_stack_pr_numbers: dict[str, int] = {}
                for b in new_stack:
                    try:
                        pr = gh.get_pr_for_branch(b)
                        if pr:
                            new_stack_pr_numbers[b] = pr.number
                    except (
                        httpx.HTTPStatusError,
                        httpx.RequestError,
                    ):  # pragma: no cover
                        pass

                stack_section = _build_stack_section(
                    new_stack,
                    branch,
                    new_stack_pr_numbers,
                    owner,
                )
                new_body = _update_pr_body_with_stack(existing_pr.body, stack_section)
                gh.update_pr(existing_pr.number, body=new_body)
            except (httpx.HTTPStatusError, httpx.RequestError):  # pragma: no cover
                # Non-fatal: moved branch PR update failed
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

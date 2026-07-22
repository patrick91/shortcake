"""Submit command - push branches and create/update GitHub PRs."""

import contextlib
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Annotated

import httpx
import typer
from rich.style import Style
from rich.text import Text

from shortcake import _git as git
from shortcake._cache import update_pr_cache
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, get_github_token, get_repo_info, push_branch
from shortcake._output import ShortcakeRichToolkit, get_rich_toolkit
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
    planned: list[BranchPlan] = field(default_factory=list)


class SubmitError(ShortcakeError):
    """Error during submit operation."""

    pass


def _is_interactive() -> bool:
    """Return whether submit may safely ask an interactive question."""
    return sys.stdin.isatty()


def _get_commit_title(repo: Repo, branch: str) -> str:
    """Get the first line of the first commit message on a branch."""
    sha = git.get_branch_head(repo, branch)
    message = git.get_commit_message(repo, sha)
    first_line = message.partition("\n")[0].strip()
    return first_line


def _get_downstack_in_order(
    repo: Repo, current_branch: str, full_stack_branches: list[str]
) -> list[str]:
    """Return the current branch and its tracked ancestors, bottom first."""
    stack_set = set(full_stack_branches)
    all_branches = set(git.get_all_local_branches(repo))
    branch_heads = {
        branch: git.get_branch_head(repo, branch) for branch in all_branches
    }
    downstack: list[str] = []
    visited: set[str] = set()
    branch = current_branch

    while branch in stack_set and branch not in visited:
        visited.add(branch)
        downstack.append(branch)
        parent = git.get_branch_parent(repo, branch, all_branches, branch_heads)
        if parent not in stack_set:
            break
        branch = parent

    downstack.reverse()
    return downstack


def _show_submit_plan(
    repo: Repo,
    toolkit: ShortcakeRichToolkit,
    full_stack_branches: list[str],
    selected_branches: list[str],
    current_branch: str,
    *,
    heading: str = "Submit plan",
    plans: list[BranchPlan] | None = None,
) -> None:
    """Show the full stack as a downward tree with planned PR actions."""
    if not full_stack_branches:
        return

    selected = set(selected_branches)
    plans_by_branch = {plan.branch: plan for plan in plans or []}
    all_branches = set(git.get_all_local_branches(repo))
    branch_heads = {
        branch: git.get_branch_head(repo, branch) for branch in all_branches
    }
    stack_set = set(full_stack_branches)
    parents = {
        branch: git.get_branch_parent(repo, branch, all_branches, branch_heads)
        for branch in full_stack_branches
    }
    children: dict[str, list[str]] = {branch: [] for branch in full_stack_branches}
    roots: list[str] = []
    for branch in full_stack_branches:
        parent = parents[branch]
        if parent in stack_set:
            children[parent].append(branch)
        else:
            roots.append(branch)

    order = {branch: index for index, branch in enumerate(full_stack_branches)}
    for branch_children in children.values():
        branch_children.sort(key=order.__getitem__)

    def branch_label(branch: str) -> Text:
        marker = "◉" if branch == current_branch else "●"
        if branch not in selected:
            marker = "◯"
        current = " (current)" if branch == current_branch else ""
        label = Text(f"{marker} {branch}{current}")

        if branch not in selected:
            label.append(" — not submitted")
            label.stylize("dim")
            return label

        plan = plans_by_branch.get(branch)
        if plan is None:
            return label
        if plan.action == PRAction.UPDATED:
            label.append(" — update PR ")
            pr_style = Style(color="cyan", underline=True)
            if plan.existing_pr_url:
                pr_style += Style(link=plan.existing_pr_url)
            label.append(f"#{plan.existing_pr_number}", style=pr_style)
        elif plan.action == PRAction.CREATED:
            label.append(" — create PR")
        elif plan.action == PRAction.SKIPPED:
            label.append(" — skip; already merged", style="dim")
        else:
            label.append(" — push only")
        return label

    def render_branch(
        branch: str,
        prefix: str = "  ",
        connector: str = "",
        continuation: str = "  ",
    ) -> None:
        line = Text(f"{prefix}{connector}")
        line.append_text(branch_label(branch))
        toolkit.print(line)
        branch_children = children[branch]
        if len(branch_children) == 1:
            child = branch_children[0]
            connector_line = Text(f"{continuation}│")
            if child not in selected:
                connector_line.stylize("dim")
            toolkit.print(connector_line)
            render_branch(child, continuation, continuation=continuation)
            return

        for index, child in enumerate(branch_children):
            is_last = index == len(branch_children) - 1
            child_connector = "└─" if is_last else "├─"
            child_continuation = continuation + ("  " if is_last else "│ ")
            render_branch(
                child,
                continuation,
                child_connector,
                child_continuation,
            )

    toolkit.echo(f"{heading}:")
    toolkit.echo()
    base = parents[roots[0]] if roots else None
    if base is not None:
        toolkit.print(Text(f"  ◯ {base} (base)"))

    for index, root in enumerate(roots):
        if base is not None:
            toolkit.print(Text("  │"))
        root_connector = (
            "" if len(roots) == 1 else ("└─" if index == len(roots) - 1 else "├─")
        )
        render_branch(root, connector=root_connector)

    toolkit.echo()
    selected_count = len(selected_branches)
    excluded_count = len(full_stack_branches) - selected_count
    if excluded_count:
        excluded_label = "branch" if excluded_count == 1 else "branches"
        toolkit.echo(
            f"● {selected_count} selected · "
            f"○ {excluded_count} upstack {excluded_label} not selected"
        )
    else:
        toolkit.echo(f"● {selected_count} selected")
    toolkit.echo()


def _get_submit_github_details(repo: Repo) -> tuple[str, str, str]:
    """Return the token, owner, and repository used for submission."""
    token = get_github_token()
    if not token:
        raise SubmitError(
            "No GitHub token found. "
            "Run 'gh auth login' or set GH_TOKEN environment variable."
        )

    repo_info = get_repo_info(repo)
    if not repo_info:
        raise SubmitError(
            "Cannot determine GitHub repo from origin URL. "
            "Expected format: git@github.com:owner/repo.git or https://github.com/owner/repo.git"
        )
    owner, repo_name = repo_info
    return token, owner, repo_name


def _build_branch_plans(
    repo: Repo,
    gh: GitHubClient,
    toolkit: ShortcakeRichToolkit,
    branches: list[str],
    full_stack_branches: list[str],
) -> list[BranchPlan]:
    """Inspect GitHub and build the action planned for each branch."""
    plans: list[BranchPlan] = []
    all_branches = set(git.get_all_local_branches(repo))
    stack_branches_set = set(full_stack_branches)

    for branch in branches:
        parent = git.get_branch_parent(repo, branch, all_branches)

        # If parent was deleted locally (merged + cleaned up), resolve to the
        # branch it was merged into so the PR does not use an invalid base.
        if parent and parent not in all_branches and parent not in stack_branches_set:
            try:
                merged_base = gh.get_merged_pr_base(parent)
                if merged_base:
                    toolkit.echo(
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
            elif gh.has_merged_pr(branch):
                plans.append(
                    BranchPlan(branch=branch, action=PRAction.SKIPPED, parent=parent)
                )
            else:
                plans.append(
                    BranchPlan(branch=branch, action=PRAction.CREATED, parent=parent)
                )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise SubmitError(
                    "GitHub authentication failed. "
                    "Re-run 'gh auth login' or check your token."
                ) from None
            if e.response.status_code == 403:
                if "rate limit" in e.response.text.lower():
                    raise SubmitError(
                        "GitHub API rate limit exceeded. Please wait and try again."
                    ) from None
                raise SubmitError(f"GitHub API forbidden: {e.response.text}") from None
            plans.append(
                BranchPlan(branch=branch, action=PRAction.CREATED, parent=parent)
            )
        except httpx.RequestError:
            plans.append(
                BranchPlan(branch=branch, action=PRAction.CREATED, parent=parent)
            )

    return plans


def _load_submit_plans(
    repo: Repo,
    toolkit: ShortcakeRichToolkit,
    branches: list[str],
    full_stack_branches: list[str],
) -> list[BranchPlan]:
    """Build a live GitHub plan for the pre-submit visualization."""
    token, owner, repo_name = _get_submit_github_details(repo)
    with GitHubClient(token, owner, repo_name) as gh:
        return _build_branch_plans(repo, gh, toolkit, branches, full_stack_branches)


def _submit(
    repo: Repo,
    submit_stack: bool = False,
    dry_run: bool = False,
    draft: bool = False,
    force: bool = False,
    stealth: bool = False,
    show_plan: bool = True,
    precomputed_plans: list[BranchPlan] | None = None,
    toolkit: ShortcakeRichToolkit | None = None,
) -> SubmitResult:
    """Submit through the current branch or submit its whole stack to GitHub.

    Pushes branches and creates/updates PRs.

    Args:
        repo: The repository.
        submit_stack: If True, also submit branches above the current branch.
        dry_run: If True, preview without making changes.
        draft: If True, create draft PRs.
        force: If True, force push without lease check.
        stealth: If True, push branches without creating or updating PRs.
        show_plan: If True, print the selected branches before acting.
        precomputed_plans: Live plans already fetched for the preview.

    Returns:
        SubmitResult with per-branch results.

    Raises:
        SubmitError: On precondition failures.
    """
    toolkit = toolkit or get_rich_toolkit()
    result = SubmitResult()

    # Check preconditions
    current_branch = git.get_current_branch(repo)
    if current_branch is None:
        raise SubmitError("Cannot submit in detached HEAD state")

    if git.has_uncommitted_changes(repo):
        typer.echo("Warning: You have uncommitted changes.", err=True)

    if not git.has_remote(repo, "origin"):
        raise SubmitError("No origin remote configured")

    if stealth and draft:
        raise SubmitError("--draft cannot be used with --stealth")

    full_stack_branches = _get_stack_in_order(repo, current_branch)
    if not full_stack_branches:
        # Current branch is untracked
        raise SubmitError(
            f"Branch '{current_branch}' is not tracked by shortcake. "
            "Use 'sc adopt' to track it first."
        )

    stack_branches = (
        full_stack_branches
        if submit_stack
        else _get_downstack_in_order(repo, current_branch, full_stack_branches)
    )

    result.stack_branches = full_stack_branches

    if not dry_run and show_plan and (stealth or precomputed_plans is not None):
        display_plans = precomputed_plans
        if stealth:
            display_plans = [
                BranchPlan(branch=branch, action=PRAction.PUSHED)
                for branch in stack_branches
            ]
        _show_submit_plan(
            repo,
            toolkit,
            full_stack_branches,
            stack_branches,
            current_branch,
            heading="Push plan" if stealth else "Submit plan",
            plans=display_plans,
        )

    # Restack before pushing (ensures branches are up-to-date with parents)
    if not dry_run:
        try:
            if submit_stack:
                restack_result = _restack(repo, toolkit=toolkit)
            else:
                restack_result = _restack(
                    repo,
                    toolkit=toolkit,
                    branches=stack_branches,
                )
            if restack_result.conflict_branch:
                raise SubmitError(
                    f"Conflict while restacking '{restack_result.conflict_branch}'. "
                    "Resolve conflicts and run 'sc continue', then re-run 'sc submit'."
                )
            if restack_result.restacked_branches:
                for branch in restack_result.restacked_branches:
                    toolkit.echo(f"Restacked {branch}.")
        except RestackError as e:
            raise SubmitError(str(e)) from None

    if stealth:
        if dry_run:
            toolkit.echo(
                f"Would push {len(stack_branches)} branch(es) without creating PRs:"
            )
            for branch in stack_branches:
                toolkit.echo(f"  {branch} (push only)")
            result.planned = [
                BranchPlan(branch=branch, action=PRAction.PUSHED)
                for branch in stack_branches
            ]
            return result

        for branch in stack_branches:
            branch_result = BranchSubmitResult(branch=branch, action=PRAction.SKIPPED)

            toolkit.echo(f"Pushing '{branch}'...")
            success, error = push_branch(repo, branch, force_with_lease=not force)
            if not success:
                branch_result.error = error or "Failed to push"
            else:
                branch_result.action = PRAction.PUSHED
            result.branch_results.append(branch_result)

        return result

    token, owner, repo_name = _get_submit_github_details(repo)

    with GitHubClient(token, owner, repo_name) as gh:
        if precomputed_plans is None:
            plans = _build_branch_plans(
                repo, gh, toolkit, stack_branches, full_stack_branches
            )
        else:
            plans_by_branch = {plan.branch: plan for plan in precomputed_plans}
            missing_branches = [
                branch for branch in stack_branches if branch not in plans_by_branch
            ]
            if missing_branches:
                for plan in _build_branch_plans(
                    repo, gh, toolkit, missing_branches, full_stack_branches
                ):
                    plans_by_branch[plan.branch] = plan
            plans = [plans_by_branch[branch] for branch in stack_branches]

        pr_numbers = {
            plan.branch: plan.existing_pr_number
            for plan in plans
            if plan.existing_pr_number is not None
        }

        if not dry_run and show_plan and precomputed_plans is None:
            _show_submit_plan(
                repo,
                toolkit,
                full_stack_branches,
                stack_branches,
                current_branch,
                plans=plans,
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
        local_branches = set(full_stack_branches)
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
            toolkit.echo(f"Would submit {len(stack_branches)} branch(es):")
            for plan in plans:
                if plan.action == PRAction.UPDATED:
                    toolkit.echo(
                        f"  {plan.branch} (update PR #{plan.existing_pr_number})"
                    )
                elif plan.action == PRAction.SKIPPED:
                    toolkit.echo(f"  {plan.branch} (skip - already merged)")
                else:
                    toolkit.echo(f"  {plan.branch} (create new PR)")
            result.planned = plans
            return result

        # Phase 2: Execute plan - push and create/update PRs
        for plan in plans:
            branch_result = BranchSubmitResult(
                branch=plan.branch, action=PRAction.SKIPPED
            )

            # Push branch
            toolkit.echo(f"Pushing '{plan.branch}'...")
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
                        toolkit.echo(
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
                    toolkit.echo(
                        f"  Skipping '{plan.branch}' - already has a merged PR. "
                        f"Run 'sc sync' to clean up merged branches."
                    )
                    branch_result.action = PRAction.SKIPPED
                    result.branch_results.append(branch_result)
                    continue

                else:  # PRAction.CREATED
                    # Create new PR
                    title = _get_commit_title(repo, plan.branch)
                    toolkit.echo(f"  Creating PR for '{plan.branch}'...")
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
                    toolkit.echo(f"  Created PR #{pr.number}: {pr.url}")

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
        if submit_stack:
            current_stack_set = set(full_stack_branches)
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
    stack: Annotated[
        bool,
        typer.Option("--stack", help="Submit every branch in the current stack"),
    ] = False,
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
    stealth: Annotated[
        bool,
        typer.Option(
            "--stealth", help="Push branches without creating or updating PRs"
        ),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Output the result as JSON")
    ] = False,
) -> None:
    """Submit through the current diff, or use --stack for the whole stack."""
    repo = git.open_repo()
    toolkit = get_rich_toolkit(json_output=json_output)

    preview_plans: list[BranchPlan] | None = None
    try:
        current_branch = git.get_current_branch(repo)
        if current_branch is not None:
            stack_branches = _get_stack_in_order(repo, current_branch)
            downstack_branches = _get_downstack_in_order(
                repo, current_branch, stack_branches
            )
            selected_branches = stack_branches if stack else downstack_branches
            if not json_output and stack_branches:
                if stealth:
                    preview_plans = [
                        BranchPlan(branch=branch, action=PRAction.PUSHED)
                        for branch in stack_branches
                    ]
                else:
                    preview_plans = _load_submit_plans(
                        repo,
                        toolkit,
                        selected_branches,
                        stack_branches,
                    )
                _show_submit_plan(
                    repo,
                    toolkit,
                    stack_branches,
                    selected_branches,
                    current_branch,
                    heading="Push plan" if stealth else "Submit plan",
                    plans=preview_plans,
                )
            if (
                not stack
                and not json_output
                and _is_interactive()
                and len(downstack_branches) < len(stack_branches)
            ):
                prompt = (
                    "Also submit upstack branches "
                    f"({len(stack_branches)} branches total)?"
                )
                stack = typer.confirm(
                    prompt,
                    default=False,
                )
                if stack:
                    planned_branches = {plan.branch for plan in preview_plans or []}
                    missing_branches = [
                        branch
                        for branch in stack_branches
                        if branch not in planned_branches
                    ]
                    if not stealth and missing_branches:
                        preview_plans = (preview_plans or []) + _load_submit_plans(
                            repo,
                            toolkit,
                            missing_branches,
                            stack_branches,
                        )
                    _show_submit_plan(
                        repo,
                        toolkit,
                        stack_branches,
                        stack_branches,
                        current_branch,
                        heading="Updated submit plan",
                        plans=preview_plans,
                    )

        result = _submit(
            repo,
            submit_stack=stack,
            dry_run=dry_run,
            draft=draft,
            force=force,
            stealth=stealth,
            show_plan=False,
            precomputed_plans=preview_plans,
            toolkit=toolkit,
        )
    except SubmitError as e:
        toolkit.fail("submit_failed", str(e))

    if json_output:
        action_names = {
            PRAction.CREATED: "created",
            PRAction.UPDATED: "updated",
            PRAction.PUSHED: "pushed",
            PRAction.SKIPPED: "skipped",
        }
        toolkit.success(
            {
                "stack": result.stack_branches,
                "branches": [
                    {
                        "branch": r.branch,
                        "action": action_names[r.action],
                        "pr": r.pr_number,
                        "url": r.pr_url,
                        "error": r.error,
                    }
                    for r in result.branch_results
                ],
                "planned": [
                    {
                        "branch": plan.branch,
                        "action": action_names[plan.action],
                        "pr": plan.existing_pr_number,
                    }
                    for plan in result.planned
                ],
            }
        )
        if any(r.error for r in result.branch_results):
            raise typer.Exit(1)
        return

    if dry_run:
        return

    # Summary
    created = sum(1 for r in result.branch_results if r.action == PRAction.CREATED)
    updated = sum(1 for r in result.branch_results if r.action == PRAction.UPDATED)
    pushed = sum(1 for r in result.branch_results if r.action == PRAction.PUSHED)
    errors = sum(1 for r in result.branch_results if r.error)

    if created or updated or pushed:
        typer.echo()
        if created:
            typer.echo(f"Created {created} PR(s)")
        if updated:
            typer.echo(f"Updated {updated} PR(s)")
        if pushed:
            typer.echo(f"Pushed {pushed} branch(es)")

    if errors:
        typer.echo(f"{errors} error(s) occurred", err=True)
        for r in result.branch_results:
            if r.error:
                typer.echo(f"  {r.branch}: {r.error}", err=True)
        raise typer.Exit(1)

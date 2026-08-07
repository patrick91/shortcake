"""Submit command - push branches and create/update GitHub PRs."""

import contextlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Annotated

import httpx
import typer
from rich.style import Style
from rich.text import Text

from shortcake import _git as git
from shortcake._cache import load_pr_cache, update_pr_cache
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, get_github_token, get_repo_info, push_branch
from shortcake._native_stack import (
    NATIVE_STACK_MINIMUM_MESSAGE,
    NativeStackAction,
    NativeStackPreparationAction,
    NativeStackSyncResult,
    fallback_native_stack,
    prepare_native_stack_restructure,
    reconcile_native_stack,
)
from shortcake._output import ShortcakeRichToolkit, get_rich_toolkit
from shortcake._pr_stack import (
    _parse_all_prs_from_body,
    _parse_merged_prs_from_body,
    _parse_stack_order_from_body,
    _remove_stack_pr_descriptions,
    _sync_pr_descriptions_for_branches,
    _sync_stack_pr_descriptions,
)
from shortcake._stack_view import (
    DIM,
    RowState,
    StackRenderer,
    StackRow,
)
from shortcake.commands._submit_picker import pick_scope
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
    native_stack_number: int | None = None
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
    native_stack: NativeStackSyncResult | None = None


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


def _plan_label(plan: BranchPlan | None, *, draft: bool = False) -> Text:
    """Status column for a branch in the plan tree."""
    if plan is None:
        return Text("")
    if plan.action == PRAction.UPDATED:
        label = Text("update PR ", style=DIM)
        pr_style = Style(color="cyan", underline=True)
        if plan.existing_pr_url:
            pr_style += Style(link=plan.existing_pr_url)
        label.append(f"#{plan.existing_pr_number}", style=pr_style)
        return label
    if plan.action == PRAction.SKIPPED:
        return Text("merged", style=DIM)
    if plan.action == PRAction.PUSHED:
        return Text("push only", style=DIM)
    return Text("create draft PR" if draft else "create PR", style=DIM)


def _build_stack_rows(
    repo: Repo,
    full_stack_branches: list[str],
    selected_branches: list[str],
    current_branch: str | None,
    *,
    plans: list[BranchPlan] | None = None,
    draft: bool = False,
) -> list[StackRow]:
    """Turn the stack into renderable rows, base first.

    The parent links come straight from the trailers, so the shared layout
    reproduces forks without submit needing its own tree walk.
    """
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

    rows: list[StackRow] = []
    bases = [
        parents[branch]
        for branch in full_stack_branches
        if parents[branch] not in stack_set and parents[branch] is not None
    ]
    if bases:
        rows.append(
            StackRow(bases[0], state=RowState.BASE, label=Text("(base)", style=DIM))
        )

    for branch in full_stack_branches:
        parent = parents[branch]
        included = branch in selected
        rows.append(
            StackRow(
                branch,
                parent=parent if parent in stack_set else (bases[0] if bases else None),
                state=RowState.PENDING if included else RowState.EXCLUDED,
                label=(
                    _plan_label(plans_by_branch.get(branch), draft=draft)
                    if included
                    else Text("not submitted", style=DIM)
                ),
                is_current=branch == current_branch,
            )
        )
    return rows


def _show_submit_plan(
    repo: Repo,
    toolkit: ShortcakeRichToolkit,
    full_stack_branches: list[str],
    selected_branches: list[str],
    current_branch: str,
    *,
    heading: str = "Submit plan",
    plans: list[BranchPlan] | None = None,
    draft: bool = False,
) -> None:
    """Show the full stack as a downward tree with planned PR actions."""
    if not full_stack_branches:
        return

    rows = _build_stack_rows(
        repo,
        full_stack_branches,
        selected_branches,
        current_branch,
        plans=plans,
        draft=draft,
    )
    renderer = StackRenderer(rows, heading, toolkit.console, planning=True)

    toolkit.echo(f"{heading}:")
    toolkit.echo()
    for line in renderer.tree_lines():
        toolkit.print(line)

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


def _stack_forks(repo: Repo, stack_branches: list[str]) -> bool:
    """True when some branch in the stack has more than one child.

    `--stack` walks the stack root's whole tree, so on a fork it sweeps in
    sibling arms you may not have meant to submit.
    """
    all_branches = set(git.get_all_local_branches(repo))
    branch_heads = {
        branch: git.get_branch_head(repo, branch) for branch in all_branches
    }
    stack_set = set(stack_branches)
    counts: dict[str, int] = {}
    for branch in stack_branches:
        parent = git.get_branch_parent(repo, branch, all_branches, branch_heads)
        if parent in stack_set:
            counts[parent] = counts.get(parent, 0) + 1
    return any(count > 1 for count in counts.values())


def _should_ask_scope(
    stack_branches: list[str],
    downstack_branches: list[str],
    *,
    stack: bool,
    json_output: bool,
    interactive: bool,
    forks: bool,
) -> bool:
    """Whether to ask what to submit.

    Without ``--stack``: when there is upstack work you might also want. With
    ``--stack``: only on a fork, where it quietly sweeps in a sibling arm.
    Never without a TTY — a pipe or CI takes the flags at face value rather
    than hanging on a prompt.
    """
    if json_output or not interactive or not stack_branches:
        return False
    if stack:
        return forks
    return len(downstack_branches) < len(stack_branches)


def _ask_scope(
    repo: Repo,
    toolkit: ShortcakeRichToolkit,
    stack_branches: list[str],
    downstack_branches: list[str],
    current_branch: str,
    *,
    stack: bool,
    stealth: bool,
    draft: bool,
) -> tuple[str, list[str]]:
    """Run the picker; returns the chosen scope and its branches."""
    rows = _build_stack_rows(
        repo,
        stack_branches,
        stack_branches if stack else downstack_branches,
        current_branch,
        draft=draft,
    )
    by_branch = {row.branch: row for row in rows}
    # The preview labels upstack rows too, so the whole stack is looked up —
    # one API call per branch. That runs with the tree already on screen, each
    # row showing "checking…" until its own answer lands.
    labels: dict[str, Text] = {}
    resting: dict[str, RowState] = {}

    def load_plans(redraw: Callable[[], None]) -> None:
        def on_lookup(branch: str, plan: BranchPlan | None) -> None:
            row = by_branch[branch]
            if plan is None:
                resting[branch] = row.state
                row.state = RowState.ACTIVE
                row.label = Text("checking…", style=DIM)
            else:
                labels[branch] = _plan_label(plan, draft=draft)
                row.state = resting.pop(branch, RowState.PENDING)
                row.label = (
                    labels[branch]
                    if row.state is RowState.PENDING
                    else Text("not submitted", style=DIM)
                )
            redraw()

        if stealth:
            for branch in stack_branches:
                on_lookup(branch, None)
                on_lookup(branch, BranchPlan(branch=branch, action=PRAction.PUSHED))
            return
        _load_submit_plans(
            repo, toolkit, stack_branches, stack_branches, progress=on_lookup
        )

    header = (
        _submit_header(len(stack_branches), None, draft=draft, stealth=stealth)
        .replace("Submitting", "Submit plan ·")
        .replace("Pushing", "Push plan ·")
    )

    scope = pick_scope(
        toolkit.console,
        rows,
        header,
        len(downstack_branches),
        stack=stack,
        labels=labels,
        load_plans=load_plans,
    )
    return scope, [row.branch for row in rows if row.state is RowState.PENDING]


def _plan_heading(count: int, *, draft: bool) -> str:
    """Header for the first frame, while the block still shows the plan."""
    noun = "branch" if count == 1 else "branches"
    parts = [f"Submit plan · {count} {noun}"]
    if draft:
        parts.append("draft")
    return " · ".join(parts)


def _submit_header(
    count: int, target: str | None, *, draft: bool, stealth: bool
) -> str:
    """Header line: states the repo and draft-ness, both invisible before."""
    noun = "branch" if count == 1 else "branches"
    verb = "Pushing" if stealth else "Submitting"
    parts = [f"{verb} {count} {noun}"]
    if target:
        parts[0] += f" to {target}"
    if draft and not stealth:
        parts.append("draft")
    return " · ".join(parts)


def _rows_for_execution(
    repo: Repo,
    full_stack_branches: list[str],
    stack_branches: list[str],
    current_branch: str | None,
    *,
    plans: list[BranchPlan] | None = None,
    draft: bool = False,
) -> tuple[list[StackRow], dict[str, StackRow]]:
    """Rows for the live tree, plus a branch -> row lookup for the loop.

    Seeded with the plan labels so the first frame *is* the plan; it then fills
    in as work happens rather than being reprinted as a second tree.
    """
    rows = _build_stack_rows(
        repo,
        full_stack_branches,
        stack_branches,
        current_branch,
        plans=plans,
        draft=draft,
    )
    if plans is None:
        for row in rows:
            if row.state is RowState.PENDING:
                row.label = Text("")
    return rows, {row.branch: row for row in rows}


def _start_execution(renderer: StackRenderer, header: str) -> None:
    """Switch the block from showing the plan to reporting progress."""
    renderer.planning = False
    renderer.header = header
    renderer.started_at = time.monotonic()
    for row in renderer.rows:
        if row.state is RowState.PENDING:
            row.label = Text("")


def _pr_label(number: int | None, url: str | None, note: str | None = None) -> Text:
    """Hyperlinked #N, with an optional dim note such as a base change."""
    style = Style(color="cyan")
    if url:
        style += Style(link=url)
    label = Text(f"#{number}", style=style)
    if note:
        label.append(f" {note}", style=DIM)
    return label


def _submit_footer(
    result: SubmitResult,
    renderer: StackRenderer,
    *,
    draft: bool,
    excluded: int,
) -> list[Text]:
    """Result summary: counts, then the link you actually want to click."""
    counts = {
        action: sum(1 for r in result.branch_results if r.action == action)
        for action in PRAction
    }
    errors = [r for r in result.branch_results if r.error]
    elapsed = int(time.monotonic() - renderer.started_at)

    if errors:
        done = sum(counts[a] for a in (PRAction.CREATED, PRAction.UPDATED))
        done += counts[PRAction.PUSHED]
        head = Text("✗ ", style=Style(color="red"))
        head.append(f"{done} of {done + len(errors)} branches submitted · ")
        head.append(f"{len(errors)} failed", style=Style(color="red"))
        lines = [head, Text("")]
        for failed in errors:
            line = Text("  ")
            line.append(failed.branch, style=Style(color="red"))
            line.append(f"  {failed.error}", style=DIM)
            lines.append(line)
        return lines

    bits = []
    if counts[PRAction.CREATED]:
        noun = "PR" if counts[PRAction.CREATED] == 1 else "PRs"
        kind = f"draft {noun}" if draft else noun
        bits.append(f"{counts[PRAction.CREATED]} {kind} created")
    if counts[PRAction.UPDATED]:
        bits.append(f"{counts[PRAction.UPDATED]} updated")
    if counts[PRAction.PUSHED]:
        noun = "branch" if counts[PRAction.PUSHED] == 1 else "branches"
        bits.append(f"{counts[PRAction.PUSHED]} {noun} pushed")
    if counts[PRAction.SKIPPED]:
        bits.append(f"{counts[PRAction.SKIPPED]} merged")
    if not bits:  # pragma: no cover - guarded by callers
        return []

    head = Text("✓ ", style=Style(color="green"))
    head.append(" · ".join(bits))
    if elapsed:
        # Sub-second runs would just print "· 0s"; it is noise, and it makes
        # the e2e snapshots depend on timing.
        head.append(f" · {elapsed}s", style=DIM)
    lines = [head]

    # A fork has more than one tip, so "top of stack" is not a single branch.
    parents = {row.parent for row in renderer.rows if row.parent}
    submitted = {r.branch: r for r in result.branch_results if r.pr_number}
    tips = [
        submitted[row.branch]
        for row in renderer.rows
        if row.branch in submitted and row.branch not in parents
    ]
    if len(tips) == 1:
        lines.append(Text(""))
        link = Text("  Top of stack  ", style=DIM)
        link.append_text(_pr_label(tips[0].pr_number, tips[0].pr_url))
        lines.append(link)
        lines.append(Text(f"  {tips[0].pr_url}", style=DIM))
    elif tips:
        lines.append(Text(""))
        lines.append(Text(f"  {len(tips)} tips", style=DIM))
        for tip in tips:
            line = Text("  ")
            line.append_text(_pr_label(tip.pr_number, tip.pr_url))
            line.append(f"  {tip.branch}", style=DIM)
            lines.append(line)

    if excluded:
        noun = "branch" if excluded == 1 else "branches"
        lines.append(Text(""))
        note = Text()
        note.append(f"  {excluded} upstack {noun} not submitted · ", style=DIM)
        note.append("sc submit --stack", style=Style(bold=True))
        note.append(" for the whole stack", style=DIM)
        lines.append(note)
    return lines


def _report_native_stack(
    toolkit: ShortcakeRichToolkit,
    native: NativeStackSyncResult,
) -> None:
    """Render the native GitHub stack outcome after branch submission."""
    if native.synced:
        action = {
            NativeStackAction.CREATED: "created",
            NativeStackAction.UPDATED: "updated",
            NativeStackAction.RECREATED: "recreated",
            NativeStackAction.UNCHANGED: "already up to date",
        }[native.action]
        toolkit.echo(f"GitHub stack #{native.stack_number} {action}.")
        return

    if (
        native.action == NativeStackAction.FALLBACK
        and native.message == NATIVE_STACK_MINIMUM_MESSAGE
    ):
        return

    if native.message:
        toolkit.echo(f"GitHub stack: {native.message}", err=True)


def _with_pr_body_fallback(
    native: NativeStackSyncResult,
) -> NativeStackSyncResult:
    """Add the submit fallback consequence to an unsuccessful native outcome."""
    assert native.message is not None
    reason = native.message.rstrip(".")
    return NativeStackSyncResult(
        action=native.action,
        stack_number=native.stack_number,
        message=(
            f"{reason}. The managed PR-body map was kept as a compatibility fallback."
        ),
    )


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
    progress: Callable[[str, BranchPlan | None], None] | None = None,
) -> list[BranchPlan]:
    """Inspect GitHub and build the action planned for each branch.

    One API call per branch, so this is the slowest part of a submit. The
    optional ``progress`` callback is invoked as ``(branch, None)`` before each
    lookup and ``(branch, plan)`` after, so the caller can show the tree filling
    in rather than sitting silent.
    """
    plans: list[BranchPlan] = []
    all_branches = set(git.get_all_local_branches(repo))
    stack_branches_set = set(full_stack_branches)

    def report(branch: str, plan: BranchPlan | None) -> None:
        if progress is not None:
            progress(branch, plan)

    for branch in branches:
        report(branch, None)
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
                        native_stack_number=(
                            existing_pr.stack.number if existing_pr.stack else None
                        ),
                        parent=parent,
                    )
                )
                report(branch, plans[-1])
            elif gh.has_merged_pr(branch):
                plans.append(
                    BranchPlan(branch=branch, action=PRAction.SKIPPED, parent=parent)
                )
                report(branch, plans[-1])
            else:
                plans.append(
                    BranchPlan(branch=branch, action=PRAction.CREATED, parent=parent)
                )
                report(branch, plans[-1])
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
            report(branch, plans[-1])
        except httpx.RequestError:
            plans.append(
                BranchPlan(branch=branch, action=PRAction.CREATED, parent=parent)
            )
            report(branch, plans[-1])

    return plans


def _load_submit_plans(
    repo: Repo,
    toolkit: ShortcakeRichToolkit,
    branches: list[str],
    full_stack_branches: list[str],
    progress: Callable[[str, BranchPlan | None], None] | None = None,
) -> list[BranchPlan]:
    """Build a live GitHub plan for the pre-submit visualization."""
    token, owner, repo_name = _get_submit_github_details(repo)
    with GitHubClient(token, owner, repo_name) as gh:
        return _build_branch_plans(
            repo, gh, toolkit, branches, full_stack_branches, progress=progress
        )


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
    explicit_branches: list[str] | None = None,
    fold_plan: bool = False,
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

    if explicit_branches is not None:
        # The picker's "just my arm" is neither the downstack nor the whole
        # stack, so it hands us the branches directly. Keep topological order.
        chosen = set(explicit_branches)
        stack_branches = [b for b in full_stack_branches if b in chosen]
    elif submit_stack:
        stack_branches = full_stack_branches
    else:
        stack_branches = _get_downstack_in_order(
            repo, current_branch, full_stack_branches
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
            # no echo here: the restack view prints its own
            # "✓ N branches restacked" footer
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

        stealth_plans = [
            BranchPlan(branch=branch, action=PRAction.PUSHED)
            for branch in stack_branches
        ]
        rows, by_branch = _rows_for_execution(
            repo,
            full_stack_branches,
            stack_branches,
            current_branch,
            plans=stealth_plans if fold_plan else None,
        )
        header = _submit_header(len(stack_branches), None, draft=draft, stealth=True)
        view, renderer = toolkit.stack_view(
            rows,
            _plan_heading(len(stack_branches), draft=False) if fold_plan else header,
            planning=fold_plan,
        )
        with view:
            if fold_plan:
                view.sync()
                _start_execution(renderer, header)
            for branch in stack_branches:
                branch_result = BranchSubmitResult(
                    branch=branch, action=PRAction.SKIPPED
                )
                row = by_branch[branch]
                row.state = RowState.ACTIVE
                row.label = Text("pushing…", style=Style(color="cyan"))
                view.sync()

                success, error = push_branch(repo, branch, force_with_lease=not force)
                if not success:
                    branch_result.error = error or "Failed to push"
                    row.state = RowState.FAILED
                    row.label = Text("push failed", style=Style(color="red"))
                    row.detail = branch_result.error
                else:
                    branch_result.action = PRAction.PUSHED
                    row.state = RowState.DONE
                    row.label = Text("pushed", style=Style(color="green"))
                result.branch_results.append(branch_result)
                view.sync()

            view.finish(
                _submit_footer(
                    result,
                    renderer,
                    draft=draft,
                    excluded=len(full_stack_branches) - len(stack_branches),
                )
            )
            view.sync()

        return result

    token, owner, repo_name = _get_submit_github_details(repo)

    with GitHubClient(token, owner, repo_name) as gh:
        # Open the block *before* the per-branch GitHub lookups. They are the
        # slowest part of a submit, and doing them first left the screen blank
        # — or, after the picker's transient block was erased, made the tree
        # vanish and reappear.
        rows, by_branch = _rows_for_execution(
            repo, full_stack_branches, stack_branches, current_branch
        )
        header = _submit_header(
            len(stack_branches), f"{owner}/{repo_name}", draft=draft, stealth=False
        )
        view, renderer = toolkit.stack_view(
            rows,
            _plan_heading(len(stack_branches), draft=draft) if fold_plan else header,
            planning=fold_plan,
        )
        if fold_plan:
            view.__enter__()

        def show_lookup(branch: str, plan: BranchPlan | None) -> None:
            if not fold_plan:
                return
            row = by_branch[branch]
            if plan is None:
                row.state = RowState.ACTIVE
                row.label = Text("checking…", style=DIM)
            else:
                row.state = RowState.PENDING
                row.label = _plan_label(plan, draft=draft)
            view.sync()

        if precomputed_plans is None:
            plans = _build_branch_plans(
                repo,
                gh,
                toolkit,
                stack_branches,
                full_stack_branches,
                progress=show_lookup,
            )
        else:
            plans_by_branch = {plan.branch: plan for plan in precomputed_plans}
            missing_branches = [
                branch for branch in stack_branches if branch not in plans_by_branch
            ]
            if missing_branches:
                for plan in _build_branch_plans(
                    repo,
                    gh,
                    toolkit,
                    missing_branches,
                    full_stack_branches,
                    progress=show_lookup,
                ):
                    plans_by_branch[plan.branch] = plan
            plans = [plans_by_branch[branch] for branch in stack_branches]
            if fold_plan:
                for plan in plans:
                    show_lookup(plan.branch, plan)

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

        # Dry run: show plan and return before any stack mutation.
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

        # GitHub's stack API is append-only. Inspect a native stack before
        # changing PR bases so reorder/move/fold operations can unstack safely.
        ordered_existing_prs = [
            pr_numbers[branch]
            for branch in stack_branches
            if isinstance(pr_numbers.get(branch), int)
        ]
        cached_prs = load_pr_cache(repo)
        owned_existing_prs = set(historical_prs.values()) | set(pr_numbers.values())
        changing_bases = any(
            plan.action == PRAction.UPDATED and plan.existing_pr_base != plan.parent
            for plan in plans
        )
        native_preparation = prepare_native_stack_restructure(
            gh,
            ordered_existing_prs,
            owned_existing_prs=owned_existing_prs,
            needs_restructure=False,
            allow_recreate=False,
        )
        remote_before_submit = native_preparation.remote_stack
        if remote_before_submit is not None:
            owned_existing_prs.update(
                cached.number
                for cached in cached_prs.values()
                if cached.native_stack_number == remote_before_submit.number
            )

        # A full submit also owns removals. Appends do not need a rebuild, but
        # any other difference between existing local PRs and the remote stack
        # does. Scoped submits deliberately preserve remote upstack PRs.
        full_linear_submit = (
            submit_stack
            and explicit_branches is None
            and stack_branches == full_stack_branches
            and not _stack_forks(repo, full_stack_branches)
        )
        if full_linear_submit and remote_before_submit is not None:
            changing_bases = (
                remote_before_submit.open_pr_numbers != ordered_existing_prs
                or changing_bases
            )

        if changing_bases:
            native_preparation = prepare_native_stack_restructure(
                gh,
                ordered_existing_prs,
                owned_existing_prs=owned_existing_prs,
                needs_restructure=True,
                allow_recreate=full_linear_submit,
            )
            remote_before_submit = (
                native_preparation.remote_stack or remote_before_submit
            )
            if not native_preparation.can_continue:
                raise SubmitError(
                    native_preparation.message
                    or "The native GitHub stack could not be safely restructured."
                )
            if (
                native_preparation.action == NativeStackPreparationAction.UNAVAILABLE
                and any(plan.native_stack_number for plan in plans)
            ):
                raise SubmitError(
                    "The PRs belong to a native GitHub stack, but its stack "
                    "endpoint is unavailable; their bases were left unchanged."
                )

        if remote_before_submit is not None:
            native_history = list(reversed(remote_before_submit.pull_requests))
            if not historical_stack_order:
                historical_stack_order = [pr.head_ref for pr in native_history]
            for pull_request in remote_before_submit.pull_requests:
                historical_prs.setdefault(pull_request.head_ref, pull_request.number)
                if pull_request.merged_at is not None:
                    historical_merged_prs.setdefault(
                        pull_request.head_ref, pull_request.number
                    )
                else:
                    pr_numbers.setdefault(pull_request.head_ref, pull_request.number)

        # Migrating an existing body-mapped stack must be atomic. A scoped
        # submit may leave open upstack PRs out of the selected sequence; do
        # not create a smaller native stack and then remove their only complete
        # stack overview. PRs already represented by the remote native stack
        # are safe because GitHub still owns their visualization.
        native_open_prs = (
            set(remote_before_submit.open_pr_numbers)
            if remote_before_submit is not None
            else set()
        )
        selected_branches = set(stack_branches)
        legacy_unselected_prs = list(
            dict.fromkeys(
                historical_prs[branch]
                for branch in full_stack_branches
                if branch not in selected_branches
                and branch in historical_prs
                and historical_prs[branch] not in native_open_prs
            )
        )

        # Phase 2: Execute plan - push and create/update PRs. The block is
        # already open showing the plan; it turns into progress in place, so
        # there is one tree on screen rather than a plan tree and a copy of it.
        if fold_plan:
            _start_execution(renderer, header)
        else:
            view.__enter__()
        for plan in plans:
            branch_result = BranchSubmitResult(
                branch=plan.branch, action=PRAction.SKIPPED
            )
            row = by_branch[plan.branch]

            # Push branch
            row.state = RowState.ACTIVE
            row.label = Text("pushing…", style=Style(color="cyan"))
            view.sync()
            success, error = push_branch(repo, plan.branch, force_with_lease=not force)
            if not success:
                branch_result.error = error or "Failed to push"
                row.state = RowState.FAILED
                row.label = Text("push rejected", style=Style(color="red"))
                row.detail = branch_result.error
                result.branch_results.append(branch_result)
                view.sync()
                continue

            if plan.parent is None:  # pragma: no cover
                branch_result.error = "No parent branch found"
                row.state = RowState.FAILED
                row.label = Text("no parent", style=Style(color="red"))
                result.branch_results.append(branch_result)
                view.sync()
                continue

            try:
                if plan.action == PRAction.UPDATED:
                    # Update existing PR - use info from planning
                    pr_numbers[plan.branch] = plan.existing_pr_number
                    branch_result.pr_number = plan.existing_pr_number
                    branch_result.pr_url = plan.existing_pr_url

                    # Update PR base if changed
                    row.state = RowState.ACTIVE
                    row.label = Text("updating PR…", style=Style(color="cyan"))
                    view.sync()
                    base_note = None
                    if plan.existing_pr_base != plan.parent:
                        base_note = f"base→{plan.parent}"
                        gh.update_pr(plan.existing_pr_number, base=plan.parent)
                    branch_result.action = PRAction.UPDATED
                    row.state = RowState.DONE
                    row.label = _pr_label(
                        plan.existing_pr_number, plan.existing_pr_url, base_note
                    )

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
                    branch_result.action = PRAction.SKIPPED
                    row.state = RowState.SKIPPED
                    row.label = Text("merged", style=DIM)
                    result.branch_results.append(branch_result)
                    view.sync()
                    continue

                else:  # PRAction.CREATED
                    # Create new PR
                    title = _get_commit_title(repo, plan.branch)
                    row.state = RowState.ACTIVE
                    row.label = Text("creating PR…", style=Style(color="cyan"))
                    view.sync()
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
                    row.state = RowState.DONE
                    row.label = _pr_label(pr.number, pr.url)

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

            if branch_result.error:
                row.state = RowState.FAILED
                row.label = Text("failed", style=Style(color="red"))
                row.detail = branch_result.error
            result.branch_results.append(branch_result)
            view.sync()

        view.finish(
            _submit_footer(
                result,
                renderer,
                draft=draft,
                excluded=len(full_stack_branches) - len(stack_branches),
            )
        )
        view.sync()
        view.__exit__(None, None, None)

        # Phase 3: Publish the linear PR sequence through GitHub's native stack
        # API. Forks and unavailable/diverged APIs retain the body map fallback.
        desired_prs = [
            pr_numbers[branch]
            for branch in stack_branches
            if isinstance(pr_numbers.get(branch), int)
        ]
        submit_failed = any(branch.error for branch in result.branch_results)
        if submit_failed:
            result.native_stack = NativeStackSyncResult(
                NativeStackAction.FAILED,
                message="Native stack publishing was skipped after a branch failed.",
            )
        elif _stack_forks(repo, full_stack_branches):
            result.native_stack = fallback_native_stack(
                "GitHub native stacks require a linear sequence, but this "
                "Shortcake tree is non-linear."
            )
        elif native_preparation.action == NativeStackPreparationAction.UNAVAILABLE:
            result.native_stack = NativeStackSyncResult(
                NativeStackAction.UNAVAILABLE,
                message=native_preparation.message,
            )
        elif native_preparation.action in {
            NativeStackPreparationAction.BLOCKED,
            NativeStackPreparationAction.FAILED,
        }:
            result.native_stack = fallback_native_stack(
                native_preparation.message or "The native stack could not be inspected."
            )
        elif legacy_unselected_prs:
            noun = "PR" if len(legacy_unselected_prs) == 1 else "PRs"
            formatted_prs = ", ".join(f"#{number}" for number in legacy_unselected_prs)
            result.native_stack = fallback_native_stack(
                f"The existing Shortcake stack includes unselected {noun} "
                f"{formatted_prs}; run 'sc submit --stack' to migrate the whole stack."
            )
        else:
            result.native_stack = reconcile_native_stack(
                gh,
                desired_prs,
                recreated=(
                    native_preparation.action == NativeStackPreparationAction.UNSTACKED
                ),
            )

        if (
            not result.native_stack.synced
            and result.native_stack.message != NATIVE_STACK_MINIMUM_MESSAGE
        ):
            result.native_stack = _with_pr_body_fallback(result.native_stack)

        if result.native_stack.synced:
            cleanup_branches = list(
                dict.fromkeys([*stack_branches, *historical_stack_order])
            )
            cleanup_prs = []
            for branch in cleanup_branches:
                with contextlib.suppress(httpx.HTTPStatusError, httpx.RequestError):
                    pull_request = gh.get_pr_for_branch(branch)
                    if pull_request is not None:
                        cleanup_prs.append(pull_request)
                        update_pr_cache(
                            repo,
                            branch,
                            pull_request.number,
                            is_draft=pull_request.is_draft,
                            url=pull_request.url,
                            native_stack_number=(
                                pull_request.stack.number
                                if pull_request.stack
                                else (
                                    result.native_stack.stack_number
                                    if isinstance(result.native_stack.stack_number, int)
                                    else None
                                )
                            ),
                            native_stack_position=(
                                pull_request.stack.position
                                if pull_request.stack
                                else None
                            ),
                            native_stack_size=(
                                pull_request.stack.size if pull_request.stack else None
                            ),
                        )
            with contextlib.suppress(httpx.HTTPStatusError, httpx.RequestError):
                _remove_stack_pr_descriptions(gh, cleanup_prs)
        else:
            with contextlib.suppress(httpx.HTTPStatusError, httpx.RequestError):
                _sync_stack_pr_descriptions(
                    repo,
                    gh,
                    owner,
                    stack_branches,
                    pr_numbers=(
                        {**historical_prs, **pr_numbers}
                        if legacy_unselected_prs
                        else pr_numbers
                    ),
                    overview_branches=(
                        full_stack_branches if legacy_unselected_prs else None
                    ),
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

        _report_native_stack(toolkit, result.native_stack)

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
    explicit_branches: list[str] | None = None
    try:
        current_branch = git.get_current_branch(repo)
        if current_branch is not None:
            stack_branches = _get_stack_in_order(repo, current_branch)
            downstack_branches = _get_downstack_in_order(
                repo, current_branch, stack_branches
            )
            selected_branches = stack_branches if stack else downstack_branches
            asking = _should_ask_scope(
                stack_branches,
                downstack_branches,
                stack=stack,
                json_output=json_output,
                interactive=_is_interactive() and toolkit.console.is_terminal,
                forks=bool(stack_branches) and _stack_forks(repo, stack_branches),
            )
            # On a TTY the live block opens with the plan and fills in, so
            # printing it separately would put the same tree on screen twice.
            fold_plan = (
                not json_output
                and not dry_run
                and bool(stack_branches)
                and toolkit.console.is_terminal
            )
            if not json_output and stack_branches and not asking and not fold_plan:
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
            if asking:
                scope, selected_branches = _ask_scope(
                    repo,
                    toolkit,
                    stack_branches,
                    downstack_branches,
                    current_branch,
                    stack=stack,
                    stealth=stealth,
                    draft=draft,
                )
                if scope == "cancel":
                    toolkit.echo("Cancelled · nothing pushed, no PRs touched")
                    return
                stack = scope == "stack"
                explicit_branches = selected_branches if scope == "lineage" else None
                preview_plans = None

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
            explicit_branches=explicit_branches,
            fold_plan=fold_plan,
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
                "native_stack": (
                    result.native_stack.to_data()
                    if result.native_stack is not None
                    else None
                ),
            }
        )
        if any(r.error for r in result.branch_results):
            raise typer.Exit(1)
        return

    if dry_run:
        return

    # The stack view already rendered the result footer, including per-branch
    # errors. Only the exit code is left to decide here.
    if any(r.error for r in result.branch_results):
        raise typer.Exit(1)

"""Helpers for keeping stacked PR metadata in sync."""

import re

import httpx

from shortcake import _git as git
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, PRInfo
from shortcake.commands.restack import _get_stack_in_order

# Markers for stack section in PR body
STACK_START_MARKER = "<!-- shortcake:start -->"
STACK_END_MARKER = "<!-- shortcake:end -->"

# Stack section heading, with a 🍰 link back to the project
SHORTCAKE_URL = "https://shortcake.patrick.wtf"
STACK_HEADING = f"## Stack [🍰]({SHORTCAKE_URL})"

# Regex patterns for parsing stack sections
# Matches: - #42 (merged) (`branch-name`)
_MERGED_PR_PATTERN = re.compile(r"-\s*#(\d+)\s*\(merged\)\s*\(`([^`]+)`\)")
# Matches any branch in stack: - #42 (`branch`) or - **#42** (`branch`)
# or - (no PR) (`branch`)
_STACK_BRANCH_PATTERN = re.compile(
    r"-\s*(?:\*\*)?(?:#\d+|#\d+\s*\(merged\)|\(no PR\))(?:\*\*)?\s*\(`([^`]+)`\)"
)
# Matches any PR number with branch: - #42 (`branch`) or - **#42** (`branch`)
# Excludes (no PR) and (merged) entries.
_ALL_PR_PATTERN = re.compile(
    r"-\s*\*{0,2}#(\d+)\*{0,2}\s*\(`([^`]+)`\)(?:\s*<-- this PR)?"
)


def _parse_merged_prs_from_body(body: str) -> dict[str, int]:
    """Extract merged PR info from an existing stack section."""
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
    """Extract all PR numbers from an existing stack section."""
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
    """Extract branch order from an existing stack section."""
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


def _build_stack_section(
    stack_branches: list[str],
    current_branch: str,
    pr_numbers: dict[str, int],
    _owner: str,
    merged_pr_numbers: dict[str, int] | None = None,
) -> str:
    """Build the stack visualization markdown section."""

    if merged_pr_numbers is None:
        merged_pr_numbers = {}

    lines = [STACK_START_MARKER, STACK_HEADING, ""]

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
    """Replace or prepend the stack section in a PR body."""
    if STACK_START_MARKER in existing_body and STACK_END_MARKER in existing_body:
        pattern = re.escape(STACK_START_MARKER) + r".*?" + re.escape(STACK_END_MARKER)
        return re.sub(pattern, stack_section, existing_body, flags=re.DOTALL)

    if existing_body.strip():
        return f"{stack_section}\n\n{existing_body}"
    return stack_section


def _sync_stack_pr_descriptions(
    repo: Repo,
    gh: GitHubClient,
    owner: str,
    stack_branches: list[str],
    *,
    pr_numbers: dict[str, int] | None = None,
    sync_bases: bool = False,
) -> None:
    """Update PR bases and stack sections for a single local stack."""
    if not stack_branches:
        return

    all_branches = set(git.get_all_local_branches(repo))
    current_stack_set = set(stack_branches)
    known_pr_numbers = dict(pr_numbers or {})
    open_prs: dict[str, PRInfo] = {}

    for branch in stack_branches:
        try:
            existing_pr = gh.get_pr_for_branch(branch)
        except (httpx.HTTPStatusError, httpx.RequestError):
            continue
        if existing_pr:
            open_prs[branch] = existing_pr
            known_pr_numbers[branch] = existing_pr.number

    if not open_prs:
        return

    if sync_bases:
        for branch, existing_pr in open_prs.items():
            parent = git.get_branch_parent(repo, branch, all_branches)
            if parent is None:
                continue
            if parent not in all_branches:
                try:
                    merged_base = gh.get_merged_pr_base(parent)
                    if merged_base:
                        parent = merged_base
                except (httpx.HTTPStatusError, httpx.RequestError):
                    pass
            if parent != existing_pr.base:
                gh.update_pr(existing_pr.number, base=parent)
                existing_pr.base = parent

    historical_merged_prs: dict[str, int] = {}
    historical_prs: dict[str, int] = {}
    historical_stack_order: list[str] = []
    for branch in stack_branches:
        existing_pr = open_prs.get(branch)
        if existing_pr is None or not existing_pr.body:
            continue

        parsed_merged = _parse_merged_prs_from_body(existing_pr.body)
        for branch_name, pr_num in parsed_merged.items():
            historical_merged_prs.setdefault(branch_name, pr_num)

        parsed_all = _parse_all_prs_from_body(existing_pr.body)
        for branch_name, pr_num in parsed_all.items():
            historical_prs.setdefault(branch_name, pr_num)

        if not historical_stack_order:
            historical_stack_order = _parse_stack_order_from_body(existing_pr.body)

    for hist_branch in historical_stack_order:
        if (
            hist_branch not in current_stack_set
            and hist_branch not in all_branches
            and hist_branch not in historical_merged_prs
        ):
            try:
                merged_num = gh.get_merged_pr_number(hist_branch)
            except (httpx.HTTPStatusError, httpx.RequestError):
                merged_num = None
            if isinstance(merged_num, int):
                historical_merged_prs[hist_branch] = merged_num
                known_pr_numbers.pop(hist_branch, None)
                continue

            if hist_branch in historical_prs:
                known_pr_numbers[hist_branch] = historical_prs[hist_branch]
            else:
                try:
                    existing_pr = gh.get_pr_for_branch(hist_branch)
                except (httpx.HTTPStatusError, httpx.RequestError):
                    continue
                if existing_pr:
                    known_pr_numbers[hist_branch] = existing_pr.number

    merged_pr_numbers: dict[str, int] = dict(historical_merged_prs)
    for branch in stack_branches:
        if branch in known_pr_numbers or branch in merged_pr_numbers:
            continue
        try:
            merged_num = gh.get_merged_pr_number(branch)
        except (httpx.HTTPStatusError, httpx.RequestError):
            continue
        if isinstance(merged_num, int):
            merged_pr_numbers[branch] = merged_num

    full_stack_branches = list(stack_branches)
    if historical_stack_order:
        historical_bottom_to_top = list(reversed(historical_stack_order))
        historical_positions = {
            branch: index for index, branch in enumerate(historical_bottom_to_top)
        }
        merged_historical_bottom: list[str] = []

        for hist_branch in historical_bottom_to_top:
            if hist_branch in full_stack_branches or hist_branch in all_branches:
                continue

            if hist_branch in historical_merged_prs:
                merged_historical_bottom.append(hist_branch)
                continue

            inserted = False
            hist_pos = historical_positions[hist_branch]
            for index, local_branch in enumerate(full_stack_branches):
                if local_branch not in historical_positions:
                    continue
                if hist_pos < historical_positions[local_branch]:
                    full_stack_branches.insert(index, hist_branch)
                    inserted = True
                    break
            if not inserted:
                full_stack_branches.append(hist_branch)

        full_stack_branches = merged_historical_bottom + full_stack_branches

    for branch, existing_pr in open_prs.items():
        stack_section = _build_stack_section(
            full_stack_branches,
            branch,
            known_pr_numbers,
            owner,
            merged_pr_numbers,
        )
        new_body = _update_pr_body_with_stack(existing_pr.body, stack_section)
        gh.update_pr(existing_pr.number, body=new_body)


def _sync_pr_descriptions_for_branches(
    repo: Repo,
    gh: GitHubClient,
    owner: str,
    branches: list[str],
    *,
    sync_bases: bool = False,
) -> None:
    """Sync PR metadata for every unique stack touched by the given branches."""
    seen_stacks: set[tuple[str, ...]] = set()
    for branch in branches:
        stack_branches = _get_stack_in_order(repo, branch)
        if not stack_branches:
            continue

        stack_key = tuple(stack_branches)
        if stack_key in seen_stacks:
            continue

        seen_stacks.add(stack_key)
        _sync_stack_pr_descriptions(
            repo,
            gh,
            owner,
            stack_branches,
            sync_bases=sync_bases,
        )

from dataclasses import dataclass
from datetime import datetime, timezone

import typer
from rich.console import Console

from shortcake import get_cli_name
from shortcake.git import GitError, GitRepo
from shortcake.metadata import get_all_branch_metadata
from shortcake.output import print_error

console = Console()

app = typer.Typer()


@dataclass
class BranchDisplayInfo:
    """Information about a branch for display in ls."""

    name: str
    parent: str | None
    is_current: bool
    pr_number: int | None = None
    pr_url: str | None = None
    commit_hash: str | None = None
    commit_message: str | None = None
    commit_date: datetime | None = None


def _get_relative_time(dt: datetime | None) -> str:
    """Convert datetime to relative time string like '24 hours ago'."""
    if dt is None:
        return ""

    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = int(seconds / 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    else:
        months = int(seconds / 2592000)
        return f"{months} month{'s' if months != 1 else ''} ago"


def _get_shortcake_branches(git: GitRepo) -> list[BranchDisplayInfo]:
    """Get all branches that are managed by shortcake."""
    branches: list[BranchDisplayInfo] = []
    current_branch = git.get_current_branch()
    all_metadata = get_all_branch_metadata()

    for branch_name, metadata in all_metadata.items():
        # Get commit info for this branch
        commit_hash = None
        commit_message = None
        commit_date = None
        try:
            commit_hash = git.get_commit_sha(branch_name)[:7]
            commit_message = git.get_commit_message(branch_name)
            commit_date = git.get_commit_date(branch_name)
        except GitError:
            pass

        branches.append(
            BranchDisplayInfo(
                name=branch_name,
                parent=metadata.get("parent"),
                is_current=branch_name == current_branch,
                pr_number=metadata.get("pr_number"),
                pr_url=metadata.get("pr_url"),
                commit_hash=commit_hash,
                commit_message=commit_message,
                commit_date=commit_date,
            )
        )

    return branches


def _build_tree_lines(
    branches: list[BranchDisplayInfo], git: GitRepo
) -> list[str]:
    """Build a vertical stack visualization with connecting lines.

    Shows the stack with trunk at bottom and tips at top.
    Uses │ lines to show parent-child relationships.
    Uses ◉ for current branch, ◯ for others.
    """
    if not branches:
        return []

    # Get set of all tracked branch names
    tracked_names = {b.name for b in branches}
    branch_map = {b.name: b for b in branches}

    # Build a map of children for each parent
    children_map: dict[str | None, list[str]] = {}
    for branch in branches:
        parent = branch.parent
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(branch.name)

    # Sort children alphabetically for consistent output
    for parent in children_map:
        children_map[parent].sort()

    def get_branch_block(branch: BranchDisplayInfo) -> list[str]:
        """Get formatted lines for a single branch."""
        result = []

        # Branch marker and name
        marker = "◉" if branch.is_current else "◯"
        if branch.pr_number and branch.pr_url:
            pr_indicator = f" [link={branch.pr_url}]#{branch.pr_number}[/link]"
        elif branch.pr_number:
            pr_indicator = f" #{branch.pr_number}"
        else:
            pr_indicator = ""

        branch_line = f"│ {marker} [bold {'cyan' if branch.is_current else 'blue'}]{branch.name}[/]{pr_indicator}"
        if branch.is_current:
            branch_line += " [dim](current)[/]"
        result.append(branch_line)

        # Time ago
        if branch.commit_date:
            time_ago = _get_relative_time(branch.commit_date)
            result.append(f"│ │  [dim]{time_ago}[/]")

        # Commit info
        if branch.commit_hash and branch.commit_message:
            msg = branch.commit_message
            if len(msg) > 50:
                msg = msg[:47] + "..."
            result.append(f"│ │  [dim]{branch.commit_hash} - {msg}[/]")

        result.append("│ │")

        return result

    def get_stack_tip(branch_name: str) -> str:
        """Get the tip (topmost) branch of a stack."""
        children = children_map.get(branch_name, [])
        if not children:
            return branch_name
        # Follow the first child to the tip
        return get_stack_tip(children[0])

    def get_stack_from_tip(tip_name: str, stop_at: str) -> list[str]:
        """Get branches from tip down to (but not including) stop_at."""
        result = []
        current = tip_name
        while current and current != stop_at and current in tracked_names:
            result.append(current)
            current = branch_map[current].parent
        return result

    lines: list[str] = []

    # Find root parent (trunk)
    root_parent: str | None = None
    for branch in branches:
        if branch.parent and branch.parent not in tracked_names:
            root_parent = branch.parent
            break

    if not root_parent:
        return []

    # Get all direct children of trunk (these are separate stacks)
    trunk_children = children_map.get(root_parent, [])

    if not trunk_children:
        return []

    # For each stack from trunk, render from tip to trunk
    for trunk_child in trunk_children:
        # Find the tip of this stack
        tip = get_stack_tip(trunk_child)

        # Get all branches from tip down to trunk
        stack = get_stack_from_tip(tip, root_parent)

        # Render each branch in the stack (tip first)
        for branch_name in stack:
            branch = branch_map[branch_name]
            lines.extend(get_branch_block(branch))

        # Close this stack with merge indicator
        lines.append("├─┘")

    # Add trunk at the bottom
    lines.append("│")
    lines.append(f"◯ {root_parent}")

    return lines


@app.command()
def ls():
    """List all shortcake-managed branches in a tree structure.

    Shows all branches that are tracked by shortcake (have shortcake git notes),
    displaying their parent-child relationships as a tree.
    The current branch is marked with (current).
    """
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    branches = _get_shortcake_branches(git)

    if not branches:
        cli = get_cli_name()
        typer.echo("No shortcake-managed branches found")
        typer.echo(
            f"Use '{cli} create' to create a new stack or '{cli} adopt' to track existing branches"
        )
        return

    tree_lines = _build_tree_lines(branches, git)
    for line in tree_lines:
        console.print(line)

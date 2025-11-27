from dataclasses import dataclass

import typer
from rich.console import Console

from shortcake import get_cli_name
from shortcake.git import GitError, GitRepo
from shortcake.metadata import get_all_branch_metadata

console = Console()

app = typer.Typer()


@dataclass
class BranchInfo:
    """Information about a branch managed by shortcake."""

    name: str
    parent: str | None
    is_current: bool
    pr_number: int | None = None
    pr_url: str | None = None


def _get_shortcake_branches(git: GitRepo) -> list[BranchInfo]:
    """Get all branches that are managed by shortcake.

    Returns:
        List of BranchInfo objects for shortcake-managed branches.
    """
    branches: list[BranchInfo] = []
    current_branch = git.get_current_branch()
    all_metadata = get_all_branch_metadata()

    for branch_name, metadata in all_metadata.items():
        branches.append(
            BranchInfo(
                name=branch_name,
                parent=metadata.get("parent"),
                is_current=branch_name == current_branch,
                pr_number=metadata.get("pr_number"),
                pr_url=metadata.get("pr_url"),
            )
        )

    return branches


def _build_tree_lines(branches: list[BranchInfo]) -> list[str]:
    """Build a vertical stack visualization.

    Shows the stack with tip at top and base at bottom.
    Uses ◉ for current branch, ◯ for others.
    This makes 'up' (to parent) go visually down, and 'down' (to child) go visually up.

    Args:
        branches: List of BranchInfo objects.

    Returns:
        List of formatted strings representing the tree.
    """
    if not branches:
        return []

    # Get set of all tracked branch names
    tracked_names = {b.name for b in branches}

    # Build a map of children for each parent
    children_map: dict[str | None, list[BranchInfo]] = {}
    for branch in branches:
        if branch.parent not in children_map:
            children_map[branch.parent] = []
        children_map[branch.parent].append(branch)

    lines: list[str] = []

    def format_branch_line(branch: BranchInfo, indent: str = "") -> str:
        """Format a single branch line."""
        marker = "◉" if branch.is_current else "◯"
        if branch.pr_number and branch.pr_url:
            pr_indicator = f" [link={branch.pr_url}]#{branch.pr_number}[/link]"
        elif branch.pr_number:
            pr_indicator = f" #{branch.pr_number}"
        else:
            pr_indicator = ""
        return f"{indent}{marker} {branch.name}{pr_indicator}"

    def add_stack(branch: BranchInfo, indent: str = ""):
        """Recursively add a branch and its ancestors (tip first, base last)."""
        lines.append(format_branch_line(branch, indent))
        lines.append(f"{indent}│")

        # Check if parent is tracked
        if branch.parent and branch.parent in tracked_names:
            parent = next(b for b in branches if b.name == branch.parent)
            add_stack(parent, indent)

    # Find leaf branches (tips of stacks - branches with no children)
    leaf_branches = [b for b in branches if b.name not in children_map]
    leaf_branches.sort(key=lambda b: b.name)

    # Find base parents (main/master etc)
    base_parents: set[str] = set()
    for branch in branches:
        if branch.parent and branch.parent not in tracked_names:
            base_parents.add(branch.parent)

    # For each leaf, build its stack from tip to base
    for i, leaf in enumerate(leaf_branches):
        add_stack(leaf)

        # Add the base branch at the bottom
        # Find which base this stack connects to
        current = leaf
        while current.parent and current.parent in tracked_names:
            current = next(b for b in branches if b.name == current.parent)
        if current.parent:
            lines.append(f"◯ {current.parent}")

        # Add separator between stacks if there are multiple
        if i < len(leaf_branches) - 1:
            lines.append("")

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
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    branches = _get_shortcake_branches(git)

    if not branches:
        cli = get_cli_name()
        typer.echo("No shortcake-managed branches found")
        typer.echo(
            f"Use '{cli} create' to create a new stack or '{cli} adopt' to track existing branches"
        )
        return

    tree_lines = _build_tree_lines(branches)
    for line in tree_lines:
        console.print(line)

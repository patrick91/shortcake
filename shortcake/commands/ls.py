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
    """Build a tree visualization of the branch stack.

    Shows the stack with leaves (tip of stack) at the top and roots (base) at the bottom.
    This makes 'up' (to parent) go visually down, and 'down' (to child) go visually up.

    Args:
        branches: List of BranchInfo objects.

    Returns:
        List of formatted strings representing the tree.
    """
    if not branches:
        return []

    # Build maps for navigation
    branch_map = {b.name: b for b in branches}

    # Build a map of children for each parent
    children_map: dict[str | None, list[BranchInfo]] = {}
    for branch in branches:
        if branch.parent not in children_map:
            children_map[branch.parent] = []
        children_map[branch.parent].append(branch)

    lines: list[str] = []

    def format_branch(branch: BranchInfo, prefix: str, connector: str) -> str:
        """Format a single branch line."""
        current_indicator = " (current)" if branch.is_current else ""
        if branch.pr_number and branch.pr_url:
            pr_indicator = f" [link={branch.pr_url}]#{branch.pr_number}[/link]"
        elif branch.pr_number:
            pr_indicator = f" #{branch.pr_number}"
        else:
            pr_indicator = ""
        return f"{prefix}{connector}{branch.name}{pr_indicator}{current_indicator}"

    def add_branch_and_ancestors(branch: BranchInfo, prefix: str = "", is_last: bool = True):
        """Add branch and its ancestors to the tree (leaf at top, root at bottom)."""
        connector = "└── " if is_last else "├── "
        lines.append(format_branch(branch, prefix, connector))

        # Add parent below with more indentation
        if branch.parent and branch.parent in branch_map:
            parent = branch_map[branch.parent]
            extension = "    " if is_last else "│   "
            add_branch_and_ancestors(parent, prefix + extension, True)

    # Find leaf branches (those with no children among tracked branches)
    leaf_branches = [b for b in branches if b.name not in children_map]

    # Also include branches whose only children are not in tracked set
    for branch in branches:
        children = children_map.get(branch.name, [])
        if not children and branch not in leaf_branches:
            leaf_branches.append(branch)

    # Sort leaves for consistent output
    leaf_branches.sort(key=lambda b: b.name)

    for i, branch in enumerate(leaf_branches):
        is_last = i == len(leaf_branches) - 1
        add_branch_and_ancestors(branch, "", is_last)

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

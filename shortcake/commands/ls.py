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

    def add_branch_to_tree(branch: BranchInfo, prefix: str = "", is_last: bool = True):
        """Recursively add branch and its children to the tree."""
        # Determine the tree characters
        connector = "└── " if is_last else "├── "
        current_indicator = " (current)" if branch.is_current else ""

        # PR indicator with optional link
        if branch.pr_number and branch.pr_url:
            pr_indicator = f" [link={branch.pr_url}]#{branch.pr_number}[/link]"
        elif branch.pr_number:
            pr_indicator = f" #{branch.pr_number}"
        else:
            pr_indicator = ""

        lines.append(f"{prefix}{connector}{branch.name}{pr_indicator}{current_indicator}")

        # Get children of this branch
        children = children_map.get(branch.name, [])

        # Add children
        for i, child in enumerate(children):
            is_last_child = i == len(children) - 1
            extension = "    " if is_last else "│   "
            add_branch_to_tree(child, prefix + extension, is_last_child)

    # Root branches are those with no parent OR whose parent is not tracked
    root_branches: list[BranchInfo] = []
    for parent_name, branches_list in children_map.items():
        if parent_name is None or parent_name not in tracked_names:
            root_branches.extend(branches_list)

    for i, branch in enumerate(root_branches):
        is_last = i == len(root_branches) - 1
        add_branch_to_tree(branch, "", is_last)

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

from dataclasses import dataclass

import typer
from rich.console import Console

from shortcake import get_cli_name
from shortcake.git import GitError, GitRepo
from shortcake.metadata import get_all_branch_metadata

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


def _get_shortcake_branches(git: GitRepo) -> list[BranchDisplayInfo]:
    """Get all branches that are managed by shortcake.

    Returns:
        List of BranchDisplayInfo objects for shortcake-managed branches.
    """
    branches: list[BranchDisplayInfo] = []
    current_branch = git.get_current_branch()
    all_metadata = get_all_branch_metadata()

    for branch_name, metadata in all_metadata.items():
        branches.append(
            BranchDisplayInfo(
                name=branch_name,
                parent=metadata.get("parent"),
                is_current=branch_name == current_branch,
                pr_number=metadata.get("pr_number"),
                pr_url=metadata.get("pr_url"),
            )
        )

    return branches


def _build_tree_lines(branches: list[BranchDisplayInfo]) -> list[str]:
    """Build a vertical stack visualization.

    Shows the stack with tip at top and base at bottom.
    Uses ◉ for current branch, ◯ for others.
    This makes 'up' (to parent) go visually down, and 'down' (to child) go visually up.

    Args:
        branches: List of BranchDisplayInfo objects.

    Returns:
        List of formatted strings representing the tree.
    """
    if not branches:
        return []

    # Get set of all tracked branch names
    tracked_names = {b.name for b in branches}
    branch_map = {b.name: b for b in branches}

    # Build a map of children for each parent
    children_map: dict[str | None, list[BranchDisplayInfo]] = {}
    for branch in branches:
        if branch.parent not in children_map:
            children_map[branch.parent] = []
        children_map[branch.parent].append(branch)

    lines: list[str] = []

    def format_branch_line(branch: BranchDisplayInfo, indent: str = "") -> str:
        """Format a single branch line."""
        marker = "◉" if branch.is_current else "◯"
        if branch.pr_number and branch.pr_url:
            pr_indicator = f" [link={branch.pr_url}]#{branch.pr_number}[/link]"
        elif branch.pr_number:
            pr_indicator = f" #{branch.pr_number}"
        else:
            pr_indicator = ""
        current_indicator = "  ← you are here" if branch.is_current else ""
        return f"{indent}{marker} {branch.name}{pr_indicator}{current_indicator}"

    def get_stack_to_base(branch: BranchDisplayInfo) -> list[BranchDisplayInfo]:
        """Get list of branches from tip to base (excluding untracked base)."""
        stack = [branch]
        current = branch
        while current.parent and current.parent in tracked_names:
            current = branch_map[current.parent]
            stack.append(current)
        return stack

    def get_base_parent(branch: BranchDisplayInfo) -> str | None:
        """Get the untracked base parent (e.g., main/master)."""
        current = branch
        while current.parent and current.parent in tracked_names:
            current = branch_map[current.parent]
        return current.parent

    # Find leaf branches (tips of stacks - branches with no children)
    leaf_branches = [b for b in branches if b.name not in children_map]
    leaf_branches.sort(key=lambda b: b.name)

    # Group leaves by their base parent
    base_to_leaves: dict[str | None, list[BranchDisplayInfo]] = {}
    for leaf in leaf_branches:
        base = get_base_parent(leaf)
        if base not in base_to_leaves:
            base_to_leaves[base] = []
        base_to_leaves[base].append(leaf)

    def stack_contains_current(leaf: BranchDisplayInfo) -> bool:
        """Check if the stack from this leaf contains the current branch."""
        current = leaf
        while current:
            if current.is_current:
                return True
            if current.parent and current.parent in tracked_names:
                current = branch_map[current.parent]
            else:
                break
        return False

    # Sort leaves so the stack with current branch is last (on main line)
    for base in base_to_leaves:
        base_to_leaves[base].sort(key=lambda leaf: (stack_contains_current(leaf), leaf.name))

    # Process each base and its stacks
    for base_idx, (base, leaves) in enumerate(
        sorted(base_to_leaves.items(), key=lambda x: x[0] or "")
    ):
        if len(leaves) == 1:
            # Single stack - simple output
            stack = get_stack_to_base(leaves[0])
            for branch in stack:
                lines.append(format_branch_line(branch))
                lines.append("│")
            if base:
                lines.append(f"◯ {base}")
        else:
            # Multiple stacks sharing the same base
            # Find where stacks diverge (common ancestors)
            stacks = [get_stack_to_base(leaf) for leaf in leaves]

            # Find common suffix (shared ancestors)
            min_len = min(len(s) for s in stacks)
            common_count = 0
            for i in range(1, min_len + 1):
                if all(s[-i].name == stacks[0][-i].name for s in stacks):
                    common_count = i
                else:
                    break

            # Output each stack's unique part with indentation
            for stack_idx, stack in enumerate(stacks):
                unique_part = stack[:-common_count] if common_count > 0 else stack
                is_last_stack = stack_idx == len(stacks) - 1

                for branch in unique_part:
                    lines.append(format_branch_line(branch, "│ " if not is_last_stack else ""))
                    lines.append("│ │" if not is_last_stack else "│")

                if not is_last_stack:
                    lines.append("├─┘")

            # Output common ancestors
            if common_count > 0:
                common_part = stacks[0][-common_count:]
                for branch in common_part:
                    lines.append(format_branch_line(branch))
                    lines.append("│")

            if base:
                lines.append(f"◯ {base}")

        # Separator between different bases
        if base_idx < len(base_to_leaves) - 1:
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

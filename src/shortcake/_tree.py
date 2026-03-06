from dataclasses import dataclass, field
from enum import Enum, auto


class BranchWarning(Enum):
    """Warning types for branch nodes."""

    ORPHAN = auto()  # Parent branch was deleted
    CYCLE = auto()  # Branch is part of a circular reference


@dataclass
class BranchNode:
    """A node in the branch tree."""

    name: str
    parent_name: str | None = None
    children: list["BranchNode"] = field(default_factory=list)
    is_current: bool = False
    warning: BranchWarning | None = None
    # PR info (populated asynchronously)
    pr_number: int | None = None
    pr_is_draft: bool = False
    pr_is_merged: bool = False
    pr_is_closed: bool = False
    pr_url: str | None = None


@dataclass
class StackTree:
    """Tree structure for visualizing stacked branches."""

    roots: list[BranchNode] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        branches: dict[str, str | None],
        all_branches: set[str],
        current: str | None,
    ) -> "StackTree":
        """
        Build a tree from branch-parent mappings.

        Args:
            branches: Dict mapping branch name to parent name (None if no parent)
            all_branches: Set of all branch names that exist in the repo
            current: Name of the current branch (or None)

        Returns:
            StackTree with roots being the base branches
        """

        # Detect cycles in the parent chain
        def find_cycle_members(branch: str) -> set[str]:
            """Find all branches that are part of a cycle starting from branch."""
            visited: set[str] = set()
            path: list[str] = []
            current_branch = branch

            while current_branch and current_branch not in visited:
                if current_branch not in branches:
                    return set()  # Reached end of tracked branches, no cycle
                visited.add(current_branch)
                path.append(current_branch)
                current_branch = branches.get(current_branch)

            if current_branch in path:
                # Found a cycle - return all members from the cycle start
                cycle_start_idx = path.index(current_branch)
                return set(path[cycle_start_idx:])
            return set()

        # Find all branches that are part of any cycle
        cycle_members: set[str] = set()
        for branch in branches:
            cycle_members.update(find_cycle_members(branch))

        # Create nodes for all tracked branches
        nodes: dict[str, BranchNode] = {}

        for branch_name, parent_name in branches.items():
            # Check for orphan (parent doesn't exist in repo)
            is_orphan = parent_name is not None and parent_name not in all_branches
            is_in_cycle = branch_name in cycle_members

            warning = None
            if is_in_cycle:
                warning = BranchWarning.CYCLE
            elif is_orphan:
                warning = BranchWarning.ORPHAN

            nodes[branch_name] = BranchNode(
                name=branch_name,
                parent_name=parent_name,
                is_current=(branch_name == current),
                warning=warning,
            )

        # Find parent branches that have tracked children but aren't tracked themselves
        # These become roots
        parent_names = {p for p in branches.values() if p is not None}
        root_parents = parent_names - set(branches.keys())

        # Create nodes for root parents if they exist in the repo
        for parent_name in root_parents:
            if parent_name in all_branches:
                nodes[parent_name] = BranchNode(
                    name=parent_name,
                    parent_name=None,
                    is_current=(parent_name == current),
                )

        # Build parent-child relationships (skip cycles to avoid infinite loops)
        for branch_name, parent_name in branches.items():
            # Don't add child relationship if it would create a cycle
            if (
                parent_name
                and parent_name in nodes
                and branch_name not in cycle_members
            ):
                nodes[parent_name].children.append(nodes[branch_name])

        # Find root nodes:
        # 1. Nodes with no parent or parent not in nodes
        # 2. Cycle members (they become roots since we broke the cycle)
        # 3. Orphan branches (parent doesn't exist)
        roots: list[BranchNode] = []
        for node in nodes.values():
            is_root = (
                node.parent_name is None
                or node.parent_name not in nodes
                or node.warning == BranchWarning.CYCLE
                or node.warning == BranchWarning.ORPHAN
            )
            if is_root:
                roots.append(node)

        # Sort roots and all children alphabetically for consistent output
        roots.sort(key=lambda n: n.name)
        for node in nodes.values():
            node.children.sort(key=lambda n: n.name)

        return cls(roots=roots)

    def render(self) -> str:
        """
        Render the tree as a string with box-drawing characters.

        PR numbers are wrapped in Rich link markup if a URL is available.

        Returns:
            Rendered tree string, or empty string if no branches.
        """
        if not self.roots:
            return ""

        def get_node_label(node: BranchNode) -> str:
            """Get the display label for a node."""
            marker = "◉" if node.is_current else "◯"

            # PR suffix with Rich link if URL available
            pr_suffix = ""
            if node.pr_number:
                pr_text = f"#{node.pr_number}"
                if node.pr_url:
                    pr_text = f"[cyan underline link={node.pr_url}]{pr_text}[/]"
                pr_suffix = f" {pr_text}"
                if node.pr_is_merged:
                    pr_suffix += " [dim]merged[/]"
                elif node.pr_is_closed:
                    pr_suffix += " [dim]closed[/]"
                elif node.pr_is_draft:
                    pr_suffix += " [dim]draft[/]"

            # Warning/status suffix
            suffix = " (current)" if node.is_current else ""
            if node.warning == BranchWarning.ORPHAN:
                suffix += " (parent missing)"
            elif node.warning == BranchWarning.CYCLE:
                suffix += " (circular ref)"

            return f"{marker} {node.name}{pr_suffix}{suffix}"

        def render_branch(node: BranchNode, prefix: str = "") -> list[str]:
            """Render a single branch and its children, bottom-up."""
            result: list[str] = []

            if not node.children:
                # Leaf node
                result.append(f"{prefix}{get_node_label(node)}")
                return result

            if len(node.children) == 1:
                # Single child - linear stack rendering
                child = node.children[0]
                child_lines = render_branch(child, prefix)
                result.extend(child_lines)
                result.append(f"{prefix}│")
                result.append(f"{prefix}{get_node_label(node)}")
                return result

            # Multiple children - parallel stacks rendering
            for i, child in enumerate(node.children):
                # Render child's subtree with no local prefix
                child_lines = render_branch(child, "")

                # Add connector line at the end of this stack
                child_lines.append("│")

                # Calculate column prefix: outer prefix + "│ " for each previous column
                column_prefix = prefix + "│ " * i

                # Add each line with the column prefix
                for line in child_lines:
                    result.append(f"{column_prefix}{line}")

            # Render the parent with merge connectors
            # Format: ◉─┴─┴─ name (for 3 children)
            marker = "◉" if node.is_current else "◯"

            # PR suffix with Rich link if URL available
            pr_suffix = ""
            if node.pr_number:
                pr_text = f"#{node.pr_number}"
                if node.pr_url:
                    pr_text = f"[cyan underline link={node.pr_url}]{pr_text}[/]"
                pr_suffix = f" {pr_text}"
                if node.pr_is_merged:
                    pr_suffix += " [dim]merged[/]"
                elif node.pr_is_closed:
                    pr_suffix += " [dim]closed[/]"
                elif node.pr_is_draft:
                    pr_suffix += " [dim]draft[/]"

            suffix = " (current)" if node.is_current else ""
            if node.warning == BranchWarning.ORPHAN:
                suffix += " (parent missing)"
            elif node.warning == BranchWarning.CYCLE:
                suffix += " (circular ref)"

            # Format: ◯─┘ (2 children), ◯─┴─┘ (3 children), etc.
            if len(node.children) == 2:
                merge_connector = "─┘"
            else:
                merge_connector = "─" + "┴─" * (len(node.children) - 2) + "┘"
            result.append(
                f"{prefix}{marker}{merge_connector} {node.name}{pr_suffix}{suffix}"
            )

            return result

        # Handle single or multiple roots
        if len(self.roots) == 1:
            lines = render_branch(self.roots[0])
        else:
            # Multiple independent roots - render each separately
            lines = []
            for root in self.roots:
                if lines:
                    lines.append("")  # Empty line between roots
                lines.extend(render_branch(root))

        return "\n".join(lines)

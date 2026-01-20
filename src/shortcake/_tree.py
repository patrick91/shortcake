from dataclasses import dataclass, field


@dataclass
class BranchNode:
    """A node in the branch tree."""

    name: str
    parent_name: str | None = None
    children: list["BranchNode"] = field(default_factory=list)
    is_current: bool = False


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
        # Create nodes for all tracked branches
        nodes: dict[str, BranchNode] = {}

        for branch_name, parent_name in branches.items():
            nodes[branch_name] = BranchNode(
                name=branch_name,
                parent_name=parent_name,
                is_current=(branch_name == current),
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

        # Build parent-child relationships
        for branch_name, parent_name in branches.items():
            if parent_name and parent_name in nodes:
                nodes[parent_name].children.append(nodes[branch_name])

        # Find root nodes (nodes with no parent or parent not in nodes)
        roots: list[BranchNode] = []
        for node in nodes.values():
            if node.parent_name is None or node.parent_name not in nodes:
                roots.append(node)

        # Sort roots and all children alphabetically for consistent output
        roots.sort(key=lambda n: n.name)
        for node in nodes.values():
            node.children.sort(key=lambda n: n.name)

        return cls(roots=roots)

    def render(self) -> str:
        """
        Render the tree as a string with box-drawing characters.

        Returns:
            Rendered tree string, or empty string if no branches.
        """
        if not self.roots:
            return ""

        lines: list[str] = []

        def render_branch(node: BranchNode, prefix: str = "") -> list[str]:
            """Render a single branch and its children, bottom-up."""
            result: list[str] = []

            # Recursively render children first (they appear above parent)
            for child in node.children:
                child_lines = render_branch(child, prefix)
                result.extend(child_lines)

            # Render connector line if this node has children
            if node.children:
                result.append(f"{prefix}│")

            # Render this node
            marker = "◉" if node.is_current else "◯"
            suffix = " (current)" if node.is_current else ""
            result.append(f"{prefix}{marker} {node.name}{suffix}")

            return result

        # Handle multiple roots (parallel stacks)
        if len(self.roots) == 1:
            # Single stack - simple rendering
            root = self.roots[0]
            lines = render_branch(root)
        else:
            # Multiple stacks converging on a common root
            # Find if there's a common root among all trees
            # For now, render each stack with proper indentation

            # Collect all lines for each root's tree
            all_stack_lines: list[list[str]] = []
            for root in self.roots:
                stack_lines = render_branch(root)
                all_stack_lines.append(stack_lines)

            # Check if all roots share a common parent (convergence point)
            # This would be shown with ─┴─ connectors
            # For simplicity, we'll handle the case where roots are independent

            # Find the common base if roots have the same parent
            common_parents = {
                r.parent_name for r in self.roots if r.parent_name is not None
            }

            if len(common_parents) == 1 and common_parents.pop() is not None:
                # All roots share a common untracked parent - this shouldn't happen
                # in typical usage since the parent would be a root
                pass

            # Render stacks side by side with proper connectors
            lines = self._render_parallel_stacks(all_stack_lines)

        return "\n".join(lines)

    def _render_parallel_stacks(self, stacks: list[list[str]]) -> list[str]:
        """Render multiple parallel stacks with merge connectors."""
        if len(stacks) == 1:
            return stacks[0]

        result: list[str] = []

        # Find max height for alignment
        max_height = max(len(s) for s in stacks)

        # Pad shorter stacks
        padded_stacks: list[list[str]] = []
        for stack in stacks:
            padding = max_height - len(stack)
            padded = [""] * padding + stack
            padded_stacks.append(padded)

        # Check if all stacks end with the same root (common base)
        last_lines = [s[-1] if s else "" for s in stacks]
        all_same_root = len(set(last_lines)) == 1 and last_lines[0]

        # Render line by line
        for i in range(max_height):
            combined_parts: list[str] = []

            for j, stack in enumerate(padded_stacks):
                line = stack[i] if i < len(stack) else ""

                if j == 0:
                    combined_parts.append(line)
                else:
                    # Add separator/connector
                    if i == max_height - 1 and all_same_root:
                        # Last line with common root - use merge connector
                        # Skip this stack's last line, we'll handle it below
                        pass
                    elif line:
                        # Add prefix for nested stack
                        combined_parts.append(f"│ {line}")
                    elif stack[i] == "":
                        # Empty padding line
                        combined_parts.append("│")

            if i == max_height - 1 and all_same_root:
                # Render the merged root line
                base_line = last_lines[0]
                # Replace the marker with merge connector
                if base_line.startswith("◉"):
                    merged = "◉" + "─┴─" * (len(stacks) - 1) + base_line[1:]
                elif base_line.startswith("◯"):
                    merged = "◯" + "─┴─" * (len(stacks) - 1) + base_line[1:]
                else:
                    merged = base_line
                result.append(merged)
            else:
                result.append(" ".join(combined_parts))

        return result

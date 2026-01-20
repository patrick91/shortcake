from inline_snapshot import snapshot

from shortcake._tree import BranchNode, BranchWarning, StackTree


def test_build_simple_stack() -> None:
    """Test building a simple single-branch stack off main."""
    branches = {"feature": "main"}
    all_branches = {"main", "feature"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)

    assert len(tree.roots) == 1
    root = tree.roots[0]
    assert root.name == "main"
    assert root.is_current is False
    assert len(root.children) == 1
    assert root.children[0].name == "feature"
    assert root.children[0].is_current is True


def test_build_multi_level() -> None:
    """Test building A → B → C chain."""
    branches = {
        "feature-a": "main",
        "feature-b": "feature-a",
        "feature-c": "feature-b",
    }
    all_branches = {"main", "feature-a", "feature-b", "feature-c"}
    current = "main"

    tree = StackTree.build(branches, all_branches, current)

    assert len(tree.roots) == 1
    root = tree.roots[0]
    assert root.name == "main"
    assert root.is_current is True
    assert len(root.children) == 1

    child_a = root.children[0]
    assert child_a.name == "feature-a"
    assert len(child_a.children) == 1

    child_b = child_a.children[0]
    assert child_b.name == "feature-b"
    assert len(child_b.children) == 1

    child_c = child_b.children[0]
    assert child_c.name == "feature-c"
    assert len(child_c.children) == 0


def test_build_multiple_roots() -> None:
    """Test building multiple independent stacks."""
    branches = {
        "stack-1": "main",
        "stack-2": "develop",
    }
    all_branches = {"main", "develop", "stack-1", "stack-2"}
    current = None

    tree = StackTree.build(branches, all_branches, current)

    # Should have two roots: develop and main (alphabetically sorted)
    assert len(tree.roots) == 2
    assert tree.roots[0].name == "develop"
    assert tree.roots[1].name == "main"


def test_build_multiple_children_same_parent() -> None:
    """Test building multiple branches from same parent."""
    branches = {
        "feature-a": "main",
        "feature-b": "main",
    }
    all_branches = {"main", "feature-a", "feature-b"}
    current = "main"

    tree = StackTree.build(branches, all_branches, current)

    assert len(tree.roots) == 1
    root = tree.roots[0]
    assert root.name == "main"
    assert len(root.children) == 2
    # Children should be sorted alphabetically
    assert root.children[0].name == "feature-a"
    assert root.children[1].name == "feature-b"


def test_render_empty() -> None:
    """Test rendering empty tree returns empty string."""
    tree = StackTree(roots=[])
    assert tree.render() == ""


def test_render_single_branch() -> None:
    """Test rendering a simple single-branch stack."""
    child = BranchNode(name="feature", is_current=True)
    root = BranchNode(name="main", children=[child])

    tree = StackTree(roots=[root])
    output = tree.render()

    lines = output.split("\n")
    assert "◉ feature (current)" in lines
    assert "│" in lines
    assert "◯ main" in lines


def test_render_current_highlighted() -> None:
    """Test that current branch shows ◉ marker."""
    child = BranchNode(name="feature", is_current=False)
    root = BranchNode(name="main", is_current=True, children=[child])

    tree = StackTree(roots=[root])
    output = tree.render()

    assert "◯ feature" in output
    assert "◉ main (current)" in output


def test_render_multi_level() -> None:
    """Test rendering A → B → C chain."""
    child_c = BranchNode(name="feature-c")
    child_b = BranchNode(name="feature-b", children=[child_c])
    child_a = BranchNode(name="feature-a", children=[child_b])
    root = BranchNode(name="main", is_current=True, children=[child_a])

    tree = StackTree(roots=[root])
    output = tree.render()

    lines = output.split("\n")
    # Verify order: feature-c should be first (top), main last (bottom)
    feature_c_idx = next(i for i, line in enumerate(lines) if "feature-c" in line)
    feature_b_idx = next(i for i, line in enumerate(lines) if "feature-b" in line)
    feature_a_idx = next(i for i, line in enumerate(lines) if "feature-a" in line)
    main_idx = next(i for i, line in enumerate(lines) if "main" in line)

    assert feature_c_idx < feature_b_idx < feature_a_idx < main_idx


def test_branch_node_defaults() -> None:
    """Test BranchNode default values."""
    node = BranchNode(name="test")
    assert node.name == "test"
    assert node.parent_name is None
    assert node.children == []
    assert node.is_current is False


def test_build_orphan_branch() -> None:
    """Test handling branch whose parent doesn't exist."""
    branches = {"feature": "deleted-branch"}
    all_branches = {"main", "feature"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)

    # Feature should be a root since its parent doesn't exist
    assert len(tree.roots) == 1
    assert tree.roots[0].name == "feature"
    assert tree.roots[0].is_current is True


def test_build_no_tracked_branches() -> None:
    """Test building with no tracked branches."""
    branches: dict[str, str | None] = {}
    all_branches = {"main", "feature"}
    current = "main"

    tree = StackTree.build(branches, all_branches, current)

    assert len(tree.roots) == 0


def test_render_parallel_stacks_same_root() -> None:
    """Test rendering multiple stacks converging on same root."""
    child1 = BranchNode(name="stack-1-a")
    child2 = BranchNode(name="stack-2-a")
    root = BranchNode(name="main", is_current=True, children=[child1, child2])

    tree = StackTree(roots=[root])
    output = tree.render()

    # Both stacks should be present
    assert "stack-1-a" in output
    assert "stack-2-a" in output
    assert "main" in output


def test_render_parallel_stacks_different_roots() -> None:
    """Test rendering multiple independent stacks."""
    child1 = BranchNode(name="feature-a")
    root1 = BranchNode(name="main", children=[child1])

    child2 = BranchNode(name="feature-b")
    root2 = BranchNode(name="develop", is_current=True, children=[child2])

    tree = StackTree(roots=[root1, root2])
    output = tree.render()

    assert "feature-a" in output
    assert "feature-b" in output
    assert "main" in output
    assert "develop" in output


def test_render_parallel_stacks_with_merge_connector() -> None:
    """Test rendering merge connector when multiple stacks share same base."""
    # Two stacks converging on same root
    stack1_top = BranchNode(name="stack-1-b")
    stack1_mid = BranchNode(name="stack-1-a", children=[stack1_top])

    stack2_top = BranchNode(name="stack-2-b")
    stack2_mid = BranchNode(name="stack-2-a", children=[stack2_top])

    root = BranchNode(name="main", is_current=True, children=[stack1_mid, stack2_mid])

    tree = StackTree(roots=[root])
    output = tree.render()

    # Should contain all branches
    assert "stack-1-a" in output
    assert "stack-1-b" in output
    assert "stack-2-a" in output
    assert "stack-2-b" in output
    assert "main" in output


def test_render_parallel_stacks_single() -> None:
    """Test _render_parallel_stacks with single stack."""
    tree = StackTree(roots=[])
    result = tree._render_parallel_stacks([["line1", "line2"]])
    assert result == ["line1", "line2"]


def test_render_parallel_stacks_empty_lines() -> None:
    """Test rendering parallel stacks with different heights."""
    stack1 = ["◯ tall-branch", "│", "◯ mid", "│", "◯ base"]
    stack2 = ["◯ short", "│", "◯ base"]

    tree = StackTree(roots=[])
    result = tree._render_parallel_stacks([stack1, stack2])

    assert len(result) == 5  # Max height
    # Verify output contains both stacks
    output = "\n".join(result)
    assert "tall-branch" in output
    assert "short" in output


def test_render_parallel_stacks_common_base_not_current() -> None:
    """Test merge connector with non-current root (◯ marker)."""
    stack1 = ["◯ stack-1", "│", "◯ base"]
    stack2 = ["◯ stack-2", "│", "◯ base"]

    tree = StackTree(roots=[])
    result = tree._render_parallel_stacks([stack1, stack2])

    output = "\n".join(result)
    # Should have merge connector with ◯
    assert "◯─┴─" in output


def test_render_parallel_stacks_common_base_is_current() -> None:
    """Test merge connector with current root (◉ marker)."""
    stack1 = ["◯ stack-1", "│", "◉ base (current)"]
    stack2 = ["◯ stack-2", "│", "◉ base (current)"]

    tree = StackTree(roots=[])
    result = tree._render_parallel_stacks([stack1, stack2])

    output = "\n".join(result)
    # Should have merge connector with ◉
    assert "◉─┴─" in output


def test_render_parallel_stacks_unknown_marker() -> None:
    """Test merge connector fallback when base doesn't start with known marker."""
    stack1 = ["◯ stack-1", "│", "? base"]
    stack2 = ["◯ stack-2", "│", "? base"]

    tree = StackTree(roots=[])
    result = tree._render_parallel_stacks([stack1, stack2])

    output = "\n".join(result)
    # Should contain the base line as-is
    assert "? base" in output


def test_build_circular_reference_two_branches() -> None:
    """Test handling circular reference: A -> B -> A."""
    branches = {"branch-a": "branch-b", "branch-b": "branch-a"}
    all_branches = {"branch-a", "branch-b"}
    current = "branch-a"

    tree = StackTree.build(branches, all_branches, current)

    # Both branches should be roots (cycle broken)
    assert len(tree.roots) == 2
    root_names = {r.name for r in tree.roots}
    assert root_names == {"branch-a", "branch-b"}

    # Both should have cycle warning
    for root in tree.roots:
        assert root.warning == BranchWarning.CYCLE


def test_build_self_reference() -> None:
    """Test handling self-reference: A -> A."""
    branches = {"branch-a": "branch-a"}
    all_branches = {"branch-a", "main"}
    current = "branch-a"

    tree = StackTree.build(branches, all_branches, current)

    assert len(tree.roots) == 1
    assert tree.roots[0].name == "branch-a"
    assert tree.roots[0].warning == BranchWarning.CYCLE


def test_build_longer_cycle() -> None:
    """Test handling longer cycle: A -> B -> C -> A."""
    branches = {
        "branch-a": "branch-b",
        "branch-b": "branch-c",
        "branch-c": "branch-a",
    }
    all_branches = {"branch-a", "branch-b", "branch-c"}
    current = "branch-a"

    tree = StackTree.build(branches, all_branches, current)

    # All three should be roots
    assert len(tree.roots) == 3
    for root in tree.roots:
        assert root.warning == BranchWarning.CYCLE


def test_render_circular_reference_warning() -> None:
    """Test that circular reference shows warning in output."""
    branches = {"branch-a": "branch-b", "branch-b": "branch-a"}
    all_branches = {"branch-a", "branch-b"}
    current = "branch-a"

    tree = StackTree.build(branches, all_branches, current)
    output = tree.render()

    assert "(circular ref)" in output
    assert "branch-a" in output
    assert "branch-b" in output


def test_render_orphan_warning() -> None:
    """Test that orphan branch shows warning in output."""
    branches = {"feature": "deleted-branch"}
    all_branches = {"main", "feature"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)
    output = tree.render()

    assert "(parent missing)" in output
    assert "feature" in output


def test_build_orphan_has_warning() -> None:
    """Test that orphan branch has correct warning set."""
    branches = {"feature": "deleted-branch"}
    all_branches = {"main", "feature"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)

    assert len(tree.roots) == 1
    assert tree.roots[0].warning == BranchWarning.ORPHAN


def test_build_mixed_normal_and_cycle() -> None:
    """Test tree with both normal branches and a cycle."""
    branches = {
        "feature": "main",  # Normal
        "cycle-a": "cycle-b",  # Cycle
        "cycle-b": "cycle-a",  # Cycle
    }
    all_branches = {"main", "feature", "cycle-a", "cycle-b"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)

    # Should have 3 roots: main (with feature child), cycle-a, cycle-b
    assert len(tree.roots) == 3
    root_names = {r.name for r in tree.roots}
    assert root_names == {"main", "cycle-a", "cycle-b"}

    # Find the main root and verify it has feature as child
    main_root = next(r for r in tree.roots if r.name == "main")
    assert len(main_root.children) == 1
    assert main_root.children[0].name == "feature"
    assert main_root.children[0].warning is None  # No warning for normal branch


# Snapshot tests for rendering output


def test_snapshot_simple_stack() -> None:
    """Snapshot test for simple single-branch stack."""
    branches = {"feature": "main"}
    all_branches = {"main", "feature"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot("""\
◉ feature (current)
│
◯ main\
""")


def test_snapshot_multi_level_stack() -> None:
    """Snapshot test for A → B → C chain."""
    branches = {
        "feature-a": "main",
        "feature-b": "feature-a",
        "feature-c": "feature-b",
    }
    all_branches = {"main", "feature-a", "feature-b", "feature-c"}
    current = "feature-c"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot("""\
◉ feature-c (current)
│
◯ feature-b
│
◯ feature-a
│
◯ main\
""")


def test_snapshot_multiple_children() -> None:
    """Snapshot test for multiple branches from same parent."""
    branches = {
        "feature-a": "main",
        "feature-b": "main",
        "feature-c": "main",
    }
    all_branches = {"main", "feature-a", "feature-b", "feature-c"}
    current = "main"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot("""\
◯ feature-a
│
◯ feature-b
│
◯ feature-c
│
◉ main (current)\
""")


def test_snapshot_current_not_on_tracked() -> None:
    """Snapshot test when current branch is not tracked."""
    branches = {"feature": "main"}
    all_branches = {"main", "feature", "other"}
    current = "other"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot("""\
◯ feature
│
◯ main\
""")


def test_snapshot_orphan_branch() -> None:
    """Snapshot test for orphan branch warning."""
    branches = {"feature": "deleted-branch"}
    all_branches = {"main", "feature"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot("◉ feature (current) (parent missing)")


def test_snapshot_circular_reference() -> None:
    """Snapshot test for circular reference warning."""
    branches = {"branch-a": "branch-b", "branch-b": "branch-a"}
    all_branches = {"branch-a", "branch-b"}
    current = "branch-a"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot(
        "◉ branch-a (current) (circular ref) │ ◯ branch-b (circular ref)"
    )


def test_snapshot_self_reference() -> None:
    """Snapshot test for self-reference warning."""
    branches = {"feature": "feature"}
    all_branches = {"main", "feature"}
    current = "feature"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot("◉ feature (current) (circular ref)")


def test_snapshot_complex_tree() -> None:
    """Snapshot test for complex tree with multiple stacks."""
    branches = {
        "feature-a": "main",
        "feature-a-1": "feature-a",
        "feature-b": "main",
    }
    all_branches = {"main", "feature-a", "feature-a-1", "feature-b"}
    current = "feature-a-1"

    tree = StackTree.build(branches, all_branches, current)
    assert tree.render() == snapshot("""\
◉ feature-a-1 (current)
│
◯ feature-a
│
◯ feature-b
│
◯ main\
""")

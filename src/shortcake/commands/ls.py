import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._cache import load_pr_cache
from shortcake._tree import BranchNode, StackTree


def _build_tree(repo: Repo) -> tuple[StackTree, set[str]]:
    """
    Build tree structure from repo.

    Returns:
        Tuple of (StackTree, set of tracked branch names)
    """
    all_branches = set(git.get_all_local_branches(repo))
    current = git.get_current_branch(repo)
    branch_heads = {b: git.get_branch_head(repo, b) for b in all_branches}

    branches: dict[str, str | None] = {}
    for branch in all_branches:
        parent = git.get_branch_parent(repo, branch, all_branches, branch_heads)
        if parent is not None:
            branches[branch] = parent

    tree = StackTree.build(branches, all_branches, current)
    return tree, set(branches.keys())


def _collect_nodes(tree: StackTree) -> list[BranchNode]:
    """Collect all nodes from tree for iteration."""
    nodes: list[BranchNode] = []

    def collect(node: BranchNode) -> None:
        nodes.append(node)
        for child in node.children:
            collect(child)

    for root in tree.roots:
        collect(root)
    return nodes


def _ls(repo: Repo) -> str:
    """
    Build and render tree of tracked branches (without PR info).

    Returns:
        Rendered tree string, empty if no tracked branches.
    """
    tree, _ = _build_tree(repo)
    return tree.render()


def ls() -> None:
    """List all tracked branches as a tree."""
    repo = git.open_repo()

    # Build tree
    tree, tracked_branches = _build_tree(repo)
    if not tree.roots:
        typer.echo("No tracked branches found.")
        return

    # Load PR info from cache
    pr_cache = load_pr_cache(repo)

    # Apply cached PR info to nodes
    branch_nodes = _collect_nodes(tree)
    for node in branch_nodes:
        if node.name in tracked_branches and node.name in pr_cache:
            cached = pr_cache[node.name]
            node.pr_number = cached.number
            node.pr_is_draft = cached.is_draft
            node.pr_is_merged = cached.is_merged

    typer.echo(tree.render())

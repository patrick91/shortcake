import typer
from dulwich.repo import Repo
from rich.live import Live
from rich.text import Text

from shortcake import _git as git
from shortcake._github import GitHubClient, get_github_token, get_repo_info
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

    # Build tree (without PR info)
    tree, tracked_branches = _build_tree(repo)
    if not tree.roots:
        typer.echo("No tracked branches found.")
        return

    # Check if we can fetch PR info
    token = get_github_token()
    repo_info = get_repo_info(repo)

    if not token or not repo_info:
        # No GitHub access, just print tree
        typer.echo(tree.render())
        return

    # Live update: show tree, then fill in PR info
    owner, repo_name = repo_info
    branch_nodes = _collect_nodes(tree)

    with Live(Text(tree.render()), refresh_per_second=4) as live:
        try:
            with GitHubClient(token, owner, repo_name) as gh:
                for node in branch_nodes:
                    # Skip untracked branches (like main)
                    if node.name not in tracked_branches:
                        continue
                    try:
                        pr = gh.get_pr_for_branch(node.name)
                        if pr:
                            node.pr_number = pr.number
                            node.pr_is_draft = pr.is_draft
                        else:
                            # Check for merged PR
                            merged_num = gh.get_merged_pr_number(node.name)
                            if merged_num:
                                node.pr_number = merged_num
                                node.pr_is_merged = True
                        # Update display
                        live.update(Text(tree.render()))
                    except Exception:
                        continue  # Skip on error
        except Exception:
            pass  # GitHub errors don't break the command

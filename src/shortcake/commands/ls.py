from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live

from shortcake import _git as git
from shortcake._cache import load_pr_cache, update_pr_cache
from shortcake._git._core import Repo
from shortcake._github import GitHubClient, get_github_token, get_repo_info
from shortcake._tree import BranchNode, StackTree

console = Console(highlight=False)


def _build_tree(repo: Repo) -> tuple[StackTree, set[str]]:
    """
    Build tree structure from repo.

    Returns:
        Tuple of (StackTree, set of tracked branch names)
    """
    all_branches = set(git.get_all_local_branches(repo))
    current = git.get_current_branch(repo)
    default_branch = git.get_default_branch(repo)
    branch_heads = {b: git.get_branch_head(repo, b) for b in all_branches}

    trunk_head = branch_heads.get(default_branch)

    branches: dict[str, str | None] = {}
    for branch in all_branches:
        # Skip trunk — after ff-merging tracked branches, trunk's commit
        # history contains Shortcake-Parent trailers from merged commits,
        # which would incorrectly make trunk appear "tracked".
        if branch == default_branch:
            continue
        parent = git.get_branch_parent(
            repo, branch, all_branches, branch_heads, trunk_head
        )
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


def _fetch_pr_info(repo: Repo, tree: StackTree, tracked_branches: set[str]) -> None:
    """Fetch PR info from GitHub and update cache.

    Args:
        repo: The repository.
        tree: The stack tree.
        tracked_branches: Set of tracked branch names.
    """
    token = get_github_token()
    repo_info = get_repo_info(repo)

    if not token or not repo_info:
        console.print(
            "Cannot fetch PR info: no GitHub token or not a GitHub repo",
            style="bold red",
        )
        return

    owner, repo_name = repo_info
    branch_nodes = _collect_nodes(tree)

    with Live(console=console, refresh_per_second=4) as live:
        live.update(tree.render())
        try:
            with GitHubClient(token, owner, repo_name) as gh:
                for node in branch_nodes:
                    if node.name not in tracked_branches:
                        continue
                    try:
                        pr = gh.get_pr_for_branch(node.name)
                        if pr:
                            node.pr_number = pr.number
                            node.pr_is_draft = pr.is_draft
                            node.pr_url = pr.url
                            update_pr_cache(
                                repo,
                                node.name,
                                pr.number,
                                is_draft=pr.is_draft,
                                url=pr.url,
                            )
                        else:
                            closed_num, is_merged = gh.get_closed_pr_info(node.name)
                            if closed_num:
                                pr_url = f"https://github.com/{owner}/{repo_name}/pull/{closed_num}"
                                node.pr_number = closed_num
                                node.pr_url = pr_url
                                node.pr_is_merged = is_merged
                                node.pr_is_closed = not is_merged
                                update_pr_cache(
                                    repo,
                                    node.name,
                                    closed_num,
                                    is_merged=is_merged,
                                    is_closed=not is_merged,
                                    url=pr_url,
                                )
                        live.update(tree.render())
                    except Exception:
                        continue
        except Exception:
            pass


def ls(
    refresh: Annotated[
        bool,
        typer.Option("--refresh", "-r", help="Refresh PR info from GitHub"),
    ] = False,
) -> None:
    """List all tracked branches as a tree."""
    repo = git.open_repo()

    # Build tree
    tree, tracked_branches = _build_tree(repo)
    if not tree.roots:
        console.print("No tracked branches found.")
        return

    branch_nodes = _collect_nodes(tree)

    if refresh:
        # Fetch from GitHub and update cache
        _fetch_pr_info(repo, tree, tracked_branches)
    else:
        # Load PR info from cache
        pr_cache = load_pr_cache(repo)
        for node in branch_nodes:
            if node.name in tracked_branches and node.name in pr_cache:
                cached = pr_cache[node.name]
                node.pr_number = cached.number
                node.pr_is_draft = cached.is_draft
                node.pr_is_merged = cached.is_merged
                node.pr_is_closed = cached.is_closed
                node.pr_url = cached.url

        console.print(tree.render())

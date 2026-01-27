import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._tree import StackTree


def _ls(repo: Repo) -> str:
    """
    Build and render tree of tracked branches.

    Returns:
        Rendered tree string, empty if no tracked branches.
    """
    # Get all branches
    all_branches = set(git.get_all_local_branches(repo))

    # Get current branch (None if detached HEAD)
    current = git.get_current_branch(repo)

    # Precompute all branch heads once (O(n) instead of O(n²))
    branch_heads = {b: git.get_branch_head(repo, b) for b in all_branches}

    # Find parent for each branch
    branches: dict[str, str | None] = {}
    for branch in all_branches:
        parent = git.get_branch_parent(repo, branch, all_branches, branch_heads)
        if parent is not None:
            branches[branch] = parent

    # Build and render tree
    tree = StackTree.build(branches, all_branches, current)
    return tree.render()


def ls() -> None:
    """List all tracked branches as a tree."""
    repo = git.open_repo()
    output = _ls(repo)

    if not output:
        typer.echo("No tracked branches found.")
        return

    typer.echo(output)

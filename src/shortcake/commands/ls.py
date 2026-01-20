import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake._tree import StackTree


def _get_branch_parent(repo: Repo, branch: str, all_branches: set[str]) -> str | None:
    """
    Get parent from Shortcake-Parent trailer in first commit.

    Walks commits from branch head to find the first commit that has the trailer,
    or until we reach a commit that's on another branch.

    Args:
        repo: The git repository
        branch: The branch name to check
        all_branches: Set of all branch names for determining boundaries

    Returns:
        Parent branch name if found, None otherwise
    """
    branch_head = git.get_branch_head(repo, branch)

    # Get heads of other branches to know where to stop
    other_branch_heads: set[bytes] = set()
    for other_branch in all_branches:
        if other_branch != branch:
            other_branch_heads.add(git.get_branch_head(repo, other_branch))

    # Walk commits from branch head
    seen: set[bytes] = set()
    to_visit = [branch_head]

    while to_visit:
        commit_sha = to_visit.pop(0)

        if commit_sha in seen:
            continue
        seen.add(commit_sha)

        # Stop if we've reached another branch's head
        if commit_sha in other_branch_heads:
            continue

        message = git.get_commit_message(repo, commit_sha)
        trailers = Trailers.from_message(message)
        if trailers.parent_branch is not None:
            return trailers.parent_branch

        # Add parents to visit
        commit = repo[commit_sha]
        for parent_sha in commit.parents:
            if parent_sha not in seen:
                to_visit.append(parent_sha)

    return None


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

    # Find parent for each branch
    branches: dict[str, str | None] = {}
    for branch in all_branches:
        parent = _get_branch_parent(repo, branch, all_branches)
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

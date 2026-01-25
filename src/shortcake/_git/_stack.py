"""Shortcake-specific stack operations: parent/children, tracked branches."""

from dulwich.repo import Repo

from shortcake._git._core import (
    get_all_local_branches,
    get_branch_head,
    get_commit_message,
)
from shortcake._git._rebase import is_ancestor
from shortcake._trailers import Trailers


def get_branch_parent(repo: Repo, branch: str, all_branches: set[str]) -> str | None:
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

    branch_head = get_branch_head(repo, branch)

    # Get heads of other branches to know where to stop
    other_branch_heads: set[bytes] = set()
    for other_branch in all_branches:
        if other_branch != branch:
            other_branch_heads.add(get_branch_head(repo, other_branch))

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

        message = get_commit_message(repo, commit_sha)
        trailers = Trailers.from_message(message)
        # A branch cannot be its own parent (can happen if merged commits
        # with trailers end up in the trunk)
        if trailers.parent_branch is not None and trailers.parent_branch != branch:
            return trailers.parent_branch

        # Add parents to visit
        commit = repo[commit_sha]
        for parent_sha in commit.parents:
            if parent_sha not in seen:
                to_visit.append(parent_sha)

    return None


def get_branch_children(repo: Repo, branch: str) -> list[str]:
    """
    Get all branches whose parent is the given branch.

    Args:
        repo: The git repository
        branch: The branch name to find children for

    Returns:
        Sorted list of branch names that have this branch as parent
    """
    all_branches = set(get_all_local_branches(repo))
    children = []
    for potential_child in all_branches:
        if potential_child == branch:
            continue
        parent = get_branch_parent(repo, potential_child, all_branches)
        if parent == branch:
            children.append(potential_child)
    return sorted(children)


def get_tracked_branches(repo: Repo) -> list[str]:
    """Get all tracked branches (those with Shortcake-Parent trailer)."""
    all_branches = set(get_all_local_branches(repo))
    tracked = []
    for branch in all_branches:
        parent = get_branch_parent(repo, branch, all_branches)
        if parent is not None:
            tracked.append(branch)
    return sorted(tracked)


def is_merged(repo: Repo, branch: str, trunk: str) -> bool:
    """Check if branch is merged into trunk.

    A branch is merged if its head is an ancestor of trunk head.
    """
    branch_head = get_branch_head(repo, branch)
    trunk_head = get_branch_head(repo, trunk)
    return is_ancestor(repo, branch_head, trunk_head)


def get_merged_branches(
    repo: Repo, tracked_branches: list[str], trunk: str
) -> list[str]:
    """Get tracked branches that are merged into trunk."""
    merged = []
    for branch in tracked_branches:
        if is_merged(repo, branch, trunk):
            merged.append(branch)
    return merged

"""Shortcake-specific stack operations: parent/children, tracked branches."""

from dulwich.repo import Repo

from shortcake._git._core import (
    get_all_local_branches,
    get_branch_head,
    get_commit_message,
)
from shortcake._git._rebase import is_ancestor
from shortcake._trailers import Trailers


def get_branch_parent(
    repo: Repo,
    branch: str,
    all_branches: set[str],
    branch_heads: dict[str, bytes] | None = None,
) -> str | None:
    """
    Get parent from Shortcake-Parent trailer in first commit.

    Walks commits from branch head to find the first commit that has the trailer,
    or until we reach a commit that's on another branch.

    Args:
        repo: The git repository
        branch: The branch name to check
        all_branches: Set of all branch names for determining boundaries
        branch_heads: Optional precomputed dict of branch name -> head SHA.
                      If provided, avoids redundant get_branch_head() calls.

    Returns:
        Parent branch name if found, None otherwise
    """

    # Use precomputed head if available, otherwise fetch it
    if branch_heads is not None:
        branch_head = branch_heads[branch]
    else:
        branch_head = get_branch_head(repo, branch)

    # Get heads of other branches to know where to stop
    # Use precomputed heads if provided (O(n) -> O(1) per call)
    if branch_heads is not None:
        other_branch_heads = {sha for b, sha in branch_heads.items() if b != branch}
    else:
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

    # Precompute ALL branch heads once (O(n) total instead of O(n²))
    branch_heads = {b: get_branch_head(repo, b) for b in all_branches}

    children = []
    for potential_child in all_branches:
        if potential_child == branch:
            continue
        # Pass precomputed heads to avoid redundant lookups
        parent = get_branch_parent(repo, potential_child, all_branches, branch_heads)
        if parent == branch:
            children.append(potential_child)
    return sorted(children)


def get_tracked_branches(repo: Repo) -> list[str]:
    """Get all tracked branches (those with Shortcake-Parent trailer)."""
    all_branches = set(get_all_local_branches(repo))

    # Precompute ALL branch heads once (O(n) total instead of O(n²))
    branch_heads = {b: get_branch_head(repo, b) for b in all_branches}

    tracked = []
    for branch in all_branches:
        # Pass precomputed heads to avoid redundant lookups
        parent = get_branch_parent(repo, branch, all_branches, branch_heads)
        if parent is not None:
            tracked.append(branch)
    return sorted(tracked)


def is_merged(repo: Repo, branch: str, trunk: str) -> bool:
    """Check if branch is merged into trunk (regular merge).

    A branch is merged if its head is an ancestor of trunk head.
    """
    branch_head = get_branch_head(repo, branch)
    trunk_head = get_branch_head(repo, trunk)
    return is_ancestor(repo, branch_head, trunk_head)


def is_squash_merged(repo: Repo, branch: str, trunk: str) -> bool:
    """Check if branch was squash-merged into trunk.

    A branch is squash-merged if its tree changes are already in trunk,
    even though its commits aren't ancestors of trunk.

    This compares the trees: if the branch's changes relative to the
    merge-base are already present in trunk, it's considered merged.
    """
    branch_head = get_branch_head(repo, branch)
    trunk_head = get_branch_head(repo, trunk)

    # Find merge base
    from dulwich.walk import Walker

    branch_ancestors = set()
    for entry in Walker(repo.object_store, [branch_head]):
        branch_ancestors.add(entry.commit.id)

    merge_base = None
    for entry in Walker(repo.object_store, [trunk_head]):
        if entry.commit.id in branch_ancestors:
            merge_base = entry.commit.id
            break

    if merge_base is None:
        return False  # No common ancestor

    # Get trees
    merge_base_tree = repo[merge_base].tree
    branch_tree = repo[branch_head].tree
    trunk_tree = repo[trunk_head].tree

    # If branch tree equals merge base, branch has no changes
    if branch_tree == merge_base_tree:
        return True

    # If branch tree equals trunk tree, all changes are in trunk
    if branch_tree == trunk_tree:
        return True

    # Check if all files changed in branch are the same in trunk
    # Get diff from merge_base to branch
    from dulwich.diff_tree import tree_changes

    branch_changes = {}
    for change in tree_changes(repo.object_store, merge_base_tree, branch_tree):
        # change is (oldpath, newpath), (oldmode, newmode), (oldsha, newsha)
        path = change.new.path if change.new.path else change.old.path
        if change.new.sha:
            branch_changes[path] = change.new.sha
        else:  # pragma: no cover - file deletion edge case
            branch_changes[path] = None

    # Check if trunk has the same changes
    for change in tree_changes(repo.object_store, merge_base_tree, trunk_tree):
        path = change.new.path if change.new.path else change.old.path
        if path in branch_changes:
            trunk_sha = change.new.sha if change.new.sha else None
            if trunk_sha == branch_changes[path]:
                del branch_changes[path]

    # If all branch changes are accounted for in trunk, it's squash-merged
    return len(branch_changes) == 0


def get_merged_branches(
    repo: Repo, tracked_branches: list[str], trunk: str
) -> list[str]:
    """Get tracked branches that are merged into trunk.

    Detects both regular merges (branch is ancestor of trunk) and
    squash merges (branch changes are in trunk but commits aren't).
    """
    merged = []
    for branch in tracked_branches:
        if is_merged(repo, branch, trunk) or is_squash_merged(repo, branch, trunk):
            merged.append(branch)
    return merged

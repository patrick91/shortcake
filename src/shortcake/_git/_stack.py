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
    result = get_branch_parent_info(repo, branch, all_branches, branch_heads)
    return result[0] if result else None


def get_branch_parent_info(
    repo: Repo,
    branch: str,
    all_branches: set[str],
    branch_heads: dict[str, bytes] | None = None,
) -> tuple[str, bytes | None] | None:
    """
    Get parent branch and the merge base commit for rebasing.

    Walks commits from branch head to find the first commit that has the
    Shortcake-Parent trailer. Returns both the parent branch name and the
    parent of that commit (which is the correct merge base for rebasing).

    This is important because when a parent branch is modified (e.g., via
    `sc modify`), the git merge-base may return an ancestor that's too old,
    causing the rebase to include commits from the old parent branch.

    Args:
        repo: The git repository
        branch: The branch name to check
        all_branches: Set of all branch names for determining boundaries
        branch_heads: Optional precomputed dict of branch name -> head SHA.
                      If provided, avoids redundant get_branch_head() calls.

    Returns:
        Tuple of (parent_branch_name, merge_base_sha) if found, None otherwise.
        The merge_base_sha is the parent commit of the first commit with the trailer,
        or None if the commit has no parents (orphan commit).
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
    # Limit depth to avoid walking entire history for untracked branches.
    # Tracked branches should have trailer in first commit, but we allow
    # some depth for edge cases (rebased commits, etc).
    max_depth = 100
    seen: set[bytes] = set()
    to_visit = [branch_head]

    while to_visit and len(seen) < max_depth:
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
            # Found the first commit with trailer - return its parent as merge base
            commit = repo[commit_sha]
            if commit.parents:
                return (trailers.parent_branch, commit.parents[0])
            # Orphan commit (no parents) - return None for merge_base
            return (trailers.parent_branch, None)

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

    Walks trunk commits from HEAD back to merge-base and checks if any commit
    has ALL files changed by the branch with matching blob SHAs. This avoids
    false positives from independent changes to the same files, while still
    detecting squash merges even when trunk has additional modifications after
    the merge.
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

    from dulwich.diff_tree import tree_changes
    from dulwich.object_store import tree_lookup_path

    # Build dict of {path: blob_sha} from branch changes relative to merge base.
    # blob_sha is None for deletions (file removed by branch).
    branch_changes: dict[bytes, bytes | None] = {}
    for change in tree_changes(repo.object_store, merge_base_tree, branch_tree):
        if change.new is None or change.new.sha is None:
            # Deletion: file was removed by branch
            branch_changes[change.old.path] = None
        else:
            # Addition or modification
            branch_changes[change.new.path] = change.new.sha

    if not branch_changes:  # pragma: no cover
        return True

    # Walk trunk commits from trunk_head back to merge_base.
    # At each commit, check if ALL branch-changed files match the branch's blobs.
    for entry in Walker(repo.object_store, [trunk_head], exclude=[merge_base]):
        commit_tree = entry.commit.tree
        all_match = True

        for path, expected_blob in branch_changes.items():
            if expected_blob is None:
                # Branch deleted this file — check it's absent in this commit
                try:
                    tree_lookup_path(repo.__getitem__, commit_tree, path)
                    all_match = False
                    break
                except KeyError:
                    continue
            else:
                # Branch added/modified this file — check blob SHA matches
                try:
                    _, blob_sha = tree_lookup_path(repo.__getitem__, commit_tree, path)
                except KeyError:
                    all_match = False
                    break
                if blob_sha != expected_blob:
                    all_match = False
                    break

        if all_match:
            return True

    return False


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

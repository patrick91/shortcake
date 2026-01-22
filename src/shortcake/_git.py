import subprocess
import time
from pathlib import Path

from dulwich import porcelain
from dulwich.objects import Commit
from dulwich.repo import Repo


def open_repo(path: Path | None = None) -> Repo:
    """Open git repository at path or current directory."""
    return Repo(str(path) if path else ".")


def get_current_branch(repo: Repo) -> str | None:
    """Get name of current branch, or None if in detached HEAD state."""
    head_ref = repo.refs.read_ref(b"HEAD")
    if head_ref and head_ref.startswith(b"ref: refs/heads/"):
        return head_ref[16:].decode()
    return None


def get_branch_head(repo: Repo, branch: str) -> bytes:
    """Get SHA of branch head."""
    return repo.refs[f"refs/heads/{branch}".encode()]


def branch_exists(repo: Repo, branch: str) -> bool:
    """Check if branch exists."""
    return f"refs/heads/{branch}".encode() in repo.refs


def get_default_branch(repo: Repo) -> str | None:
    """Get the default branch name from origin/HEAD or fallback to main/master."""
    # Try origin/HEAD first (set by git clone)
    origin_head = repo.refs.read_ref(b"refs/remotes/origin/HEAD")
    if origin_head and origin_head.startswith(b"ref: refs/remotes/origin/"):
        return origin_head[25:].decode()

    # Fallback to checking for main/master
    for branch in ("main", "master"):
        if branch_exists(repo, branch):
            return branch

    return None


def get_commits_between(repo: Repo, head: bytes, base: bytes) -> list[bytes]:
    """Get commits reachable from head but not from base."""
    walker = repo.get_walker(include=[head], exclude=[base])
    return [entry.commit.id for entry in walker]


def get_commit_message(repo: Repo, sha: bytes) -> str:
    """Get commit message."""
    return repo[sha].message.decode()


def amend_commit_message(repo: Repo, sha: bytes, new_message: str) -> bytes:
    """Create new commit with different message, return new SHA."""
    old_commit = repo[sha]

    new_commit = Commit()
    new_commit.tree = old_commit.tree
    new_commit.parents = old_commit.parents
    new_commit.author = old_commit.author
    new_commit.committer = old_commit.committer
    new_commit.author_time = old_commit.author_time
    new_commit.author_timezone = old_commit.author_timezone
    new_commit.commit_time = int(time.time())
    new_commit.commit_timezone = old_commit.commit_timezone
    new_commit.encoding = old_commit.encoding
    new_commit.message = new_message.encode()

    repo.object_store.add_object(new_commit)
    return new_commit.id


def update_branch(repo: Repo, branch: str, sha: bytes) -> None:
    """Update branch to point to commit."""
    repo.refs[f"refs/heads/{branch}".encode()] = sha


def get_all_local_branches(repo: Repo) -> list[str]:
    """Get all local branch names."""
    prefix = b"refs/heads/"
    return [ref[len(prefix) :].decode() for ref in repo.refs if ref.startswith(prefix)]


def create_branch(repo: Repo, name: str, sha: bytes) -> None:
    """Create a new branch pointing at sha."""
    repo.refs[f"refs/heads/{name}".encode()] = sha


def set_head_to_branch(repo: Repo, branch: str) -> None:
    """Set HEAD to branch without updating working directory.

    Only updates the HEAD symbolic ref. Does not modify the working
    directory or index. Use this when you want to preserve staged
    changes (e.g., for creating a new branch with pending changes).

    For full branch switching with working directory update, use switch_branch().
    """
    repo.refs.set_symbolic_ref(b"HEAD", f"refs/heads/{branch}".encode())


def switch_branch(repo: Repo, branch: str) -> None:
    """Switch to branch, updating working directory and index."""
    porcelain.switch(repo, branch)


def has_staged_changes(repo: Repo) -> bool:
    """Check if there are staged changes."""
    status = porcelain.status(repo)
    return bool(
        status.staged["add"] or status.staged["modify"] or status.staged["delete"]
    )


def get_staged_files(repo: Repo) -> list[str]:
    """Get list of staged file paths."""
    status = porcelain.status(repo)
    files = []
    files.extend(p.decode() for p in status.staged["add"])
    files.extend(p.decode() for p in status.staged["modify"])
    return files


def has_precommit_hook(repo: Repo) -> bool:
    """Check if pre-commit hook exists and is executable."""
    hook_path = Path(repo.controldir()) / "hooks" / "pre-commit"
    return hook_path.exists() and hook_path.is_file()


def run_precommit_hook(repo: Repo) -> tuple[bool, str | None]:
    """Run pre-commit hook. Returns (success, error_message)."""
    hook_path = Path(repo.controldir()) / "hooks" / "pre-commit"
    if not hook_path.exists():
        return True, None

    staged_files = get_staged_files(repo)

    try:
        result = subprocess.run(
            [str(hook_path)],
            capture_output=True,
            text=True,
            cwd=repo.path,  # Working directory
        )
        # Re-stage files modified by hooks (e.g., formatters)
        if staged_files:
            porcelain.add(repo, paths=staged_files)

        if result.returncode != 0:
            return False, result.stdout or result.stderr
        return True, None
    except Exception as e:
        return False, str(e)


def create_commit(repo: Repo, message: str, no_verify: bool = False) -> bytes:
    """Create commit with staged changes. Returns SHA."""
    return porcelain.commit(repo, message=message.encode(), no_verify=no_verify)


def amend_commit(repo: Repo, message: str, no_verify: bool = False) -> bytes:
    """Amend HEAD commit with new message and staged changes. Returns SHA."""
    return porcelain.commit(
        repo, message=message.encode(), amend=True, no_verify=no_verify
    )


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
    from shortcake._trailers import Trailers

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
        if trailers.parent_branch is not None:
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


def get_merge_base(repo: Repo, commit1: bytes, commit2: bytes) -> bytes | None:
    """Get merge base of two commits using dulwich.

    Returns the common ancestor of two commits, or None if no common ancestor.
    """
    from dulwich.graph import find_merge_base

    bases = find_merge_base(repo, [commit1, commit2])
    return bases[0] if bases else None


def is_rebase_in_progress(repo: Repo) -> bool:
    """Check if git rebase is in progress."""
    git_dir = Path(repo.controldir())
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def has_uncommitted_changes(repo: Repo) -> bool:
    """Check for uncommitted changes (staged or unstaged)."""
    status = porcelain.status(repo)
    return bool(
        status.staged["add"]
        or status.staged["modify"]
        or status.staged["delete"]
        or status.unstaged
    )


def is_ancestor(repo: Repo, maybe_ancestor: bytes, descendant: bytes) -> bool:
    """Check if commit is ancestor of another.

    Returns True if maybe_ancestor is reachable from descendant.
    """
    if maybe_ancestor == descendant:
        return True

    merge_base = get_merge_base(repo, maybe_ancestor, descendant)
    return merge_base == maybe_ancestor


def get_remote_ref(repo: Repo, remote_branch: str) -> bytes | None:
    """Get SHA of remote ref like origin/branch_a."""
    full_ref = f"refs/remotes/{remote_branch}".encode()
    try:
        return repo.refs[full_ref]
    except KeyError:
        return None

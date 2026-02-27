"""Core git operations: repo, branches, commits, staging."""

import subprocess
import time
from pathlib import Path

from dulwich import porcelain
from dulwich.errors import (
    ApplyDeltaError,
    CommitError,
    FileFormatException,
    GitProtocolError,
    HangupException,
    HookError,
    MissingCommitError,
    NotBlobError,
    NotCommitError,
    NotGitRepository,
    NotTagError,
    NotTreeError,
    ObjectFormatException,
    PackedRefsException,
    RefFormatError,
    SendPackError,
    UnexpectedCommandError,
    WorkingTreeModifiedError,
    WrongObjectException,
)
from dulwich.index import ConflictedIndexEntry
from dulwich.objects import Commit
from dulwich.repo import Repo

DULWICH_ERRORS = (
    ApplyDeltaError,
    CommitError,
    FileFormatException,
    GitProtocolError,
    HangupException,
    HookError,
    MissingCommitError,
    NotBlobError,
    NotCommitError,
    NotGitRepository,
    NotTagError,
    NotTreeError,
    ObjectFormatException,
    PackedRefsException,
    RefFormatError,
    SendPackError,
    UnexpectedCommandError,
    WorkingTreeModifiedError,
    WrongObjectException,
    porcelain.Error,  # Cherry-pick conflict errors
)

DULWICH_HOOK_ERRORS = (*DULWICH_ERRORS, OSError, subprocess.SubprocessError)
DULWICH_IO_ERRORS = (*DULWICH_ERRORS, OSError)


def open_repo(path: Path | None = None) -> Repo:
    """Open git repository at path or current directory.

    If path is not provided, discovers the repository by walking up
    from the current directory.
    """
    if path:
        return Repo(str(path))
    return Repo.discover()


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


def update_branch(repo: Repo, branch: str, sha_hex: str) -> None:
    """Update branch to point to commit (sha_hex is 40-char hex string)."""
    repo.refs[f"refs/heads/{branch}".encode()] = sha_hex.encode()


def get_all_local_branches(repo: Repo) -> list[str]:
    """Get all local branch names."""
    prefix = b"refs/heads/"
    return [ref[len(prefix) :].decode() for ref in repo.refs if ref.startswith(prefix)]


def create_branch(repo: Repo, name: str, sha: bytes) -> None:
    """Create a new branch pointing at sha."""
    repo.refs[f"refs/heads/{name}".encode()] = sha


def delete_branch(repo: Repo, branch: str) -> None:
    """Delete a local branch."""
    ref = f"refs/heads/{branch}".encode()
    if ref in repo.refs:
        del repo.refs[ref]


def set_head_to_branch(repo: Repo, branch: str) -> None:
    """Set HEAD to branch without updating working directory.

    Only updates the HEAD symbolic ref. Does not modify the working
    directory or index. Use this when you want to preserve staged
    changes (e.g., for creating a new branch with pending changes).

    For full branch switching with working directory update, use switch_branch().
    """
    repo.refs.set_symbolic_ref(b"HEAD", f"refs/heads/{branch}".encode())


def switch_branch(repo: Repo, branch: str, force: bool = False) -> None:
    """Switch to branch, updating working directory and index.

    Uses native git instead of dulwich because dulwich's porcelain.switch()
    has a bug where it incorrectly stages files when switching between branches
    that have different file sets.
    """
    cmd = ["git", "switch", branch]
    if force:
        cmd.append("--force")
    result = subprocess.run(cmd, cwd=repo.path, capture_output=True, text=True)
    if result.returncode != 0:  # pragma: no cover
        raise ValueError(f"Failed to switch branch: {result.stderr.strip()}")


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
    """Run pre-commit hook with real-time output. Returns (success, error_message)."""
    hook_path = Path(repo.controldir()) / "hooks" / "pre-commit"
    if not hook_path.exists():
        return True, None

    staged_files = get_staged_files(repo)

    try:
        # Don't capture stdout - let it flow directly to terminal
        # This preserves ANSI escape sequences and carriage returns
        process = subprocess.Popen(
            [str(hook_path)],
            cwd=repo.path,
        )
        process.wait()

        # Re-stage files modified by hooks (e.g., formatters)
        if staged_files:
            porcelain.add(repo, paths=staged_files)

        if process.returncode != 0:
            return False, "Pre-commit hook failed"
        return True, None
    except DULWICH_HOOK_ERRORS as e:
        return False, str(e)


def create_commit(repo: Repo, message: str, no_verify: bool = False) -> bytes:
    """Create commit with staged changes. Returns SHA."""
    return porcelain.commit(repo, message=message.encode(), no_verify=no_verify)


def amend_commit(repo: Repo, message: str, no_verify: bool = False) -> bytes:
    """Amend HEAD commit with new message and staged changes. Returns SHA."""
    return porcelain.commit(
        repo, message=message.encode(), amend=True, no_verify=no_verify
    )


def has_uncommitted_changes(repo: Repo) -> bool:
    """Check for uncommitted changes (staged or unstaged)."""
    status = porcelain.status(repo)
    return bool(
        status.staged["add"]
        or status.staged["modify"]
        or status.staged["delete"]
        or status.unstaged
    )


def get_conflict_files(repo: Repo) -> list[str]:
    """Get list of files with conflicts from the index.

    Returns empty list if no conflicts found or on any error.
    """
    try:
        index = repo.open_index()
    except DULWICH_IO_ERRORS:
        return []

    paths = []
    for path, entry in index.items():
        if isinstance(entry, ConflictedIndexEntry):
            paths.append(path.decode())

    return sorted(paths)

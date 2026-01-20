import time
from pathlib import Path

from dulwich.objects import Commit
from dulwich.repo import Repo


def open_repo(path: Path | None = None) -> Repo:
    """Open git repository at path or current directory."""
    return Repo(str(path) if path else ".")


def get_current_branch(repo: Repo) -> str:
    """Get name of current branch."""
    head_ref = repo.refs.read_ref(b"HEAD")
    if head_ref and head_ref.startswith(b"ref: refs/heads/"):
        return head_ref[16:].decode()
    raise ValueError("Not on a branch (detached HEAD)")


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

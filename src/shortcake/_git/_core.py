"""Core git operations: repo, branches, commits, staging."""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygit2

type Repo = Any


@dataclass(frozen=True)
class CommitSummary:
    """Small display-oriented summary of a commit."""

    sha: str
    short_sha: str
    subject: str


# Error tuples — kept for backward compatibility with command-level handlers.
# These previously listed ~20 dulwich exception types; now they cover the
# pygit2 + stdlib equivalents that callers catch.
DULWICH_ERRORS = (pygit2.GitError, KeyError, ValueError, OSError)
DULWICH_HOOK_ERRORS = (*DULWICH_ERRORS, subprocess.SubprocessError)
DULWICH_IO_ERRORS = (*DULWICH_ERRORS,)


def _oid(sha: bytes | str) -> str:
    """Convert a SHA (bytes or str) to hex string for pygit2."""
    return sha.decode() if isinstance(sha, bytes) else sha


def _repo_workdir(repo: Repo) -> str:
    """Get working directory path for subprocess cwd."""
    return repo.workdir


def _git_dir(repo: Repo) -> Path:
    """Get .git directory path."""
    return Path(repo.path)


def open_repo(path: Path | None = None) -> Repo:
    """Open git repository at path or current directory.

    If path is not provided, discovers the repository by walking up
    from the current directory.
    """
    search = str(path) if path else "."
    git_dir = pygit2.discover_repository(search)
    if git_dir is None:
        raise ValueError(f"Not a git repository: {search}")
    return pygit2.Repository(git_dir)


def get_current_branch(repo: Repo) -> str | None:
    """Get name of current branch, or None if in detached HEAD state."""
    if repo.head_is_unborn or repo.head_is_detached:
        return None
    return repo.head.shorthand


def get_branch_head(repo: Repo, branch: str) -> bytes:
    """Get SHA of branch head."""
    ref = repo.references.get(f"refs/heads/{branch}")
    if ref is None:
        raise KeyError(f"refs/heads/{branch}")
    return str(ref.target).encode()


def branch_exists(repo: Repo, branch: str) -> bool:
    """Check if branch exists."""
    return f"refs/heads/{branch}" in repo.references


def get_default_branch(repo: Repo) -> str | None:
    """Get the default branch name from origin/HEAD or fallback to main/master."""
    # Try origin/HEAD first (set by git clone)
    try:
        origin_head_ref = repo.references.get("refs/remotes/origin/HEAD")
        if origin_head_ref is not None:
            target = origin_head_ref.target
            # target is a string like "refs/remotes/origin/main"
            prefix = "refs/remotes/origin/"
            if isinstance(target, str) and target.startswith(prefix):
                return target[len(prefix) :]
    except (pygit2.GitError, KeyError):
        pass

    # Fallback to checking for main/master
    for branch in ("main", "master"):
        if branch_exists(repo, branch):
            return branch

    return None


def get_commits_between(repo: Repo, head: bytes, base: bytes) -> list[bytes]:
    """Get commits reachable from head but not from base."""
    head_oid = pygit2.Oid(hex=_oid(head))
    base_oid = pygit2.Oid(hex=_oid(base))

    commits = []
    for commit in repo.walk(head_oid, pygit2.GIT_SORT_TOPOLOGICAL):
        if commit.id == base_oid:
            break
        commits.append(str(commit.id).encode())
    return commits


def get_commit_message(repo: Repo, sha: bytes) -> str:
    """Get commit message."""
    return repo.get(_oid(sha)).message


def get_branch_latest_commit(repo: Repo, branch: str) -> CommitSummary:
    """Get the HEAD commit summary for a branch."""
    sha = _oid(get_branch_head(repo, branch))
    commit = repo.get(sha)
    subject = (commit.message or "").splitlines()[0].strip()
    return CommitSummary(
        sha=sha,
        short_sha=sha[:7],
        subject=subject or "(no subject)",
    )


def amend_commit_message(repo: Repo, sha: bytes, new_message: str) -> bytes:
    """Create new commit with different message, return new SHA."""
    old = repo.get(_oid(sha))

    new_oid = repo.create_commit(
        None,  # don't update any ref
        old.author,
        pygit2.Signature(
            old.committer.name,
            old.committer.email,
            int(time.time()),
            old.committer.offset,
        ),
        new_message,
        old.tree_id,
        list(old.parent_ids),
    )
    return str(new_oid).encode()


def update_branch(repo: Repo, branch: str, sha_hex: str) -> None:
    """Update branch to point to commit (sha_hex is 40-char hex string)."""
    ref_name = f"refs/heads/{branch}"
    oid = pygit2.Oid(hex=sha_hex)
    if ref_name in repo.references:
        repo.references[ref_name].set_target(oid)
    else:
        repo.references.create(ref_name, oid)


def get_all_local_branches(repo: Repo) -> list[str]:
    """Get all local branch names."""
    prefix = "refs/heads/"
    return [ref[len(prefix) :] for ref in repo.references if ref.startswith(prefix)]


def create_branch(repo: Repo, name: str, sha: bytes) -> None:
    """Create a new branch pointing at sha."""
    oid = pygit2.Oid(hex=_oid(sha))
    repo.references.create(f"refs/heads/{name}", oid)


def delete_branch(repo: Repo, branch: str) -> None:
    """Delete a local branch."""
    ref_name = f"refs/heads/{branch}"
    if ref_name in repo.references:
        repo.references.delete(ref_name)


def set_head_to_branch(repo: Repo, branch: str) -> None:
    """Set HEAD to branch without updating working directory.

    Only updates the HEAD symbolic ref. Does not modify the working
    directory or index. Use this when you want to preserve staged
    changes (e.g., for creating a new branch with pending changes).

    For full branch switching with working directory update, use switch_branch().
    """
    repo.set_head(f"refs/heads/{branch}")


def switch_branch(repo: Repo, branch: str, force: bool = False) -> None:
    """Switch to branch, updating working directory and index."""
    cmd = ["git", "switch", branch]
    if force:
        cmd.append("--force")
    result = subprocess.run(
        cmd, cwd=_repo_workdir(repo), capture_output=True, text=True
    )
    if result.returncode != 0:  # pragma: no cover
        raise ValueError(f"Failed to switch branch: {result.stderr.strip()}")


def has_staged_changes(repo: Repo) -> bool:
    """Check if there are staged changes."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=_repo_workdir(repo),
        capture_output=True,
    )
    return result.returncode != 0


def get_staged_files(repo: Repo) -> list[str]:
    """Get list of staged file paths."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


def has_precommit_hook(repo: Repo) -> bool:
    """Check if pre-commit hook exists and is executable."""
    hook_path = _git_dir(repo) / "hooks" / "pre-commit"
    return hook_path.exists() and hook_path.is_file()


def run_precommit_hook(repo: Repo) -> tuple[bool, str | None]:
    """Run pre-commit hook with real-time output. Returns (success, error_message)."""
    hook_path = _git_dir(repo) / "hooks" / "pre-commit"
    if not hook_path.exists():
        return True, None

    staged_files = get_staged_files(repo)

    try:
        # Don't capture stdout - let it flow directly to terminal
        # This preserves ANSI escape sequences and carriage returns
        process = subprocess.Popen(
            [str(hook_path)],
            cwd=_repo_workdir(repo),
        )
        process.wait()

        # Re-stage files modified by hooks (e.g., formatters)
        if staged_files:
            subprocess.run(
                ["git", "add", *staged_files],
                cwd=_repo_workdir(repo),
                capture_output=True,
            )

        if process.returncode != 0:
            return False, "Pre-commit hook failed"
        return True, None
    except DULWICH_HOOK_ERRORS as e:
        return False, str(e)


def create_commit(
    repo: Repo,
    message: str,
    no_verify: bool = False,
    allow_empty: bool = True,
) -> bytes:
    """Create commit with staged changes. Returns SHA."""
    cmd = ["git", "commit", "-m", message]
    if no_verify:
        cmd.append("--no-verify")
    if allow_empty:
        cmd.append("--allow-empty")
    result = subprocess.run(
        cmd,
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"Commit failed: {result.stderr.strip()}")
    # Get the new HEAD SHA
    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    return head_result.stdout.strip().encode()


def amend_commit(
    repo: Repo,
    message: str,
    no_verify: bool = False,
    allow_empty: bool = True,
) -> bytes:
    """Amend HEAD commit with new message and staged changes. Returns SHA."""
    cmd = ["git", "commit", "--amend", "-m", message]
    if no_verify:
        cmd.append("--no-verify")
    if allow_empty:
        cmd.append("--allow-empty")
    result = subprocess.run(
        cmd,
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"Amend failed: {result.stderr.strip()}")
    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    return head_result.stdout.strip().encode()


def has_uncommitted_changes(repo: Repo) -> bool:
    """Check for uncommitted changes (staged or unstaged, excluding untracked)."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "-uno"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def get_staged_diff(repo: Repo) -> str:
    """Get the diff of staged changes as a patch string."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--no-color", "--full-index"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def unstage_all(repo: Repo) -> None:
    """Unstage all staged changes (move back to working tree)."""
    subprocess.run(
        ["git", "reset", "HEAD"],
        cwd=_repo_workdir(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def get_conflict_files(repo: Repo) -> list[str]:
    """Get list of files with conflicts from the index.

    Returns empty list if no conflicts found or on any error.
    """
    try:
        index = repo.index
        index.read()
        if not index.conflicts:
            return []
        # pygit2 index.conflicts yields (ancestor, ours, theirs) tuples
        # of IndexEntry objects. Extract the path from whichever entry exists.
        paths = []
        for ancestor, ours, theirs in index.conflicts:
            entry = ancestor or ours or theirs
            if entry:
                paths.append(entry.path)
        return sorted(paths)
    except DULWICH_IO_ERRORS:
        return []

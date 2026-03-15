import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pygit2

from shortcake import _git as git

type Repo = Any


def _repo_path(repo: Repo | Path) -> Path:
    """Get working directory path (resolved to handle macOS /var → /private/var)."""
    if isinstance(repo, Path):
        return repo.resolve()
    # pygit2.Repository: .workdir is the working directory
    if hasattr(repo, "workdir") and repo.workdir:
        return Path(repo.workdir).resolve()
    return Path(repo.path).resolve()


def _libgit_repo(repo: Repo | Path) -> pygit2.Repository:
    if isinstance(repo, pygit2.Repository):
        return repo
    repo_path = _repo_path(repo)
    git_dir = repo_path / ".git"
    return pygit2.Repository(git_dir if git_dir.exists() else repo_path)


def _git_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return value


def _repo_relative_path(repo_path: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_path))
    except ValueError:
        return str(path)


def _run_git(
    repo: Repo | Path,
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_path = _repo_path(repo)
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    )


def run_git(
    repo: Repo | Path,
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command in the repo and return the completed process."""
    return _run_git(repo, *args, input_text=input_text)


def configure_git_identity(
    repo_path: Path,
    *,
    email: str = "test@test.com",
    name: str = "Test User",
) -> None:
    """Configure test commit identity in repository config."""
    repo = _libgit_repo(repo_path)
    repo.config["user.email"] = email
    repo.config["user.name"] = name


def init_repo(path: Path, *, default_branch: str = "main") -> Repo:
    """Create a repo and configure a default test identity."""
    path.mkdir(parents=True, exist_ok=True)
    pygit2.init_repository(path, initial_head=default_branch)
    configure_git_identity(path)
    return git.open_repo(path)


def get_branch_head(repo: Repo, branch: str) -> bytes:
    """Return the SHA of a local branch head."""
    return git.get_branch_head(repo, branch)


def get_ref(repo: Repo, ref_name: str | bytes) -> bytes:
    """Get ref SHA as bytes. Works with both dulwich and pygit2 repos."""
    name = ref_name.decode() if isinstance(ref_name, bytes) else ref_name
    ref = repo.references.get(name)
    if ref is None:
        raise KeyError(ref_name)
    return str(ref.target).encode()


def set_ref(repo: Repo, ref_name: str | bytes, sha: bytes | str) -> None:
    """Set ref to SHA. Works with both dulwich and pygit2 repos."""
    name = ref_name.decode() if isinstance(ref_name, bytes) else ref_name
    sha_hex = sha.decode() if isinstance(sha, bytes) else sha
    if name == "HEAD":
        # To detach HEAD, write the SHA directly to the HEAD file
        head_path = Path(repo.path.rstrip("/")) / "HEAD"
        head_path.write_text(sha_hex + "\n")
        return
    oid = pygit2.Oid(hex=sha_hex)
    if name in repo.references:
        repo.references[name].set_target(oid)
    else:
        repo.references.create(name, oid)


def set_remote(repo: Repo, remote_name: str, url: str) -> None:
    """Configure a remote in the repo config."""
    repo.remotes.create(remote_name, url)


def update_branch(repo: Repo, branch: str, sha: bytes) -> None:
    """Move a local branch ref to a specific commit."""
    _libgit_repo(repo).create_reference(
        f"refs/heads/{branch}",
        pygit2.Oid(hex=_git_value(sha)),
        force=True,
    )


def switch_branch(repo: Repo, branch: str) -> None:
    """Switch branches and refresh the working tree if already on the target.

    Some tests move refs directly and then "switch" back to the same branch name
    to force the index and working tree to match the new branch tip.
    """
    if git.get_current_branch(repo) == branch:
        reset_hard(repo)
        return
    _libgit_repo(repo).checkout(f"refs/heads/{branch}")
    reset_hard(repo)


def reset_hard(repo: Repo, treeish: bytes | str | None = None) -> None:
    """Reset the index and working tree to the current HEAD."""
    libgit_repo = _libgit_repo(repo)
    if treeish is None:
        target = libgit_repo.head.target
    else:
        target = libgit_repo.revparse_single(_git_value(treeish)).id
    libgit_repo.reset(target, pygit2.GIT_RESET_HARD)


def create_branch(
    repo: Repo,
    branch: str,
    start_point: bytes,
    *,
    checkout: bool = False,
) -> None:
    """Create a branch at a commit and optionally check it out."""
    _libgit_repo(repo).create_reference(
        f"refs/heads/{branch}",
        pygit2.Oid(hex=_git_value(start_point)),
    )
    if checkout:
        switch_branch(repo, branch)


def add_paths(repo: Repo, *paths: Path) -> None:
    """Stage one or more paths."""
    libgit_repo = _libgit_repo(repo)
    repo_path = _repo_path(repo)
    for path in paths:
        libgit_repo.index.add(_repo_relative_path(repo_path, path))
    libgit_repo.index.write()


def remove_paths(repo: Repo, *paths: Path) -> None:
    """Remove one or more tracked paths."""
    libgit_repo = _libgit_repo(repo)
    repo_path = _repo_path(repo)
    for path in paths:
        if path.exists():
            path.unlink()
        libgit_repo.index.remove(_repo_relative_path(repo_path, path))
    libgit_repo.index.write()


def commit(repo: Repo, message: str | bytes) -> bytes:
    """Create a commit from the current index."""
    libgit_repo = _libgit_repo(repo)
    libgit_repo.index.write()
    tree = libgit_repo.index.write_tree()
    signature = libgit_repo.default_signature
    parents = [] if libgit_repo.head_is_unborn else [libgit_repo.head.target]
    commit_oid = libgit_repo.create_commit(
        "HEAD",
        signature,
        signature,
        _git_value(message),
        tree,
        parents,
    )
    return str(commit_oid).encode()


def commit_files(
    repo: Repo,
    files: Mapping[Path, str],
    message: str | bytes,
) -> bytes:
    """Write files, stage them, and create a commit."""
    paths: list[Path] = []
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        paths.append(path)

    add_paths(repo, *paths)
    return commit(repo, message)

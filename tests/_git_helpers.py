import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shortcake import _git as git

type Repo = Any


def _repo_path(repo: Repo | Path) -> Path:
    if isinstance(repo, Path):
        return repo
    return Path(repo.path)


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
    """Configure git identity for tests that rely on git CLI operations."""
    subprocess.run(
        ["git", "config", "user.email", email],
        cwd=repo_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", name],
        cwd=repo_path,
        check=True,
    )


def init_repo(path: Path, *, default_branch: str = "main") -> Repo:
    """Create a repo and configure a default test identity."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init", f"--initial-branch={default_branch}")
    configure_git_identity(path)
    return git.open_repo(path)


def get_branch_head(repo: Repo, branch: str) -> bytes:
    """Return the SHA of a local branch head."""
    return git.get_branch_head(repo, branch)


def update_branch(repo: Repo, branch: str, sha: bytes) -> None:
    """Move a local branch ref to a specific commit."""
    _run_git(repo, "update-ref", f"refs/heads/{branch}", _git_value(sha))


def switch_branch(repo: Repo, branch: str) -> None:
    """Switch branches and refresh the working tree if already on the target.

    Some tests move refs directly and then "switch" back to the same branch name
    to force the index and working tree to match the new branch tip.
    """
    if git.get_current_branch(repo) == branch:
        reset_hard(repo)
        return
    git.switch_branch(repo, branch)
    reset_hard(repo)


def reset_hard(repo: Repo, treeish: bytes | str | None = None) -> None:
    """Reset the index and working tree to the current HEAD."""
    args = ["reset", "--hard"]
    if treeish is not None:
        args.append(_git_value(treeish))
    _run_git(repo, *args)


def create_branch(
    repo: Repo,
    branch: str,
    start_point: bytes,
    *,
    checkout: bool = False,
) -> None:
    """Create a branch at a commit and optionally check it out."""
    git.create_branch(repo, branch, start_point)
    if checkout:
        switch_branch(repo, branch)


def add_paths(repo: Repo, *paths: Path) -> None:
    """Stage one or more paths."""
    repo_path = _repo_path(repo)
    _run_git(repo, "add", *[_repo_relative_path(repo_path, path) for path in paths])


def remove_paths(repo: Repo, *paths: Path) -> None:
    """Remove one or more tracked paths."""
    repo_path = _repo_path(repo)
    _run_git(repo, "rm", *[_repo_relative_path(repo_path, path) for path in paths])


def commit(repo: Repo, message: str | bytes) -> bytes:
    """Create a commit from the current index."""
    _run_git(repo, "commit", "--quiet", "-F", "-", input_text=_git_value(message))
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip().encode()


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

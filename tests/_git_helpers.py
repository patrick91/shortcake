import subprocess
from collections.abc import Mapping
from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo


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
    repo = Repo.init(path, default_branch=default_branch.encode())
    configure_git_identity(path)
    return repo


def get_branch_head(repo: Repo, branch: str) -> bytes:
    """Return the SHA of a local branch head."""
    return repo.refs[f"refs/heads/{branch}".encode()]


def update_branch(repo: Repo, branch: str, sha: bytes) -> None:
    """Move a local branch ref to a specific commit."""
    repo.refs[f"refs/heads/{branch}".encode()] = sha


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset.

    dulwich's porcelain.switch doesn't fully reset the index, which can
    cause files from the old branch to be included in new commits.
    This helper sets HEAD first, then uses reset --hard to update the
    index and working tree without moving any branch refs.
    """
    repo.refs.set_symbolic_ref(b"HEAD", f"refs/heads/{branch}".encode())
    porcelain.reset(repo, "hard")


def reset_hard(repo: Repo) -> None:
    """Reset the index and working tree to the current HEAD."""
    porcelain.reset(repo, "hard")


def create_branch(
    repo: Repo,
    branch: str,
    start_point: bytes,
    *,
    checkout: bool = False,
) -> None:
    """Create a branch at a commit and optionally check it out."""
    update_branch(repo, branch, start_point)
    if checkout:
        switch_branch(repo, branch)


def add_paths(repo: Repo, *paths: Path) -> None:
    """Stage one or more paths."""
    porcelain.add(repo, paths=[str(path) for path in paths])


def commit(repo: Repo, message: str | bytes) -> bytes:
    """Create a commit from the current index."""
    encoded_message = message.encode() if isinstance(message, str) else message
    return porcelain.commit(repo, message=encoded_message)


def commit_files(
    repo: Repo,
    files: Mapping[Path, str],
    message: str | bytes,
) -> bytes:
    """Write files, stage them, and create a commit."""
    paths: list[str] = []
    for path, content in files.items():
        path.write_text(content)
        paths.append(str(path))

    porcelain.add(repo, paths=paths)
    return commit(repo, message)

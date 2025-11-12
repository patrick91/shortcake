"""Git helper utilities for testing."""

import subprocess
from pathlib import Path


def git_run(
    command: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git"] + command,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def create_commit(repo_path: Path, message: str, file_changes: dict[str, str] | None = None) -> str:
    """Create a commit with optional file changes.

    Args:
        repo_path: Path to the repository
        message: Commit message
        file_changes: Dict of filename -> content to write before committing

    Returns:
        The commit SHA
    """
    if file_changes:
        for filename, content in file_changes.items():
            file_path = repo_path / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
            git_run(["add", filename], cwd=repo_path)

    git_run(["commit", "-m", message], cwd=repo_path)
    result = git_run(["rev-parse", "HEAD"], cwd=repo_path)
    return result.stdout.strip()


def create_branch(repo_path: Path, branch_name: str, checkout: bool = True) -> None:
    """Create a new branch.

    Args:
        repo_path: Path to the repository
        branch_name: Name of the branch to create
        checkout: Whether to checkout the new branch
    """
    if checkout:
        git_run(["checkout", "-b", branch_name], cwd=repo_path)
    else:
        git_run(["branch", branch_name], cwd=repo_path)


def checkout_branch(repo_path: Path, branch_name: str) -> None:
    """Checkout an existing branch."""
    git_run(["checkout", branch_name], cwd=repo_path)


def get_current_branch(repo_path: Path) -> str:
    """Get the name of the current branch."""
    result = git_run(["branch", "--show-current"], cwd=repo_path)
    return result.stdout.strip()


def get_current_commit(repo_path: Path) -> str:
    """Get the SHA of the current commit."""
    result = git_run(["rev-parse", "HEAD"], cwd=repo_path)
    return result.stdout.strip()


def get_commit_message(repo_path: Path, ref: str = "HEAD") -> str:
    """Get the commit message for a given ref."""
    result = git_run(["log", "-1", "--format=%B", ref], cwd=repo_path)
    return result.stdout.strip()


def get_branches(repo_path: Path) -> list[str]:
    """Get list of all branches."""
    result = git_run(["branch", "--format=%(refname:short)"], cwd=repo_path)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def branch_exists(repo_path: Path, branch_name: str) -> bool:
    """Check if a branch exists."""
    return branch_name in get_branches(repo_path)


def get_notes(repo_path: Path, ref: str = "HEAD", notes_ref: str = "shortcake") -> str | None:
    """Get git notes for a commit.

    Args:
        repo_path: Path to the repository
        ref: The commit ref to get notes for
        notes_ref: The notes ref to read from

    Returns:
        The notes content or None if no notes exist
    """
    result = git_run(
        ["notes", "--ref", notes_ref, "show", ref],
        cwd=repo_path,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def add_notes(
    repo_path: Path, content: str, ref: str = "HEAD", notes_ref: str = "shortcake"
) -> None:
    """Add git notes to a commit.

    Args:
        repo_path: Path to the repository
        content: The notes content to add
        ref: The commit ref to add notes to
        notes_ref: The notes ref to write to
    """
    git_run(["notes", "--ref", notes_ref, "add", "-m", content, ref], cwd=repo_path)


def setup_remote(local_repo: Path, remote_repo: Path, remote_name: str = "origin") -> None:
    """Set up a remote for a local repository.

    Args:
        local_repo: Path to the local repository
        remote_repo: Path to the remote repository
        remote_name: Name for the remote (default: "origin")
    """
    git_run(["remote", "add", remote_name, str(remote_repo)], cwd=local_repo)


def push_branch(
    repo_path: Path,
    branch_name: str,
    remote_name: str = "origin",
    force: bool = False,
) -> None:
    """Push a branch to a remote.

    Args:
        repo_path: Path to the repository
        branch_name: Name of the branch to push
        remote_name: Name of the remote to push to
        force: Whether to force push
    """
    cmd = ["push"]
    if force:
        cmd.append("--force")
    cmd.extend([remote_name, branch_name])
    git_run(cmd, cwd=repo_path)


def fetch(repo_path: Path, remote_name: str = "origin") -> None:
    """Fetch from a remote."""
    git_run(["fetch", remote_name], cwd=repo_path)


def create_bare_repo(repo_path: Path) -> None:
    """Create a bare git repository.

    Args:
        repo_path: Path where the bare repository should be created
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    git_run(["init", "--bare"], cwd=repo_path)

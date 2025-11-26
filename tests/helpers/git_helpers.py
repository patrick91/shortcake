"""Git helper utilities for testing."""

from pathlib import Path

from shortcake.git import GitRepo


def create_commit(repo_path: Path, message: str, file_changes: dict[str, str] | None = None) -> str:
    """Create a commit with optional file changes.

    Args:
        repo_path: Path to the repository
        message: Commit message
        file_changes: Dict of filename -> content to write before committing

    Returns:
        The commit SHA
    """
    git = GitRepo(repo_path)

    if file_changes:
        for filename, content in file_changes.items():
            file_path = repo_path / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
            git.add_files(filename)

    git.commit(message)
    return git.get_current_commit()


def create_branch(repo_path: Path, branch_name: str, checkout: bool = True) -> None:
    """Create a new branch.

    Args:
        repo_path: Path to the repository
        branch_name: Name of the branch to create
        checkout: Whether to checkout the new branch
    """
    git = GitRepo(repo_path)
    git.create_branch(branch_name, checkout=checkout)


def checkout_branch(repo_path: Path, branch_name: str) -> None:
    """Checkout an existing branch."""
    git = GitRepo(repo_path)
    git.checkout_branch(branch_name)


def get_current_branch(repo_path: Path) -> str:
    """Get the name of the current branch."""
    git = GitRepo(repo_path)
    return git.get_current_branch()


def get_current_commit(repo_path: Path) -> str:
    """Get the SHA of the current commit."""
    git = GitRepo(repo_path)
    return git.get_current_commit()


def get_commit_message(repo_path: Path, ref: str = "HEAD") -> str:
    """Get the commit message for a given ref."""
    git = GitRepo(repo_path)
    return git.get_commit_message(ref)


def get_branches(repo_path: Path) -> list[str]:
    """Get list of all branches."""
    git = GitRepo(repo_path)
    return git.get_branches()


def branch_exists(repo_path: Path, branch_name: str) -> bool:
    """Check if a branch exists."""
    git = GitRepo(repo_path)
    return git.branch_exists(branch_name)


def get_notes(repo_path: Path, ref: str = "HEAD", notes_ref: str = "shortcake") -> str | None:
    """Get git notes for a commit.

    Args:
        repo_path: Path to the repository
        ref: The commit ref to get notes for
        notes_ref: The notes ref to read from

    Returns:
        The notes content or None if no notes exist
    """
    git = GitRepo(repo_path)
    return git.get_notes(ref, notes_ref)


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
    git = GitRepo(repo_path)
    git.add_notes(content, ref, notes_ref)


def setup_remote(local_repo: Path, remote_repo: Path, remote_name: str = "origin") -> None:
    """Set up a remote for a local repository.

    Args:
        local_repo: Path to the local repository
        remote_repo: Path to the remote repository
        remote_name: Name for the remote (default: "origin")
    """
    git = GitRepo(local_repo)
    git.add_remote(remote_name, str(remote_repo))


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
    git = GitRepo(repo_path)
    git.push(remote_name, branch_name, force=force)


def fetch(repo_path: Path, remote_name: str = "origin") -> None:
    """Fetch from a remote."""
    git = GitRepo(repo_path)
    git.fetch(remote_name)


def create_bare_repo(repo_path: Path) -> None:
    """Create a bare git repository.

    Args:
        repo_path: Path where the bare repository should be created
    """
    GitRepo.create_bare_repo(repo_path)

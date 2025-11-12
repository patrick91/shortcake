"""Git operations using GitPython."""

import subprocess
from pathlib import Path

from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError


class GitError(Exception):
    """Raised when a git operation fails."""

    pass


class GitRepo:
    """Wrapper around GitPython Repo for shortcake operations."""

    def __init__(self, path: Path | None = None):
        """Initialize the git repository.

        Args:
            path: Path to the repository. If None, uses current directory.
        """
        try:
            self.repo = Repo(path or Path.cwd(), search_parent_directories=True)
            self.working_dir = Path(self.repo.working_dir)
        except (InvalidGitRepositoryError, NoSuchPathError):
            raise GitError(
                "fatal: not a git repository (or any of the parent directories): .git"
            ) from None
        except Exception as e:
            raise GitError(f"Failed to initialize git repository: {e}") from e

    def get_current_branch(self) -> str:
        """Get the name of the current branch.

        Returns:
            The current branch name.

        Raises:
            GitError: If unable to determine current branch.
        """
        try:
            return self.repo.active_branch.name
        except Exception as e:
            raise GitError(f"Failed to get current branch: {e}") from e

    def create_branch(self, name: str, checkout: bool = True) -> None:
        """Create a new branch and optionally switch to it.

        Args:
            name: The name of the branch to create.
            checkout: If True, switch to the new branch.

        Raises:
            GitError: If branch creation fails.
        """
        try:
            new_branch = self.repo.create_head(name)
            if checkout:
                new_branch.checkout()
        except Exception as e:
            raise GitError(f"Failed to create branch '{name}': {e}") from e

    def checkout_branch(self, name: str) -> None:
        """Switch to an existing branch.

        Args:
            name: The name of the branch to checkout.

        Raises:
            GitError: If checkout fails.
        """
        try:
            self.repo.heads[name].checkout()
        except Exception as e:
            raise GitError(f"Failed to checkout branch '{name}': {e}") from e

    def rename_branch(self, old_name: str, new_name: str) -> None:
        """Rename a branch.

        Args:
            old_name: The current name of the branch.
            new_name: The new name for the branch.

        Raises:
            GitError: If rename fails.
        """
        try:
            branch = self.repo.heads[old_name]
            branch.rename(new_name)
        except Exception as e:
            raise GitError(f"Failed to rename branch '{old_name}' to '{new_name}': {e}") from e

    def delete_branch(self, name: str, force: bool = True) -> None:
        """Delete a branch.

        Args:
            name: The name of the branch to delete.
            force: If True, force delete the branch.

        Raises:
            GitError: If deletion fails.
        """
        try:
            self.repo.delete_head(name, force=force)
        except Exception as e:
            raise GitError(f"Failed to delete branch '{name}': {e}") from e

    def commit(self, message: str | None = None, amend: bool = False) -> None:
        """Create a commit.

        Args:
            message: The commit message. If None, opens editor.
            amend: If True, amend the previous commit.

        Raises:
            GitError: If commit fails.
        """
        try:
            if amend:
                # GitPython's amend is a bit tricky, use git directly
                subprocess.run(
                    ["git", "commit", "--amend", "--no-edit"],
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=self.working_dir,
                )
            elif message is None:
                # Use git directly for interactive commit (opens editor)
                # GitPython doesn't handle interactive commits well
                subprocess.run(["git", "commit"], check=True, cwd=self.working_dir)
            else:
                self.repo.index.commit(message)
        except subprocess.CalledProcessError as e:
            raise GitError(f"Failed to commit: {e.stderr if e.stderr else str(e)}") from e
        except Exception as e:
            raise GitError(f"Failed to commit: {e}") from e

    def get_last_commit_message(self) -> str:
        """Get the message of the last commit (subject line only).

        Returns:
            The commit message subject line.

        Raises:
            GitError: If unable to get commit message.
        """
        try:
            return self.repo.head.commit.summary
        except Exception as e:
            raise GitError(f"Failed to get commit message: {e}") from e

    def has_staged_changes(self) -> bool:
        """Check if there are staged changes.

        Returns:
            True if there are staged changes, False otherwise.
        """
        # Use git directly for this check to ensure we get the current state
        # git diff --cached --quiet returns 0 if no changes, 1 if there are changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True,
            cwd=self.working_dir,
        )
        return result.returncode != 0

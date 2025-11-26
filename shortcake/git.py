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

    @staticmethod
    def create_bare_repo(path: Path) -> None:
        """Create a bare git repository.

        Args:
            path: Path where the bare repository should be created.

        Raises:
            GitError: If repository creation fails.
        """
        try:
            path.mkdir(parents=True, exist_ok=True)
            Repo.init(path, bare=True)
        except Exception as e:
            raise GitError(f"Failed to create bare repository at '{path}': {e}") from e

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

    def add_files(self, paths: list[str] | str) -> None:
        """Stage files to the index.

        Args:
            paths: File path(s) to add to the index. Can be a string or list of strings.

        Raises:
            GitError: If adding files fails.
        """
        try:
            if isinstance(paths, str):
                paths = [paths]
            self.repo.index.add(paths)
        except Exception as e:
            raise GitError(f"Failed to add files: {e}") from e

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

    def get_current_commit(self) -> str:
        """Get the SHA of the current commit (HEAD).

        Returns:
            The commit SHA as a hex string.

        Raises:
            GitError: If unable to get commit SHA.
        """
        try:
            return self.repo.head.commit.hexsha
        except Exception as e:
            raise GitError(f"Failed to get current commit: {e}") from e

    def get_commit_message(self, ref: str = "HEAD") -> str:
        """Get the full commit message for a given ref.

        Args:
            ref: The commit reference (branch name, tag, SHA, etc.). Defaults to HEAD.

        Returns:
            The full commit message (including body).

        Raises:
            GitError: If unable to get commit message.
        """
        try:
            commit = self.repo.commit(ref)
            return commit.message.strip()
        except Exception as e:
            raise GitError(f"Failed to get commit message for '{ref}': {e}") from e

    def get_branches(self) -> list[str]:
        """Get list of all branch names in the repository.

        Returns:
            List of branch names.

        Raises:
            GitError: If unable to get branches.
        """
        try:
            return [head.name for head in self.repo.heads]
        except Exception as e:
            raise GitError(f"Failed to get branches: {e}") from e

    def branch_exists(self, branch_name: str) -> bool:
        """Check if a branch exists.

        Args:
            branch_name: The name of the branch to check.

        Returns:
            True if the branch exists, False otherwise.
        """
        try:
            return branch_name in [head.name for head in self.repo.heads]
        except Exception:
            return False

    def get_notes(self, ref: str = "HEAD", notes_ref: str = "shortcake") -> str | None:
        """Get git notes for a commit.

        Args:
            ref: The commit ref to get notes for.
            notes_ref: The notes ref to read from.

        Returns:
            The notes content or None if no notes exist.
        """
        try:
            # Use GitPython's git command interface for notes operations
            note_content = self.repo.git.notes("--ref", notes_ref, "show", ref)
            return note_content.strip()
        except Exception:
            # If notes don't exist or other error, return None
            return None

    def add_notes(self, content: str, ref: str = "HEAD", notes_ref: str = "shortcake") -> None:
        """Add git notes to a commit.

        Args:
            content: The notes content to add.
            ref: The commit ref to add notes to.
            notes_ref: The notes ref to write to.

        Raises:
            GitError: If adding notes fails.
        """
        try:
            # Use GitPython's git command interface for notes operations
            self.repo.git.notes("--ref", notes_ref, "add", "-m", content, ref)
        except Exception as e:
            raise GitError(f"Failed to add notes: {e}") from e

    def add_remote(self, name: str, url: str) -> None:
        """Add a remote to the repository.

        Args:
            name: The name of the remote.
            url: The URL of the remote repository.

        Raises:
            GitError: If adding remote fails.
        """
        try:
            self.repo.create_remote(name, url)
        except Exception as e:
            raise GitError(f"Failed to add remote '{name}': {e}") from e

    def push(self, remote_name: str, branch_name: str, force: bool = False) -> None:
        """Push a branch to a remote.

        Args:
            remote_name: The name of the remote to push to.
            branch_name: The name of the branch to push.
            force: Whether to force push.

        Raises:
            GitError: If push fails.
        """
        try:
            remote = self.repo.remote(remote_name)
            push_info = remote.push(branch_name, force=force)
            # Check if push was successful
            if push_info and push_info[0].flags & push_info[0].ERROR:
                raise GitError(f"Push failed: {push_info[0].summary}")
        except Exception as e:
            if isinstance(e, GitError):
                raise
            raise GitError(f"Failed to push to '{remote_name}': {e}") from e

    def fetch(self, remote_name: str = "origin") -> None:
        """Fetch from a remote.

        Args:
            remote_name: The name of the remote to fetch from.

        Raises:
            GitError: If fetch fails.
        """
        try:
            remote = self.repo.remote(remote_name)
            remote.fetch()
        except Exception as e:
            raise GitError(f"Failed to fetch from '{remote_name}': {e}") from e

    def has_staged_changes(self) -> bool:
        """Check if there are staged changes.

        Returns:
            True if there are staged changes, False otherwise.
        """
        try:
            # Check if there are any staged changes by comparing index to HEAD
            return len(self.repo.index.diff("HEAD")) > 0
        except Exception:
            # If HEAD doesn't exist (no commits yet), check if index has entries
            return len(self.repo.index.entries) > 0

    def get_merge_base(self, branch1: str, branch2: str) -> str | None:
        """Get the merge-base (common ancestor) commit of two branches.

        Args:
            branch1: First branch name.
            branch2: Second branch name.

        Returns:
            The SHA of the merge-base commit, or None if no common ancestor.
        """
        try:
            result = self.repo.git.merge_base(branch1, branch2)
            return result.strip() if result else None
        except Exception:
            return None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Check if ancestor is in the history of descendant.

        Args:
            ancestor: The potential ancestor branch/commit.
            descendant: The descendant branch/commit.

        Returns:
            True if ancestor is an ancestor of descendant, False otherwise.
        """
        try:
            # Use git merge-base --is-ancestor
            self.repo.git.merge_base("--is-ancestor", ancestor, descendant)
            return True
        except Exception:
            return False

    def count_commits_between(self, base: str, head: str) -> int:
        """Count the number of commits between two refs.

        Args:
            base: The base ref (older commit).
            head: The head ref (newer commit).

        Returns:
            Number of commits between base and head.
        """
        try:
            # Use git rev-list to count commits
            result = self.repo.git.rev_list("--count", f"{base}..{head}")
            return int(result.strip())
        except Exception:
            return 0

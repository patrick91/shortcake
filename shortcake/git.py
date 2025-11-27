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

    def commit(
        self,
        message: str | None = None,
        amend: bool = False,
        no_verify: bool = False,
        message_prefix: str | None = None,
    ) -> None:
        """Create a commit.

        Args:
            message: The commit message. If None, opens editor.
            amend: If True, amend the previous commit.
            no_verify: If True, skip pre-commit and commit-msg hooks.
            message_prefix: If provided, pre-fill the editor with this prefix.

        Raises:
            GitError: If commit fails.
        """
        try:
            if amend:
                # GitPython's amend is a bit tricky, use git directly
                # Don't capture output so pre-commit hooks stream naturally to terminal
                cmd = ["git", "commit", "--amend", "--no-edit"]
                if no_verify:
                    cmd.append("--no-verify")
                subprocess.run(
                    cmd,
                    check=True,
                    cwd=self.working_dir,
                )
            elif message is None:
                # Use git directly for interactive commit (opens editor)
                # GitPython doesn't handle interactive commits well
                cmd = ["git", "commit"]
                if no_verify:
                    cmd.append("--no-verify")

                # If message_prefix is provided, use -m to pre-fill and -e to edit
                if message_prefix:
                    cmd.extend(["-m", f"{message_prefix} ", "-e"])

                subprocess.run(cmd, check=True, cwd=self.working_dir)
            else:
                # If message_prefix is provided, prepend it to the message
                full_message = f"{message_prefix} {message}" if message_prefix else message
                if no_verify:
                    # Use subprocess for --no-verify support
                    cmd = ["git", "commit", "-m", full_message, "--no-verify"]
                    subprocess.run(cmd, check=True, cwd=self.working_dir)
                else:
                    self.repo.index.commit(full_message)
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

    def push(
        self,
        remote_name: str,
        branch_name: str,
        force: bool = False,
        force_with_lease: bool = False,
    ) -> None:
        """Push a branch to a remote.

        Args:
            remote_name: The name of the remote to push to.
            branch_name: The name of the branch to push.
            force: Whether to force push (uses --force-with-lease for safety).
            force_with_lease: Explicitly use --force-with-lease.

        Raises:
            GitError: If push fails.
        """
        try:
            cmd = ["git", "push", remote_name, branch_name]
            if force:
                cmd.append("--force")
            elif force_with_lease:
                cmd.append("--force-with-lease")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.working_dir,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip()
                if "non-fast-forward" in error_msg or "[rejected]" in error_msg:
                    raise GitError("Push failed: [rejected] (non-fast-forward)")
                raise GitError(f"Push failed: {error_msg}")
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

    def remove_notes(self, ref: str = "HEAD", notes_ref: str = "shortcake") -> None:
        """Remove git notes from a commit.

        Args:
            ref: The commit ref to remove notes from.
            notes_ref: The notes ref to remove from.

        Raises:
            GitError: If removing notes fails.
        """
        try:
            self.repo.git.notes("--ref", notes_ref, "remove", ref)
        except Exception as e:
            raise GitError(f"Failed to remove notes: {e}") from e

    def update_notes(self, content: str, ref: str = "HEAD", notes_ref: str = "shortcake") -> None:
        """Update git notes for a commit (removes existing and adds new).

        Args:
            content: The new notes content.
            ref: The commit ref to update notes for.
            notes_ref: The notes ref to update.

        Raises:
            GitError: If updating notes fails.
        """
        try:
            # Use --force to overwrite existing notes
            self.repo.git.notes("--ref", notes_ref, "add", "-f", "-m", content, ref)
        except Exception as e:
            raise GitError(f"Failed to update notes: {e}") from e

    def rebase_onto(self, new_base: str, old_base: str, branch: str) -> None:
        """Rebase a branch onto a new base.

        Equivalent to: git rebase --onto <new_base> <old_base> <branch>

        Args:
            new_base: The new base commit/branch to rebase onto.
            old_base: The old base commit/branch (commits after this will be rebased).
            branch: The branch to rebase.

        Raises:
            GitError: If rebase fails (e.g., conflicts).
        """
        try:
            subprocess.run(
                ["git", "rebase", "--onto", new_base, old_base, branch],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.working_dir,
            )
        except subprocess.CalledProcessError as e:
            # Check if it's a conflict
            if "conflict" in e.stderr.lower() or "conflict" in e.stdout.lower():
                raise GitError(f"Rebase conflict while rebasing {branch}.") from e
            raise GitError(f"Failed to rebase {branch}: {e.stderr or e.stdout}") from e

    def rebase(self, onto: str) -> None:
        """Rebase current branch onto another branch.

        Args:
            onto: The branch/commit to rebase onto.

        Raises:
            GitError: If rebase fails.
        """
        try:
            subprocess.run(
                ["git", "rebase", onto],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.working_dir,
            )
        except subprocess.CalledProcessError as e:
            if "conflict" in e.stderr.lower() or "conflict" in e.stdout.lower():
                raise GitError("Rebase conflict.") from e
            raise GitError(f"Failed to rebase: {e.stderr or e.stdout}") from e

    def merge_ff_only(self, ref: str) -> None:
        """Fast-forward merge the current branch to a ref.

        Args:
            ref: The ref to fast-forward to.

        Raises:
            GitError: If merge fails (not fast-forwardable).
        """
        try:
            subprocess.run(
                ["git", "merge", "--ff-only", ref],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.working_dir,
            )
        except subprocess.CalledProcessError as e:
            raise GitError(f"Failed to fast-forward merge: {e.stderr or e.stdout}") from e

    def rebase_continue(self) -> None:
        """Continue a rebase after resolving conflicts.

        Raises:
            GitError: If continuing rebase fails.
        """
        import os

        try:
            # Set GIT_EDITOR to true to prevent editor from opening
            env = os.environ.copy()
            env["GIT_EDITOR"] = "true"
            subprocess.run(
                ["git", "rebase", "--continue"],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.working_dir,
                env=env,
            )
        except subprocess.CalledProcessError as e:
            raise GitError(f"Failed to continue rebase: {e.stderr or e.stdout}") from e

    def rebase_abort(self) -> None:
        """Abort a rebase in progress.

        Raises:
            GitError: If aborting rebase fails.
        """
        try:
            subprocess.run(
                ["git", "rebase", "--abort"],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.working_dir,
            )
        except subprocess.CalledProcessError as e:
            raise GitError(f"Failed to abort rebase: {e.stderr or e.stdout}") from e

    def is_rebase_in_progress(self) -> bool:
        """Check if a rebase is currently in progress.

        Returns:
            True if a rebase is in progress, False otherwise.
        """
        rebase_merge = self.working_dir / ".git" / "rebase-merge"
        rebase_apply = self.working_dir / ".git" / "rebase-apply"
        return rebase_merge.exists() or rebase_apply.exists()

    def get_commit_sha(self, ref: str) -> str:
        """Get the commit SHA for a ref.

        Args:
            ref: The ref to get the SHA for.

        Returns:
            The commit SHA.

        Raises:
            GitError: If unable to get SHA.
        """
        try:
            return self.repo.commit(ref).hexsha
        except Exception as e:
            raise GitError(f"Failed to get commit SHA for '{ref}': {e}") from e

    def update_ref(self, ref: str, sha: str) -> None:
        """Update a ref to point to a specific commit.

        Args:
            ref: The ref to update (e.g., 'refs/heads/my-branch').
            sha: The commit SHA to point to.

        Raises:
            GitError: If unable to update the ref.
        """
        try:
            result = subprocess.run(
                ["git", "update-ref", ref, sha],
                capture_output=True,
                text=True,
                cwd=self.working_dir,
            )
            if result.returncode != 0:
                raise GitError(f"Failed to update ref: {result.stderr.strip()}")
        except Exception as e:
            if isinstance(e, GitError):
                raise
            raise GitError(f"Failed to update ref '{ref}': {e}") from e

    def has_remote(self, remote_name: str = "origin") -> bool:
        """Check if a remote exists.

        Args:
            remote_name: The name of the remote to check.

        Returns:
            True if the remote exists, False otherwise.
        """
        try:
            return remote_name in [r.name for r in self.repo.remotes]
        except Exception:
            return False

    def get_remote_url(self, remote_name: str = "origin") -> str:
        """Get the URL of a remote.

        Args:
            remote_name: The name of the remote.

        Returns:
            The remote URL.

        Raises:
            GitError: If the remote doesn't exist.
        """
        try:
            for remote in self.repo.remotes:
                if remote.name == remote_name:
                    return remote.url
            raise GitError(f"Remote '{remote_name}' not found")
        except Exception as e:
            if isinstance(e, GitError):
                raise
            raise GitError(f"Failed to get remote URL: {e}") from e

    def is_tree_subset(self, branch: str, target: str) -> bool:
        """Check if branch's changes are contained in target (for squash merge detection).

        This works by checking if the diff between the merge-base and the branch
        is empty when compared against the target. If target contains all the
        changes from branch, the branch is effectively merged (even via squash).

        Args:
            branch: The branch to check.
            target: The target branch (e.g., main).

        Returns:
            True if all changes from branch are in target.
        """
        try:
            # Get the merge base between branch and target
            merge_base = self.get_merge_base(branch, target)
            if not merge_base:
                return False

            # Get the tree (file state) at each point
            branch_tree = self.repo.commit(branch).tree
            target_tree = self.repo.commit(target).tree
            base_tree = self.repo.commit(merge_base).tree

            # Get files changed in branch (compared to merge base)
            branch_diff = base_tree.diff(branch_tree)

            # For each file changed in branch, check if target has the same content
            for diff_item in branch_diff:
                # Get the path of the changed file
                path = diff_item.b_path or diff_item.a_path
                if not path:
                    continue

                # Get the blob (file content) in branch
                try:
                    branch_blob = branch_tree[path]
                except KeyError:
                    # File was deleted in branch
                    # Check if it's also deleted/missing in target
                    try:
                        target_tree[path]
                        return False  # File exists in target but deleted in branch
                    except KeyError:
                        continue  # Both deleted, OK

                # Get the blob in target
                try:
                    target_blob = target_tree[path]
                except KeyError:
                    return False  # File doesn't exist in target

                # Compare content
                if branch_blob.data_stream.read() != target_blob.data_stream.read():
                    return False

            return True
        except Exception:
            return False

"""Branch metadata storage using a JSON file in .git directory.

This module provides atomic read/write access to branch metadata stored in
`.git/shortcake.json`. The file structure is:

{
    "version": 1,
    "branches": {
        "branch-name": {
            "parent": "main",
            "parent_revision": "abc123...",
            "pr_number": 42,
            "pr_url": "https://github.com/..."
        }
    }
}
"""

import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict


class BranchMetadata(TypedDict, total=False):
    """Type definition for branch metadata."""

    parent: str
    parent_revision: str
    pr_number: int
    pr_url: str


class MetadataStore:
    """Handles reading and writing branch metadata to .git/shortcake.json."""

    FILENAME = "shortcake.json"
    VERSION = 1

    def __init__(self, git_dir: Path | None = None):
        """Initialize the metadata store.

        Args:
            git_dir: Path to the .git directory. If None, will be detected.
        """
        if git_dir is None:
            git_dir = self._find_git_dir()
        self.git_dir = git_dir
        self.filepath = git_dir / self.FILENAME

    def _find_git_dir(self) -> Path:
        """Find the .git directory from current working directory."""
        cwd = Path.cwd()
        while cwd != cwd.parent:
            git_dir = cwd / ".git"
            if git_dir.is_dir():
                return git_dir
            cwd = cwd.parent
        raise FileNotFoundError("Not in a git repository")

    def _read_file(self) -> dict:
        """Read and parse the JSON file.

        Returns:
            The parsed JSON data, or empty structure if file doesn't exist.
        """
        if not self.filepath.exists():
            return {"version": self.VERSION, "branches": {}}

        try:
            content = self.filepath.read_text()
            data = json.loads(content)
            # Ensure required structure exists
            if "branches" not in data:
                data["branches"] = {}
            return data
        except (json.JSONDecodeError, OSError):
            # File is corrupted or unreadable, start fresh
            return {"version": self.VERSION, "branches": {}}

    def _write_file(self, data: dict) -> None:
        """Write data to the JSON file atomically.

        Uses a temporary file and rename to ensure atomic writes,
        preventing corruption from concurrent access or crashes.

        Args:
            data: The data to write.
        """
        data["version"] = self.VERSION

        # Write to temp file in same directory (ensures same filesystem for rename)
        fd, temp_path = tempfile.mkstemp(
            dir=self.git_dir,
            prefix=".shortcake-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")  # Trailing newline
            # Atomic rename
            os.replace(temp_path, self.filepath)
        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def get(self, branch: str) -> BranchMetadata:
        """Get metadata for a branch.

        Args:
            branch: The branch name.

        Returns:
            The branch metadata, or empty dict if not found.
        """
        data = self._read_file()
        return data["branches"].get(branch, {})

    def set(self, branch: str, metadata: BranchMetadata) -> None:
        """Set metadata for a branch.

        Args:
            branch: The branch name.
            metadata: The metadata to store.
        """
        data = self._read_file()
        data["branches"][branch] = metadata
        self._write_file(data)

    def update(self, branch: str, **kwargs: str | int | None) -> None:
        """Update specific fields in a branch's metadata.

        Args:
            branch: The branch name.
            **kwargs: Fields to update. Use None to remove a field.
        """
        data = self._read_file()
        if branch not in data["branches"]:
            data["branches"][branch] = {}

        for key, value in kwargs.items():
            if value is None:
                data["branches"][branch].pop(key, None)
            else:
                data["branches"][branch][key] = value

        self._write_file(data)

    def delete(self, branch: str) -> bool:
        """Delete metadata for a branch.

        Args:
            branch: The branch name.

        Returns:
            True if the branch existed and was deleted, False otherwise.
        """
        data = self._read_file()
        if branch in data["branches"]:
            del data["branches"][branch]
            self._write_file(data)
            return True
        return False

    def get_all(self) -> dict[str, BranchMetadata]:
        """Get metadata for all branches.

        Returns:
            Dict mapping branch names to their metadata.
        """
        data = self._read_file()
        return data["branches"]

    def get_children(self, branch: str) -> list[str]:
        """Get all branches that have the given branch as their parent.

        Args:
            branch: The parent branch name.

        Returns:
            List of child branch names.
        """
        children = []
        for name, meta in self.get_all().items():
            if meta.get("parent") == branch:
                children.append(name)
        return children

    def rename(self, old_name: str, new_name: str) -> bool:
        """Rename a branch in the metadata.

        Also updates any branches that have old_name as their parent.

        Args:
            old_name: The current branch name.
            new_name: The new branch name.

        Returns:
            True if the branch existed and was renamed, False otherwise.
        """
        data = self._read_file()
        if old_name not in data["branches"]:
            return False

        # Move the metadata
        data["branches"][new_name] = data["branches"].pop(old_name)

        # Update any children
        for meta in data["branches"].values():
            if meta.get("parent") == old_name:
                meta["parent"] = new_name

        self._write_file(data)
        return True


# Module-level convenience functions using a default store instance
_default_store: MetadataStore | None = None


def _get_store() -> MetadataStore:
    """Get or create the default metadata store."""
    global _default_store
    if _default_store is None:
        _default_store = MetadataStore()
    return _default_store


def get_branch_metadata(branch: str) -> BranchMetadata:
    """Get metadata for a branch."""
    return _get_store().get(branch)


def set_branch_metadata(branch: str, metadata: BranchMetadata) -> None:
    """Set metadata for a branch."""
    _get_store().set(branch, metadata)


def update_branch_metadata(branch: str, **kwargs: str | int | None) -> None:
    """Update specific fields in a branch's metadata."""
    _get_store().update(branch, **kwargs)


def delete_branch_metadata(branch: str) -> bool:
    """Delete metadata for a branch."""
    return _get_store().delete(branch)


def get_all_branch_metadata() -> dict[str, BranchMetadata]:
    """Get metadata for all branches."""
    return _get_store().get_all()


def get_children(branch: str) -> list[str]:
    """Get all branches that have the given branch as their parent."""
    return _get_store().get_children(branch)


def rename_branch_metadata(old_name: str, new_name: str) -> bool:
    """Rename a branch in the metadata."""
    return _get_store().rename(old_name, new_name)


def reset_store() -> None:
    """Reset the default store (useful for testing)."""
    global _default_store
    _default_store = None

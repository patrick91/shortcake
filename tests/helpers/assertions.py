"""Custom assertions for testing."""

import re
from pathlib import Path

from tests.helpers.git_helpers import get_branches, get_current_branch, get_notes


def normalize_commit_shas(output: str) -> str:
    """Replace commit SHAs with a placeholder for deterministic comparison.

    Replaces patterns like "abc1234 - Commit message" with "SHA - Commit message".
    """
    return re.sub(r"\b[0-9a-f]{7}\b(?= - )", "SHA", output)


def assert_branch_exists(repo_path: Path, branch_name: str) -> None:
    """Assert that a branch exists."""
    branches = get_branches(repo_path)
    assert (
        branch_name in branches
    ), f"Branch '{branch_name}' not found. Available branches: {branches}"


def assert_branch_not_exists(repo_path: Path, branch_name: str) -> None:
    """Assert that a branch does not exist."""
    branches = get_branches(repo_path)
    assert branch_name not in branches, f"Branch '{branch_name}' exists but should not"


def assert_current_branch(repo_path: Path, expected_branch: str) -> None:
    """Assert the current branch name."""
    current = get_current_branch(repo_path)
    assert current == expected_branch, f"Expected branch '{expected_branch}', but on '{current}'"


def assert_notes_exist(repo_path: Path, ref: str = "HEAD", notes_ref: str = "shortcake") -> None:
    """Assert that git notes exist for a commit."""
    notes = get_notes(repo_path, ref, notes_ref)
    assert notes is not None, f"No notes found for {ref} in notes ref '{notes_ref}'"


def assert_notes_not_exist(
    repo_path: Path, ref: str = "HEAD", notes_ref: str = "shortcake"
) -> None:
    """Assert that git notes do not exist for a commit."""
    notes = get_notes(repo_path, ref, notes_ref)
    assert notes is None, f"Notes unexpectedly found for {ref} in notes ref '{notes_ref}': {notes}"


def assert_notes_contain(
    repo_path: Path,
    expected_content: str,
    ref: str = "HEAD",
    notes_ref: str = "shortcake",
) -> None:
    """Assert that git notes contain specific content."""
    notes = get_notes(repo_path, ref, notes_ref)
    assert notes is not None, f"No notes found for {ref} in notes ref '{notes_ref}'"
    assert (
        expected_content in notes
    ), f"Expected notes to contain '{expected_content}', but got: {notes}"


def assert_file_exists(file_path: Path) -> None:
    """Assert that a file exists."""
    assert file_path.exists(), f"File '{file_path}' does not exist"


def assert_file_not_exists(file_path: Path) -> None:
    """Assert that a file does not exist."""
    assert not file_path.exists(), f"File '{file_path}' exists but should not"


def assert_file_contains(file_path: Path, expected_content: str) -> None:
    """Assert that a file contains specific content."""
    assert_file_exists(file_path)
    content = file_path.read_text()
    assert (
        expected_content in content
    ), f"Expected file to contain '{expected_content}', but got: {content}"


def assert_output_contains(output: str, expected: str) -> None:
    """Assert that command output contains expected text."""
    assert expected in output, f"Expected output to contain '{expected}', but got: {output}"


def assert_output_not_contains(output: str, unexpected: str) -> None:
    """Assert that command output does not contain specific text."""
    assert (
        unexpected not in output
    ), f"Expected output to not contain '{unexpected}', but got: {output}"


def assert_pr_metadata(
    notes: str,
    pr_number: int | None = None,
    parent_branch: str | None = None,
) -> None:
    """Assert PR metadata in git notes.

    Args:
        notes: The notes content to check
        pr_number: Expected PR number (if any)
        parent_branch: Expected parent branch (if any)
    """
    if pr_number is not None:
        assert (
            f'"pr_number": {pr_number}' in notes or f'"pr_number":{pr_number}' in notes
        ), f"Expected PR number {pr_number} in notes, but got: {notes}"

    if parent_branch is not None:
        assert (
            f'"parent": "{parent_branch}"' in notes
        ), f"Expected parent branch '{parent_branch}' in notes, but got: {notes}"


def assert_stack_structure(
    repo_path: Path,
    expected_structure: dict[str, str | None],
) -> None:
    """Assert the structure of a branch stack.

    Args:
        repo_path: Path to the repository
        expected_structure: Dict mapping branch names to their parent branch (or None for root)
    """
    for branch, expected_parent in expected_structure.items():
        assert_branch_exists(repo_path, branch)
        notes = get_notes(repo_path, branch, "shortcake")

        if expected_parent is None:
            # Root branch - notes might not exist or should not have a parent
            if notes:
                assert (
                    '"parent"' not in notes
                ), f"Branch '{branch}' should be root but has parent in notes: {notes}"
        else:
            # Child branch - should have parent in notes
            assert notes is not None, f"Branch '{branch}' should have notes but doesn't"
            assert_pr_metadata(notes, parent_branch=expected_parent)

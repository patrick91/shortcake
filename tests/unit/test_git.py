"""Tests for the git module."""

from pathlib import Path

import pytest

from shortcake.git import GitError, GitRepo


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """Create a bare git repository."""
    bare_path = tmp_path / "bare.git"
    GitRepo.create_bare_repo(bare_path)
    return bare_path


def test_git_repo_not_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test GitRepo raises error when not in a git repository."""
    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()
    monkeypatch.chdir(non_repo)

    with pytest.raises(GitError) as exc_info:
        GitRepo()

    assert "not a git repository" in str(exc_info.value)


def test_create_bare_repo(tmp_path: Path):
    """Test creating a bare repository."""
    bare_path = tmp_path / "test_bare.git"
    GitRepo.create_bare_repo(bare_path)

    assert bare_path.exists()
    assert (bare_path / "HEAD").exists()


def test_create_bare_repo_error(tmp_path: Path):
    """Test create_bare_repo error handling."""
    # Create a file where we want to create the repo
    blocking_file = tmp_path / "blocked.git"
    blocking_file.write_text("blocking")

    with pytest.raises(GitError) as exc_info:
        GitRepo.create_bare_repo(blocking_file / "nested")

    assert "Failed to create bare repository" in str(exc_info.value)


def test_get_current_branch(isolated_git_repo: Path):
    """Test getting current branch name."""
    git = GitRepo(isolated_git_repo)

    # Default branch after init
    branch = git.get_current_branch()
    assert branch in ("main", "master")


def test_create_branch(isolated_git_repo: Path):
    """Test creating a new branch."""
    git = GitRepo(isolated_git_repo)

    git.create_branch("test-branch")

    assert git.get_current_branch() == "test-branch"
    assert "test-branch" in git.get_branches()


def test_create_branch_without_checkout(isolated_git_repo: Path):
    """Test creating a branch without checking it out."""
    git = GitRepo(isolated_git_repo)
    original_branch = git.get_current_branch()

    git.create_branch("no-checkout-branch", checkout=False)

    assert git.get_current_branch() == original_branch
    assert "no-checkout-branch" in git.get_branches()


def test_checkout_branch(isolated_git_repo: Path):
    """Test checking out an existing branch."""
    git = GitRepo(isolated_git_repo)
    git.create_branch("feature", checkout=False)

    git.checkout_branch("feature")

    assert git.get_current_branch() == "feature"


def test_checkout_branch_error(isolated_git_repo: Path):
    """Test checkout_branch error when branch doesn't exist."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.checkout_branch("nonexistent")

    assert "Failed to checkout branch" in str(exc_info.value)


def test_rename_branch(isolated_git_repo: Path):
    """Test renaming a branch."""
    git = GitRepo(isolated_git_repo)
    git.create_branch("old-name")

    git.rename_branch("old-name", "new-name")

    assert "new-name" in git.get_branches()
    assert "old-name" not in git.get_branches()


def test_rename_branch_error(isolated_git_repo: Path):
    """Test rename_branch error when branch doesn't exist."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.rename_branch("nonexistent", "new-name")

    assert "Failed to rename branch" in str(exc_info.value)


def test_delete_branch(isolated_git_repo: Path):
    """Test deleting a branch."""
    git = GitRepo(isolated_git_repo)
    git.create_branch("to-delete", checkout=False)

    git.delete_branch("to-delete")

    assert "to-delete" not in git.get_branches()


def test_delete_branch_error(isolated_git_repo: Path):
    """Test delete_branch error when branch doesn't exist."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.delete_branch("nonexistent")

    assert "Failed to delete branch" in str(exc_info.value)


def test_add_files(isolated_git_repo: Path):
    """Test staging files."""
    git = GitRepo(isolated_git_repo)
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    git.add_files("test.txt")

    assert git.has_staged_changes()


def test_add_files_list(isolated_git_repo: Path):
    """Test staging multiple files as a list."""
    git = GitRepo(isolated_git_repo)
    (isolated_git_repo / "file1.txt").write_text("content1")
    (isolated_git_repo / "file2.txt").write_text("content2")

    git.add_files(["file1.txt", "file2.txt"])

    assert git.has_staged_changes()


def test_add_files_error(isolated_git_repo: Path):
    """Test add_files error with invalid path."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.add_files("/nonexistent/path/file.txt")

    assert "Failed to add files" in str(exc_info.value)


def test_commit_with_message(isolated_git_repo: Path):
    """Test creating a commit with a message."""
    git = GitRepo(isolated_git_repo)
    test_file = isolated_git_repo / "commit_test.txt"
    test_file.write_text("commit content")
    git.add_files("commit_test.txt")

    git.commit("Test commit message")

    assert git.get_last_commit_message() == "Test commit message"


def test_commit_amend(isolated_git_repo: Path):
    """Test amending a commit."""
    git = GitRepo(isolated_git_repo)
    original_sha = git.get_current_commit()

    test_file = isolated_git_repo / "amend_test.txt"
    test_file.write_text("amend content")
    git.add_files("amend_test.txt")

    git.commit(amend=True)

    # SHA should change after amend
    assert git.get_current_commit() != original_sha


def test_get_last_commit_message(isolated_git_repo: Path):
    """Test getting the last commit message."""
    git = GitRepo(isolated_git_repo)

    # Initial commit message
    message = git.get_last_commit_message()
    assert message == "Initial commit"


def test_get_current_commit(isolated_git_repo: Path):
    """Test getting current commit SHA."""
    git = GitRepo(isolated_git_repo)

    sha = git.get_current_commit()

    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_get_commit_message(isolated_git_repo: Path):
    """Test getting commit message for a ref."""
    git = GitRepo(isolated_git_repo)

    message = git.get_commit_message("HEAD")

    assert "Initial commit" in message


def test_get_commit_message_error(isolated_git_repo: Path):
    """Test get_commit_message error with invalid ref."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.get_commit_message("nonexistent-ref")

    assert "Failed to get commit message" in str(exc_info.value)


def test_get_branches(isolated_git_repo: Path):
    """Test getting list of branches."""
    git = GitRepo(isolated_git_repo)
    git.create_branch("branch1", checkout=False)
    git.create_branch("branch2", checkout=False)

    branches = git.get_branches()

    assert "branch1" in branches
    assert "branch2" in branches


def test_branch_exists(isolated_git_repo: Path):
    """Test checking if branch exists."""
    git = GitRepo(isolated_git_repo)
    git.create_branch("exists", checkout=False)

    assert git.branch_exists("exists")
    assert not git.branch_exists("does-not-exist")


def test_get_notes_and_add_notes(isolated_git_repo: Path):
    """Test adding and getting git notes."""
    git = GitRepo(isolated_git_repo)

    # Initially no notes
    assert git.get_notes("HEAD", "test-notes") is None

    # Add notes
    git.add_notes("test note content", "HEAD", "test-notes")

    # Retrieve notes
    notes = git.get_notes("HEAD", "test-notes")
    assert notes == "test note content"


def test_add_notes_error(isolated_git_repo: Path):
    """Test add_notes error with invalid ref."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.add_notes("content", "nonexistent-ref", "shortcake")

    assert "Failed to add notes" in str(exc_info.value)


def test_add_remote(isolated_git_repo: Path, bare_repo: Path):
    """Test adding a remote."""
    git = GitRepo(isolated_git_repo)

    git.add_remote("origin", str(bare_repo))

    remotes = [r.name for r in git.repo.remotes]
    assert "origin" in remotes


def test_add_remote_error(isolated_git_repo: Path, bare_repo: Path):
    """Test add_remote error when remote already exists."""
    git = GitRepo(isolated_git_repo)
    git.add_remote("origin", str(bare_repo))

    with pytest.raises(GitError) as exc_info:
        git.add_remote("origin", str(bare_repo))

    assert "Failed to add remote" in str(exc_info.value)


def test_push(isolated_git_repo: Path, bare_repo: Path):
    """Test pushing to a remote."""
    git = GitRepo(isolated_git_repo)
    git.add_remote("origin", str(bare_repo))

    git.push("origin", git.get_current_branch())

    # Verify push succeeded by checking bare repo
    bare = GitRepo.__new__(GitRepo)
    from git import Repo

    bare.repo = Repo(bare_repo)
    assert git.get_current_branch() in [h.name for h in bare.repo.heads]


def test_push_error(isolated_git_repo: Path):
    """Test push error when remote doesn't exist."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.push("nonexistent", "main")

    assert "Push failed" in str(exc_info.value) or "Failed to push" in str(exc_info.value)


def test_fetch(isolated_git_repo: Path, bare_repo: Path):
    """Test fetching from a remote."""
    git = GitRepo(isolated_git_repo)
    git.add_remote("origin", str(bare_repo))
    git.push("origin", git.get_current_branch())

    # Fetch should not raise
    git.fetch("origin")


def test_fetch_error(isolated_git_repo: Path):
    """Test fetch error when remote doesn't exist."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.fetch("nonexistent")

    assert "Failed to fetch" in str(exc_info.value)


def test_has_staged_changes_false(isolated_git_repo: Path):
    """Test has_staged_changes returns False when no staged changes."""
    git = GitRepo(isolated_git_repo)

    assert not git.has_staged_changes()


def test_has_staged_changes_true(isolated_git_repo: Path):
    """Test has_staged_changes returns True when there are staged changes."""
    git = GitRepo(isolated_git_repo)
    test_file = isolated_git_repo / "staged.txt"
    test_file.write_text("staged content")
    git.add_files("staged.txt")

    assert git.has_staged_changes()


def test_get_merge_base(isolated_git_repo: Path):
    """Test getting merge base between branches."""
    git = GitRepo(isolated_git_repo)
    main_branch = git.get_current_branch()
    main_sha = git.get_current_commit()

    git.create_branch("feature")
    test_file = isolated_git_repo / "feature.txt"
    test_file.write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Feature commit")

    merge_base = git.get_merge_base(main_branch, "feature")

    assert merge_base == main_sha


def test_get_merge_base_no_common_ancestor(isolated_git_repo: Path):
    """Test get_merge_base returns None for unrelated refs."""
    git = GitRepo(isolated_git_repo)

    # Invalid ref should return None
    result = git.get_merge_base("HEAD", "nonexistent")

    assert result is None


def test_is_ancestor(isolated_git_repo: Path):
    """Test checking if one commit is ancestor of another."""
    git = GitRepo(isolated_git_repo)
    main_branch = git.get_current_branch()

    git.create_branch("child")
    test_file = isolated_git_repo / "child.txt"
    test_file.write_text("child content")
    git.add_files("child.txt")
    git.commit("Child commit")

    assert git.is_ancestor(main_branch, "child")
    assert not git.is_ancestor("child", main_branch)


def test_count_commits_between(isolated_git_repo: Path):
    """Test counting commits between refs."""
    git = GitRepo(isolated_git_repo)
    main_branch = git.get_current_branch()

    git.create_branch("commits")
    for i in range(3):
        test_file = isolated_git_repo / f"file{i}.txt"
        test_file.write_text(f"content {i}")
        git.add_files(f"file{i}.txt")
        git.commit(f"Commit {i}")

    count = git.count_commits_between(main_branch, "commits")

    assert count == 3


def test_count_commits_between_invalid_ref(isolated_git_repo: Path):
    """Test count_commits_between returns 0 for invalid refs."""
    git = GitRepo(isolated_git_repo)

    count = git.count_commits_between("HEAD", "nonexistent")

    assert count == 0


def test_update_notes(isolated_git_repo: Path):
    """Test updating existing git notes."""
    git = GitRepo(isolated_git_repo)

    # Add initial notes
    git.add_notes("initial content", "HEAD", "test-notes")
    assert git.get_notes("HEAD", "test-notes") == "initial content"

    # Update notes
    git.update_notes("updated content", "HEAD", "test-notes")
    assert git.get_notes("HEAD", "test-notes") == "updated content"


def test_remove_notes(isolated_git_repo: Path):
    """Test removing git notes."""
    git = GitRepo(isolated_git_repo)

    # Add notes
    git.add_notes("content to remove", "HEAD", "test-notes")
    assert git.get_notes("HEAD", "test-notes") is not None

    # Remove notes
    git.remove_notes("HEAD", "test-notes")
    assert git.get_notes("HEAD", "test-notes") is None


def test_remove_notes_error(isolated_git_repo: Path):
    """Test remove_notes error when no notes exist."""
    git = GitRepo(isolated_git_repo)

    with pytest.raises(GitError) as exc_info:
        git.remove_notes("HEAD", "nonexistent-notes")

    assert "Failed to remove notes" in str(exc_info.value)


def test_is_rebase_in_progress_false(isolated_git_repo: Path):
    """Test is_rebase_in_progress returns False normally."""
    git = GitRepo(isolated_git_repo)

    assert not git.is_rebase_in_progress()


def test_get_commit_sha(isolated_git_repo: Path):
    """Test getting commit SHA for a ref."""
    git = GitRepo(isolated_git_repo)

    sha = git.get_commit_sha("HEAD")

    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_get_commit_sha_for_branch(isolated_git_repo: Path):
    """Test getting commit SHA for a branch."""
    git = GitRepo(isolated_git_repo)
    git.create_branch("test-branch", checkout=False)

    sha = git.get_commit_sha("test-branch")
    head_sha = git.get_commit_sha("HEAD")

    assert sha == head_sha


def test_has_remote_false(isolated_git_repo: Path):
    """Test has_remote returns False when no remote exists."""
    git = GitRepo(isolated_git_repo)

    assert not git.has_remote("origin")


def test_has_remote_true(isolated_git_repo: Path, bare_repo: Path):
    """Test has_remote returns True when remote exists."""
    git = GitRepo(isolated_git_repo)
    git.add_remote("origin", str(bare_repo))

    assert git.has_remote("origin")


def test_rebase_simple(isolated_git_repo: Path):
    """Test simple rebase onto another branch."""
    git = GitRepo(isolated_git_repo)

    # Create a commit on main
    (isolated_git_repo / "main_change.txt").write_text("main change")
    git.add_files("main_change.txt")
    git.commit("Main change")

    # Create a feature branch from initial commit
    git.checkout_branch("main")
    git.repo.git.checkout("HEAD~1")  # Go back one commit
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature")
    git.add_files("feature.txt")
    git.commit("Feature commit")

    # Rebase feature onto main
    git.rebase("main")

    # Feature should now be on top of main
    assert git.is_ancestor("main", "feature")


def test_is_tree_subset_with_squash_merge(isolated_git_repo: Path):
    """Test is_tree_subset detects squash merged content."""
    git = GitRepo(isolated_git_repo)

    # Create a feature branch with a file
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Add feature")

    # Simulate squash merge: add same content to main as a different commit
    git.checkout_branch("main")
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Squashed feature")

    # feature's changes should be detected as subset of main
    assert git.is_tree_subset("feature", "main")


def test_is_tree_subset_not_merged(isolated_git_repo: Path):
    """Test is_tree_subset returns False when content differs."""
    git = GitRepo(isolated_git_repo)

    # Create a feature branch with a file
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Add feature")

    # Main doesn't have this file
    git.checkout_branch("main")

    # feature's changes are NOT in main
    assert not git.is_tree_subset("feature", "main")


def test_is_tree_subset_partial_merge(isolated_git_repo: Path):
    """Test is_tree_subset returns False when only partially merged."""
    git = GitRepo(isolated_git_repo)

    # Create a feature branch with two files
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content 1")
    (isolated_git_repo / "file2.txt").write_text("content 2")
    git.add_files(["file1.txt", "file2.txt"])
    git.commit("Add two files")

    # Squash merge only one file to main
    git.checkout_branch("main")
    (isolated_git_repo / "file1.txt").write_text("content 1")
    git.add_files("file1.txt")
    git.commit("Partial squash")

    # feature is NOT fully merged (missing file2.txt)
    assert not git.is_tree_subset("feature", "main")


def test_is_squash_merged_detects_squash_merge(isolated_git_repo: Path):
    """Test is_squash_merged detects squash-merged branches using git cherry."""
    git = GitRepo(isolated_git_repo)

    # Create a feature branch with a commit
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Add feature")

    # Squash merge to main (same content, different commit)
    git.checkout_branch("main")
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Squashed feature")

    # feature should be detected as squash-merged
    assert git.is_squash_merged("feature", "main")


def test_is_squash_merged_returns_false_when_not_merged(isolated_git_repo: Path):
    """Test is_squash_merged returns False when branch is not merged."""
    git = GitRepo(isolated_git_repo)

    # Create a feature branch with a commit
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "feature.txt").write_text("feature content")
    git.add_files("feature.txt")
    git.commit("Add feature")

    # Main doesn't have this change
    git.checkout_branch("main")

    # feature is NOT merged
    assert not git.is_squash_merged("feature", "main")


def test_is_squash_merged_with_multiple_commits(isolated_git_repo: Path):
    """Test is_squash_merged works with multiple commits on feature branch."""
    git = GitRepo(isolated_git_repo)

    # Create a feature branch with multiple commits
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content 1")
    git.add_files("file1.txt")
    git.commit("Add file1")

    (isolated_git_repo / "file2.txt").write_text("content 2")
    git.add_files("file2.txt")
    git.commit("Add file2")

    # Squash merge both files to main in one commit
    git.checkout_branch("main")
    (isolated_git_repo / "file1.txt").write_text("content 1")
    (isolated_git_repo / "file2.txt").write_text("content 2")
    git.add_files(["file1.txt", "file2.txt"])
    git.commit("Squash merge feature")

    # feature should be detected as squash-merged
    assert git.is_squash_merged("feature", "main")


def test_is_squash_merged_partial_merge(isolated_git_repo: Path):
    """Test is_squash_merged returns False when only partially merged."""
    git = GitRepo(isolated_git_repo)

    # Create a feature branch with two commits
    git.create_branch("feature", checkout=True)
    (isolated_git_repo / "file1.txt").write_text("content 1")
    git.add_files("file1.txt")
    git.commit("Add file1")

    (isolated_git_repo / "file2.txt").write_text("content 2")
    git.add_files("file2.txt")
    git.commit("Add file2")

    # Only merge the first commit's changes to main
    git.checkout_branch("main")
    (isolated_git_repo / "file1.txt").write_text("content 1")
    git.add_files("file1.txt")
    git.commit("Partial squash")

    # feature is NOT fully merged (missing file2 changes)
    assert not git.is_squash_merged("feature", "main")


def test_get_worktree_for_branch_no_worktrees(isolated_git_repo: Path):
    git = GitRepo(isolated_git_repo)

    # No worktrees besides the main one
    result = git.get_worktree_for_branch("main")
    # Main working dir is technically a worktree, but the branch being checked out
    # in the main worktree should still be found
    assert result == isolated_git_repo or result is None


def test_get_worktree_for_branch_nonexistent_branch(isolated_git_repo: Path):
    git = GitRepo(isolated_git_repo)

    result = git.get_worktree_for_branch("nonexistent")
    assert result is None


def test_get_worktree_for_branch_finds_worktree(isolated_git_repo: Path, tmp_path: Path):
    import subprocess

    git = GitRepo(isolated_git_repo)

    # Create a branch
    git.create_branch("feature", checkout=False)

    # Create a worktree for that branch
    worktree_path = tmp_path / "feature-worktree"
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "feature"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Should find the worktree
    result = git.get_worktree_for_branch("feature")
    assert result == worktree_path

    # Clean up
    subprocess.run(
        ["git", "worktree", "remove", str(worktree_path)],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )


def test_checkout_in_worktree(isolated_git_repo: Path, tmp_path: Path):
    import subprocess

    git = GitRepo(isolated_git_repo)

    # Create two branches
    git.create_branch("feature1", checkout=False)
    git.create_branch("feature2", checkout=False)

    # Create a worktree for feature1
    worktree_path = tmp_path / "feature-worktree"
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "feature1"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Verify feature1 is checked out
    assert git.get_worktree_for_branch("feature1") == worktree_path

    # Switch worktree to feature2
    git.checkout_in_worktree(worktree_path, "feature2")

    # Verify feature2 is now checked out and feature1 is no longer in a worktree
    assert git.get_worktree_for_branch("feature2") == worktree_path
    assert git.get_worktree_for_branch("feature1") is None

    # Clean up
    subprocess.run(
        ["git", "worktree", "remove", str(worktree_path)],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )


def test_checkout_in_worktree_error(isolated_git_repo: Path, tmp_path: Path):
    import subprocess

    git = GitRepo(isolated_git_repo)

    # Create a branch and worktree
    git.create_branch("feature", checkout=False)
    worktree_path = tmp_path / "feature-worktree"
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "feature"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Try to checkout a nonexistent branch
    with pytest.raises(GitError) as exc_info:
        git.checkout_in_worktree(worktree_path, "nonexistent")

    assert "Failed to checkout" in str(exc_info.value)

    # Clean up
    subprocess.run(
        ["git", "worktree", "remove", str(worktree_path)],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

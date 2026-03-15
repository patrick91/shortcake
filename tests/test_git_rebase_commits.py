from pathlib import Path

import pytest

from shortcake import _git as git
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_ref,
    run_git,
    switch_branch,
)


def test_get_rebase_commits_same_commit(temp_repo: Repo) -> None:
    """Test get_rebase_commits returns empty when head equals merge_base."""
    head_sha = get_ref(temp_repo, "refs/heads/main")
    commits = git.get_rebase_commits(temp_repo, head_sha, head_sha)
    assert commits == []


def test_get_rebase_commits_no_parents(temp_repo: Repo) -> None:
    """Test get_rebase_commits handles root commit (no parents)."""
    # The initial commit has no parents - walk should stop there
    head_sha = get_ref(temp_repo, "refs/heads/main")
    # Use a non-existent merge_base that won't be found
    # The function should stop when it runs out of parents
    fake_base = b"0" * 40
    with pytest.raises(ValueError, match="Merge base not found"):
        git.get_rebase_commits(temp_repo, head_sha, fake_base)


def test_get_rebase_commits_rejects_merge_commit(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test get_rebase_commits rejects non-linear history with merge commits."""
    head_sha = get_ref(temp_repo, "refs/heads/main")

    # Create two branches from main
    create_branch(temp_repo, "branch1", head_sha)
    create_branch(temp_repo, "branch2", head_sha)

    # Add commit on branch1
    switch_branch(temp_repo, "branch1")
    commit_files(
        temp_repo,
        {tmp_path / "file1.txt": "content1"},
        "C1",
    )

    # Add commit on branch2
    switch_branch(temp_repo, "branch2")
    commit_files(
        temp_repo,
        {tmp_path / "file2.txt": "content2"},
        "C2",
    )

    # Merge branch2 into branch1 (creates merge commit)
    switch_branch(temp_repo, "branch1")
    run_git(temp_repo, "merge", "--no-ff", "--no-edit", "branch2")

    merge_sha = get_ref(temp_repo, "refs/heads/branch1")

    with pytest.raises(ValueError, match="Non-linear history"):
        git.get_rebase_commits(temp_repo, merge_sha, head_sha)


def test_get_rebase_commits_linear_chain(repo_with_feature: Repo) -> None:
    """Test get_rebase_commits returns commits walking the parent chain."""
    head_sha = get_ref(repo_with_feature, "refs/heads/feature")
    merge_base = get_ref(repo_with_feature, "refs/heads/main")
    commits = git.get_rebase_commits(repo_with_feature, head_sha, merge_base)
    assert len(commits) == 1
    assert commits[0] == head_sha

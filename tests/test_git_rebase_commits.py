import pytest
from dulwich.objects import Commit
from dulwich.repo import Repo

from shortcake import _git as git


def test_get_rebase_commits_same_commit(temp_repo: Repo) -> None:
    """Test get_rebase_commits returns empty when head equals merge_base."""
    head_sha = temp_repo.refs[b"refs/heads/main"]
    commits = git.get_rebase_commits(temp_repo, head_sha, head_sha)
    assert commits == []


def test_get_rebase_commits_no_parents(temp_repo: Repo) -> None:
    """Test get_rebase_commits handles root commit (no parents)."""
    # The initial commit has no parents - walk should stop there
    head_sha = temp_repo.refs[b"refs/heads/main"]
    # Use a non-existent merge_base that won't be found
    # The function should stop when it runs out of parents
    fake_base = b"0" * 40
    with pytest.raises(ValueError, match="Merge base not found"):
        git.get_rebase_commits(temp_repo, head_sha, fake_base)


def test_get_rebase_commits_rejects_merge_commit(temp_repo: Repo) -> None:
    """Test get_rebase_commits rejects non-linear history with merge commits."""
    head_sha = temp_repo.refs[b"refs/heads/main"]
    head_commit = temp_repo[head_sha]

    # Create two commits off main
    c1 = Commit()
    c1.tree = head_commit.tree
    c1.parents = [head_sha]
    c1.author = head_commit.author
    c1.committer = head_commit.committer
    c1.author_time = head_commit.author_time
    c1.author_timezone = head_commit.author_timezone
    c1.commit_time = head_commit.commit_time + 1
    c1.commit_timezone = head_commit.commit_timezone
    c1.message = b"C1"
    c1.encoding = head_commit.encoding
    temp_repo.object_store.add_object(c1)

    c2 = Commit()
    c2.tree = head_commit.tree
    c2.parents = [head_sha]
    c2.author = head_commit.author
    c2.committer = head_commit.committer
    c2.author_time = head_commit.author_time
    c2.author_timezone = head_commit.author_timezone
    c2.commit_time = head_commit.commit_time + 2
    c2.commit_timezone = head_commit.commit_timezone
    c2.message = b"C2"
    c2.encoding = head_commit.encoding
    temp_repo.object_store.add_object(c2)

    # Merge commit with two parents
    merge = Commit()
    merge.tree = head_commit.tree
    merge.parents = [c1.id, c2.id]
    merge.author = head_commit.author
    merge.committer = head_commit.committer
    merge.author_time = head_commit.author_time
    merge.author_timezone = head_commit.author_timezone
    merge.commit_time = head_commit.commit_time + 3
    merge.commit_timezone = head_commit.commit_timezone
    merge.message = b"Merge C1 and C2"
    merge.encoding = head_commit.encoding
    temp_repo.object_store.add_object(merge)

    temp_repo.refs[b"refs/heads/main"] = merge.id

    with pytest.raises(ValueError, match="Non-linear history"):
        git.get_rebase_commits(temp_repo, merge.id, head_sha)

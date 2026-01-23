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
    # This will walk to root and hit the "no parents" break
    commits = git.get_rebase_commits(temp_repo, head_sha, fake_base)
    # Should return the initial commit since it never found the base
    assert len(commits) >= 1

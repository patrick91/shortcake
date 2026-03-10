from pathlib import Path

import pytest

from shortcake import _git as git
from shortcake._git._core import CommitError
from tests._git_helpers import Repo, switch_branch


def test_cherry_pick_raises_rebase_failure(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test cherry_pick wraps dulwich errors in RebaseFailure."""

    def mock_cherry_pick(repo, commit):
        raise CommitError("Cherry-pick conflict")

    monkeypatch.setattr(
        "shortcake._git._rebase.porcelain.cherry_pick",
        mock_cherry_pick,
    )

    with pytest.raises(git.RebaseFailure, match="Cherry-pick conflict"):
        git.cherry_pick(temp_repo, b"abc123")


def test_cherry_pick_success(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test cherry_pick copies a commit."""
    # Get the feature commit SHA
    feature_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Switch to main
    switch_branch(repo_with_feature, "main")
    original_main_sha = repo_with_feature.refs[b"refs/heads/main"]

    # Cherry-pick the feature commit
    git.cherry_pick(repo_with_feature, feature_sha)

    # Main should have moved
    new_main_sha = repo_with_feature.refs[b"refs/heads/main"]
    assert new_main_sha != original_main_sha

from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git


def test_cherry_pick_success(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test cherry_pick copies a commit."""
    # Get the feature commit SHA
    feature_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Switch to main
    porcelain.switch(repo_with_feature, "main")
    original_main_sha = repo_with_feature.refs[b"refs/heads/main"]

    # Cherry-pick the feature commit
    git.cherry_pick(repo_with_feature, feature_sha)

    # Main should have moved
    new_main_sha = repo_with_feature.refs[b"refs/heads/main"]
    assert new_main_sha != original_main_sha

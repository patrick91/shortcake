from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shortcake import _git as git
from tests._git_helpers import Repo, get_ref, switch_branch


def test_cherry_pick_raises_rebase_failure(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test cherry_pick wraps subprocess errors in RebaseFailure."""

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "Cherry-pick conflict"

    monkeypatch.setattr(
        "shortcake._git._rebase.subprocess.run",
        lambda *args, **kwargs: mock_result,
    )

    with pytest.raises(git.RebaseFailure, match="Cherry-pick conflict"):
        git.cherry_pick(temp_repo, b"abc123")


def test_cherry_pick_success(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test cherry_pick copies a commit."""
    # Get the feature commit SHA
    feature_sha = get_ref(repo_with_feature, "refs/heads/feature")

    # Switch to main
    switch_branch(repo_with_feature, "main")
    original_main_sha = get_ref(repo_with_feature, "refs/heads/main")

    # Cherry-pick the feature commit
    git.cherry_pick(repo_with_feature, feature_sha)

    # Main should have moved
    new_main_sha = get_ref(repo_with_feature, "refs/heads/main")
    assert new_main_sha != original_main_sha

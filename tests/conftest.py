from pathlib import Path

import pytest

from shortcake._github import GitHubClient
from shortcake._trailers import Trailers
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_branch_head,
    init_repo,
    switch_branch,
    update_branch,
)


@pytest.fixture(autouse=True)
def _skip_repo_identity_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip GitHubClient._resolve_repo_identity for all tests by default.

    The identity resolution makes a real API call on __enter__. Tests that
    need to exercise it should call _real_resolve_repo_identity() explicitly.
    """
    monkeypatch.setattr(GitHubClient, "_resolve_repo_identity", lambda self: None)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Repo:
    """Create a temporary git repo with initial commit on main."""
    repo = init_repo(tmp_path)
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")
    return repo


@pytest.fixture
def repo_with_feature(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with main and a feature branch (1 commit)."""
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    commit_files(
        temp_repo,
        {tmp_path / "feature.txt": "feature content"},
        "Add feature",
    )
    return temp_repo


@pytest.fixture
def repo_with_tracked_feature(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with main and a tracked feature branch."""
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit_files(
        temp_repo,
        {tmp_path / "feature.txt": "feature content"},
        message,
    )
    return temp_repo


@pytest.fixture
def repo_with_stack(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a linear stack: main → branch_a → branch_b."""
    create_branch(
        temp_repo,
        "branch_a",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    commit_files(temp_repo, {tmp_path / "a.txt": "branch a content"}, message_a)
    branch_a_sha = get_branch_head(temp_repo, "branch_a")

    create_branch(temp_repo, "branch_b", branch_a_sha, checkout=True)
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    commit_files(temp_repo, {tmp_path / "b.txt": "branch b content"}, message_b)
    return temp_repo


@pytest.fixture
def repo_with_stack_behind(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with stack where main has moved ahead.

    Creates: main → branch_a → branch_b
    Then adds a commit to main, so branch_a needs rebasing.
    """
    create_branch(
        temp_repo,
        "branch_a",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    commit_files(temp_repo, {tmp_path / "a.txt": "branch a content"}, message_a)
    branch_a_sha = get_branch_head(temp_repo, "branch_a")

    create_branch(temp_repo, "branch_b", branch_a_sha, checkout=True)
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    commit_files(temp_repo, {tmp_path / "b.txt": "branch b content"}, message_b)

    # Now add a commit to main (to make branch_a behind)
    # Use switch_branch to properly reset index and working tree
    switch_branch(temp_repo, "main")
    commit_files(
        temp_repo,
        {tmp_path / "main_update.txt": "main update"},
        "chore: update main",
    )

    # Switch back to branch_b
    switch_branch(temp_repo, "branch_b")

    return temp_repo


@pytest.fixture
def repo_with_merged_branch(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a tracked feature branch merged into main.

    Creates: main → feature (tracked)
    Then merges feature into main and adds a commit to main, so feature is merged.
    """
    create_branch(
        temp_repo,
        "feature",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit_files(temp_repo, {tmp_path / "feature.txt": "feature content"}, message)
    feature_sha = get_branch_head(temp_repo, "feature")

    # Fast-forward main to feature (simulates merge)
    update_branch(temp_repo, "main", feature_sha)

    # Add another commit to main so it's ahead of feature
    switch_branch(temp_repo, "main")
    commit_files(
        temp_repo,
        {tmp_path / "main_after_merge.txt": "main after merge"},
        "chore: post-merge commit",
    )

    # Switch back to feature
    switch_branch(temp_repo, "feature")

    return temp_repo


@pytest.fixture
def repo_with_merged_and_children(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo where branch_a is merged but has child branch_b.

    Creates: main → branch_a → branch_b
    Then merges branch_a into main (with follow-up commit), so branch_a is merged.
    """
    create_branch(
        temp_repo,
        "branch_a",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    commit_files(temp_repo, {tmp_path / "a.txt": "branch a content"}, message_a)
    branch_a_sha = get_branch_head(temp_repo, "branch_a")

    create_branch(temp_repo, "branch_b", branch_a_sha, checkout=True)
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    commit_files(temp_repo, {tmp_path / "b.txt": "branch b content"}, message_b)

    # Fast-forward main to branch_a (simulates merge of branch_a)
    update_branch(temp_repo, "main", branch_a_sha)

    # Add commit to main so it's ahead of branch_a
    switch_branch(temp_repo, "main")
    commit_files(
        temp_repo,
        {tmp_path / "main_after_merge.txt": "main after merge"},
        "chore: post-merge commit",
    )

    # Switch to branch_b
    switch_branch(temp_repo, "branch_b")

    return temp_repo


@pytest.fixture
def repo_with_fork(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with forked stack: main → branch_a → (branch_b, branch_c)."""
    create_branch(
        temp_repo,
        "branch_a",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    commit_files(temp_repo, {tmp_path / "a.txt": "branch a content"}, message_a)
    branch_a_sha = get_branch_head(temp_repo, "branch_a")

    create_branch(temp_repo, "branch_b", branch_a_sha, checkout=True)
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    commit_files(temp_repo, {tmp_path / "b.txt": "branch b content"}, message_b)

    # Create branch_c from branch_a (fork)
    create_branch(temp_repo, "branch_c", branch_a_sha, checkout=True)
    trailers_c = Trailers(parent_branch="branch_a")
    message_c = trailers_c.apply_to("feat: branch c")
    commit_files(temp_repo, {tmp_path / "c.txt": "branch c content"}, message_c)
    return temp_repo

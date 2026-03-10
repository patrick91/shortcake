"""Fixtures for navigation command tests."""

from pathlib import Path

import pytest

from shortcake._trailers import Trailers
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_branch_head,
    init_repo,
)


@pytest.fixture
def repo_with_stack(tmp_path: Path) -> Repo:
    """
    Create a repo with a linear stack: main → branch_a → branch_b → branch_c.

    All branches are tracked with Shortcake-Parent trailers.
    """
    repo = init_repo(tmp_path)
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    # Create branch_a from main
    create_branch(repo, "branch_a", get_branch_head(repo, "main"), checkout=True)

    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit_files(repo, {tmp_path / "a.txt": "a"}, msg_a)

    # Create branch_b from branch_a
    branch_a_sha = get_branch_head(repo, "branch_a")
    create_branch(repo, "branch_b", branch_a_sha, checkout=True)

    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    commit_files(repo, {tmp_path / "b.txt": "b"}, msg_b)

    # Create branch_c from branch_b
    branch_b_sha = get_branch_head(repo, "branch_b")
    create_branch(repo, "branch_c", branch_b_sha, checkout=True)

    trailers_c = Trailers(parent_branch="branch_b")
    msg_c = trailers_c.apply_to("feat: branch c")
    commit_files(repo, {tmp_path / "c.txt": "c"}, msg_c)

    return repo


@pytest.fixture
def repo_with_fork(tmp_path: Path) -> Repo:
    """
    Create a repo with a forked stack: main → branch_a → (branch_b, branch_c).

    branch_b and branch_c both have branch_a as parent.
    """
    repo = init_repo(tmp_path)
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    # Create branch_a from main
    create_branch(repo, "branch_a", get_branch_head(repo, "main"), checkout=True)

    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    commit_files(repo, {tmp_path / "a.txt": "a"}, msg_a)

    # Create branch_b from branch_a
    branch_a_sha = get_branch_head(repo, "branch_a")
    create_branch(repo, "branch_b", branch_a_sha, checkout=True)

    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    commit_files(repo, {tmp_path / "b.txt": "b"}, msg_b)

    # Create branch_c from branch_a (fork!)
    create_branch(repo, "branch_c", branch_a_sha, checkout=True)

    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: branch c")
    commit_files(repo, {tmp_path / "c.txt": "c"}, msg_c)

    return repo


@pytest.fixture
def repo_with_tracked_feature(tmp_path: Path) -> Repo:
    """
    Create a repo with main and a single tracked feature branch.

    main → feature (tracked with trailer)
    """
    repo = init_repo(tmp_path)
    commit_files(repo, {tmp_path / "README.md": "# Test"}, "Initial commit")

    # Create feature branch from main
    create_branch(repo, "feature", get_branch_head(repo, "main"), checkout=True)

    trailers = Trailers(parent_branch="main")
    msg = trailers.apply_to("feat: add feature")
    commit_files(repo, {tmp_path / "feature.txt": "feature"}, msg)

    return repo

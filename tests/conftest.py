import subprocess
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake._github import GitHubClient
from shortcake._trailers import Trailers


@pytest.fixture(autouse=True)
def _skip_repo_identity_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip GitHubClient._resolve_repo_identity for all tests by default.

    The identity resolution makes a real API call on __enter__. Tests that
    need to exercise it should call _real_resolve_repo_identity() explicitly.
    """
    monkeypatch.setattr(
        GitHubClient, "_resolve_repo_identity", lambda self: None
    )


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset.

    dulwich's porcelain.switch doesn't fully reset the index, which can
    cause files from the old branch to be included in new commits.
    This helper sets HEAD first, then uses reset --hard to update the
    index and working tree without moving any branch refs.
    """
    ref = f"refs/heads/{branch}".encode()
    # Set HEAD to target branch first
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    # Reset index and working tree to match HEAD (doesn't move branch refs)
    porcelain.reset(repo, "hard")


@pytest.fixture
def temp_repo(tmp_path: Path) -> Repo:
    """Create a temporary git repo with initial commit on main."""
    repo = Repo.init(tmp_path, default_branch=b"main")

    # Configure git user identity for git CLI operations (needed for rebase)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )

    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    return repo


@pytest.fixture
def repo_with_feature(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with main and a feature branch (1 commit)."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add a commit on feature
    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    porcelain.add(temp_repo, paths=[str(test_file)])
    porcelain.commit(temp_repo, message=b"Add feature")

    return temp_repo


@pytest.fixture
def repo_with_tracked_feature(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with main and a tracked feature branch."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add a commit on feature with trailer
    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    porcelain.add(temp_repo, paths=[str(test_file)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    porcelain.commit(temp_repo, message=message.encode())

    return temp_repo


@pytest.fixture
def repo_with_stack(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a linear stack: main → branch_a → branch_b."""
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # Commit on branch_a with trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    # Commit on branch_b with trailer
    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    return temp_repo


@pytest.fixture
def repo_with_stack_behind(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with stack where main has moved ahead.

    Creates: main → branch_a → branch_b
    Then adds a commit to main, so branch_a needs rebasing.
    """
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # Commit on branch_a with trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    # Commit on branch_b with trailer
    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    # Now add a commit to main (to make branch_a behind)
    # Use switch_branch to properly reset index and working tree
    switch_branch(temp_repo, "main")
    main_file = tmp_path / "main_update.txt"
    main_file.write_text("main update")
    porcelain.add(temp_repo, paths=[str(main_file)])
    porcelain.commit(temp_repo, message=b"chore: update main")

    # Switch back to branch_b
    switch_branch(temp_repo, "branch_b")

    return temp_repo


@pytest.fixture
def repo_with_merged_branch(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a tracked feature branch merged into main.

    Creates: main → feature (tracked)
    Then merges feature into main and adds a commit to main, so feature is merged.
    """
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Commit on feature with trailer
    file_a = tmp_path / "feature.txt"
    file_a.write_text("feature content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    porcelain.commit(temp_repo, message=message.encode())
    feature_sha = temp_repo.refs[b"refs/heads/feature"]

    # Fast-forward main to feature (simulates merge)
    temp_repo.refs[b"refs/heads/main"] = feature_sha

    # Add another commit to main so it's ahead of feature
    switch_branch(temp_repo, "main")
    main_file = tmp_path / "main_after_merge.txt"
    main_file.write_text("main after merge")
    porcelain.add(temp_repo, paths=[str(main_file)])
    porcelain.commit(temp_repo, message=b"chore: post-merge commit")

    # Switch back to feature
    switch_branch(temp_repo, "feature")

    return temp_repo


@pytest.fixture
def repo_with_merged_and_children(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo where branch_a is merged but has child branch_b.

    Creates: main → branch_a → branch_b
    Then merges branch_a into main (with follow-up commit), so branch_a is merged.
    """
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # Commit on branch_a with trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    # Commit on branch_b with trailer
    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    # Fast-forward main to branch_a (simulates merge of branch_a)
    temp_repo.refs[b"refs/heads/main"] = branch_a_sha

    # Add commit to main so it's ahead of branch_a
    switch_branch(temp_repo, "main")
    main_file = tmp_path / "main_after_merge.txt"
    main_file.write_text("main after merge")
    porcelain.add(temp_repo, paths=[str(main_file)])
    porcelain.commit(temp_repo, message=b"chore: post-merge commit")

    # Switch to branch_b
    switch_branch(temp_repo, "branch_b")

    return temp_repo


@pytest.fixture
def repo_with_fork(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with forked stack: main → branch_a → (branch_b, branch_c)."""
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    # Create branch_c from branch_a (fork)
    temp_repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    file_c = tmp_path / "c.txt"
    file_c.write_text("branch c content")
    porcelain.add(temp_repo, paths=[str(file_c)])
    trailers_c = Trailers(parent_branch="branch_a")
    message_c = trailers_c.apply_to("feat: branch c")
    porcelain.commit(temp_repo, message=message_c.encode())

    return temp_repo

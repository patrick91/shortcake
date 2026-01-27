"""Tests for checkout command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.checkout import (
    CheckoutError,
    _checkout,
    _create_branch_from_remote,
    _fetch_branch,
    checkout,  # noqa: F401 - imported for coverage
    co,  # noqa: F401 - imported for coverage
)


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


# Tests for _checkout with local branches


def test_checkout_local_branch_exists(repo_with_feature: Repo) -> None:
    """Test checking out an existing local branch."""
    # Switch to main first
    switch_branch(repo_with_feature, "main")

    result = _checkout(repo_with_feature, "feature", adopt=False)

    assert result.branch == "feature"
    assert result.from_remote is False
    assert result.adopted is False
    assert result.pr_number is None

    # Verify we're on feature branch (HEAD points to feature)
    assert repo_with_feature.refs.read_ref(b"HEAD") == b"ref: refs/heads/feature"


def test_checkout_local_branch_adopts_untracked(repo_with_feature: Repo) -> None:
    """Test checkout adopts untracked branches by default."""
    # Switch to main first
    switch_branch(repo_with_feature, "main")

    result = _checkout(repo_with_feature, "feature", adopt=True)

    assert result.branch == "feature"
    assert result.adopted is True

    # Verify trailer was added
    head = git.get_branch_head(repo_with_feature, "feature")
    message = git.get_commit_message(repo_with_feature, head)
    trailers = Trailers.from_message(message)
    assert trailers.parent_branch == "main"


def test_checkout_local_branch_already_tracked(
    repo_with_tracked_feature: Repo,
) -> None:
    """Test checkout doesn't re-adopt already tracked branches."""
    # Switch to main first
    switch_branch(repo_with_tracked_feature, "main")

    result = _checkout(repo_with_tracked_feature, "feature", adopt=True)

    assert result.branch == "feature"
    assert result.adopted is False  # Already tracked, no adoption needed


def test_checkout_local_branch_no_adopt_flag(repo_with_feature: Repo) -> None:
    """Test checkout with adopt=False skips adoption."""
    # Switch to main first
    switch_branch(repo_with_feature, "main")

    result = _checkout(repo_with_feature, "feature", adopt=False)

    assert result.branch == "feature"
    assert result.adopted is False


def test_checkout_default_branch_not_adopted(temp_repo: Repo) -> None:
    """Test checkout of default branch doesn't try to adopt it."""
    # Create feature and switch to it
    temp_repo.refs[b"refs/heads/feature"] = temp_repo.head()
    switch_branch(temp_repo, "feature")

    # Checkout main - should not try to adopt
    result = _checkout(temp_repo, "main", adopt=True)

    assert result.branch == "main"
    assert result.adopted is False


def test_checkout_local_branch_adoption_fails(temp_repo: Repo) -> None:
    """Test local checkout when adoption fails (no unique commits)."""
    # Create feature branch at same commit as main (no unique commits)
    main_sha = temp_repo.head()
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    switch_branch(temp_repo, "feature")

    # Switch to main first
    switch_branch(temp_repo, "main")

    # Checkout feature - adoption should fail silently (no commits relative to main)
    result = _checkout(temp_repo, "feature", adopt=True)

    assert result.branch == "feature"
    assert result.adopted is False  # Can't adopt, no unique commits


def test_checkout_local_branch_no_default_branch(tmp_path: Path) -> None:
    """Test local checkout when no default branch is detected."""
    # Create repo without main or master
    repo = Repo.init(tmp_path, default_branch=b"develop")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create feature branch with a unique commit
    develop_sha = repo.head()
    repo.refs[b"refs/heads/feature"] = develop_sha
    switch_branch(repo, "feature")
    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature")
    porcelain.add(repo, paths=[str(test_file)])
    porcelain.commit(repo, message=b"Add feature")

    # Switch to develop
    switch_branch(repo, "develop")

    # Checkout feature - no default branch detected, so no adoption
    result = _checkout(repo, "feature", adopt=True)

    assert result.branch == "feature"
    assert result.adopted is False  # No default branch, so no adoption attempt


# Tests for _checkout with remote branches


def test_checkout_remote_branch_not_found(temp_repo: Repo) -> None:
    """Test error when branch doesn't exist locally or on remote."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Mock fetch to fail
    with (
        patch("shortcake.commands.checkout._fetch_branch", return_value=False),
        pytest.raises(CheckoutError, match="not found locally or on remote"),
    ):
        _checkout(temp_repo, "nonexistent-branch")


def test_checkout_branch_no_remote(temp_repo: Repo) -> None:
    """Test error when branch doesn't exist and no remote configured."""
    with pytest.raises(CheckoutError, match="no remote configured"):
        _checkout(temp_repo, "nonexistent-branch")


def test_checkout_from_remote_creates_local(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout creates local branch from remote."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Simulate remote ref exists
    remote_sha = temp_repo.head()
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = remote_sha

    with patch("shortcake.commands.checkout._fetch_branch", return_value=True):
        result = _checkout(temp_repo, "remote-feature", adopt=False)

    assert result.branch == "remote-feature"
    assert result.from_remote is True
    assert result.adopted is False

    # Verify local branch was created
    assert b"refs/heads/remote-feature" in temp_repo.refs


def test_checkout_from_remote_with_adoption(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout from remote with adoption enabled."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create a commit on remote branch that differs from main
    main_sha = temp_repo.head()

    # Add a file and commit to make a unique SHA for remote branch
    test_file = tmp_path / "remote.txt"
    test_file.write_text("remote content")
    porcelain.add(temp_repo, paths=[str(test_file)])
    porcelain.commit(temp_repo, message=b"Add remote feature")
    remote_sha = temp_repo.head()

    # Reset back to main
    temp_repo.refs[b"refs/heads/main"] = main_sha
    porcelain.reset(temp_repo, "hard")

    # Simulate remote ref exists with the new commit
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = remote_sha

    with patch("shortcake.commands.checkout._fetch_branch", return_value=True):
        result = _checkout(temp_repo, "remote-feature", adopt=True)

    assert result.branch == "remote-feature"
    assert result.from_remote is True
    assert result.adopted is True


def test_checkout_from_remote_adoption_fails(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout from remote when adoption fails (no commits relative to parent)."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Remote branch at same commit as main (no unique commits)
    remote_sha = temp_repo.head()
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = remote_sha

    with patch("shortcake.commands.checkout._fetch_branch", return_value=True):
        result = _checkout(temp_repo, "remote-feature", adopt=True)

    assert result.branch == "remote-feature"
    assert result.from_remote is True
    assert result.adopted is False  # Adoption fails silently


def test_checkout_from_remote_create_branch_fails(temp_repo: Repo) -> None:
    """Test error when _create_branch_from_remote fails."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Simulate remote ref exists
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = temp_repo.head()

    with (
        patch("shortcake.commands.checkout._fetch_branch", return_value=True),
        patch(
            "shortcake.commands.checkout._create_branch_from_remote", return_value=False
        ),
        pytest.raises(CheckoutError, match="Failed to create local branch"),
    ):
        _checkout(temp_repo, "remote-feature")


def test_checkout_from_remote_no_default_branch(tmp_path: Path) -> None:
    """Test remote checkout when no default branch is detected."""
    # Create repo without main or master
    repo = Repo.init(tmp_path, default_branch=b"develop")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Set up origin remote
    config = repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Create remote ref for feature (different commit from develop)
    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature")
    porcelain.add(repo, paths=[str(test_file)])
    porcelain.commit(repo, message=b"Add feature")
    feature_sha = repo.head()

    # Reset to develop
    develop_sha = repo.refs[b"refs/heads/develop"]
    repo.refs[b"refs/heads/develop"] = develop_sha
    porcelain.reset(repo, "hard")

    # Simulate remote ref
    repo.refs[b"refs/remotes/origin/remote-feature"] = feature_sha

    with patch("shortcake.commands.checkout._fetch_branch", return_value=True):
        result = _checkout(repo, "remote-feature", adopt=True)

    # No default branch detected, so no adoption attempt
    assert result.branch == "remote-feature"
    assert result.from_remote is True
    assert result.adopted is False


# Tests for _checkout with PR numbers


@respx.mock
def test_checkout_by_pr_number(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test checkout by PR number resolves branch name."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Set up GitHub token
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Create the branch locally
    temp_repo.refs[b"refs/heads/pr-feature"] = temp_repo.head()

    # Mock GitHub API
    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 42,
                "html_url": "https://github.com/owner/repo/pull/42",
                "base": {"ref": "main"},
                "head": {"ref": "pr-feature"},
                "title": "Test PR",
                "body": "",
                "state": "open",
                "draft": False,
            },
        )
    )

    result = _checkout(temp_repo, "42", adopt=False)

    assert result.branch == "pr-feature"
    assert result.pr_number == 42


@respx.mock
def test_checkout_by_pr_number_not_found(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when PR number doesn't exist."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    respx.get("https://api.github.com/repos/owner/repo/pulls/999").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(CheckoutError, match="PR #999 not found"):
        _checkout(temp_repo, "999")


def test_checkout_by_pr_number_no_token(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when checking out by PR number without GitHub token."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Mock gh auth to return nothing
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with (
        patch.object(Path, "home", return_value=Path("/nonexistent")),
        patch("subprocess.run", return_value=mock_result),
        pytest.raises(CheckoutError, match="without GitHub token"),
    ):
        _checkout(temp_repo, "42")


def test_checkout_by_pr_number_no_remote(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when checking out by PR number without origin remote."""
    monkeypatch.setenv("GH_TOKEN", "test-token")

    with pytest.raises(CheckoutError, match="Cannot determine GitHub repo"):
        _checkout(temp_repo, "42")


@respx.mock
def test_checkout_by_pr_number_no_head_ref(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when PR has no head branch (fork)."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 42,
                "html_url": "https://github.com/owner/repo/pull/42",
                "base": {"ref": "main"},
                "head": {"ref": None},  # No head ref (fork)
                "title": "Test PR",
                "body": "",
                "state": "open",
                "draft": False,
            },
        )
    )

    with pytest.raises(CheckoutError, match="no head branch"):
        _checkout(temp_repo, "42")


@respx.mock
def test_checkout_by_pr_number_api_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error handling for GitHub API errors."""
    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )

    with pytest.raises(CheckoutError, match="GitHub API error: 500"):
        _checkout(temp_repo, "42")


# Tests for helper functions


def test_fetch_branch_success(temp_repo: Repo) -> None:
    """Test _fetch_branch returns True on success."""
    # Mock at the transport level to let fetch() run but not actually connect
    with patch("shortcake.commands.checkout.porcelain.fetch") as mock_fetch:
        mock_fetch.return_value = {}
        result = _fetch_branch(temp_repo, "feature")

    assert result is True
    mock_fetch.assert_called_once()


def test_fetch_branch_failure(temp_repo: Repo) -> None:
    """Test _fetch_branch returns False on failure."""
    with patch(
        "shortcake.commands.checkout.porcelain.fetch",
        side_effect=Exception("fetch failed"),
    ):
        result = _fetch_branch(temp_repo, "feature")

    assert result is False


def test_create_branch_from_remote_success(temp_repo: Repo) -> None:
    """Test _create_branch_from_remote creates local branch."""
    # Create remote tracking ref
    temp_repo.refs[b"refs/remotes/origin/feature"] = temp_repo.head()

    result = _create_branch_from_remote(temp_repo, "feature")

    assert result is True
    assert b"refs/heads/feature" in temp_repo.refs


def test_create_branch_from_remote_no_remote_ref(temp_repo: Repo) -> None:
    """Test _create_branch_from_remote returns False when no remote ref."""
    result = _create_branch_from_remote(temp_repo, "nonexistent")

    assert result is False


# CLI tests

runner = CliRunner()


def test_checkout_cli_local_branch(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test checkout CLI with local branch."""
    import os

    switch_branch(repo_with_feature, "main")

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "feature", "--no-adopt"])

    assert result.exit_code == 0
    assert "Switched to 'feature'" in result.output


def test_checkout_cli_with_adoption(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test checkout CLI adopts untracked branch."""
    import os

    switch_branch(repo_with_feature, "main")

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "feature"])

    assert result.exit_code == 0
    assert "Switched to 'feature'" in result.output
    assert "Adopted 'feature' for stack tracking" in result.output


def test_checkout_cli_error(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout CLI error handling."""
    import os

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "nonexistent"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_checkout_cli_uncommitted_changes(
    repo_with_feature: Repo, tmp_path: Path
) -> None:
    """Test checkout CLI warns about uncommitted changes."""
    import os

    switch_branch(repo_with_feature, "main")

    # Create uncommitted changes
    (tmp_path / "uncommitted.txt").write_text("uncommitted")
    porcelain.add(repo_with_feature, paths=[str(tmp_path / "uncommitted.txt")])

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "feature", "--no-adopt"])

    assert result.exit_code == 0
    assert "Warning: You have uncommitted changes." in result.output


def test_co_alias(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test co alias works same as checkout."""
    import os

    switch_branch(repo_with_feature, "main")

    os.chdir(tmp_path)
    result = runner.invoke(app, ["co", "feature", "--no-adopt"])

    assert result.exit_code == 0
    assert "Switched to 'feature'" in result.output


@respx.mock
def test_checkout_cli_pr_output(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test checkout CLI output for PR checkout."""
    import os

    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Create the branch locally
    temp_repo.refs[b"refs/heads/pr-feature"] = temp_repo.head()

    respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 42,
                "html_url": "https://github.com/owner/repo/pull/42",
                "base": {"ref": "main"},
                "head": {"ref": "pr-feature"},
                "title": "Test PR",
                "body": "",
                "state": "open",
                "draft": False,
            },
        )
    )

    os.chdir(tmp_path)
    result = runner.invoke(app, ["checkout", "42", "--no-adopt"])

    assert result.exit_code == 0
    assert "Checked out PR #42 (pr-feature)" in result.output


def test_checkout_cli_remote_output(temp_repo: Repo, tmp_path: Path) -> None:
    """Test checkout CLI output for remote checkout."""
    import os

    # Set up origin remote
    config = temp_repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

    # Simulate remote ref exists
    remote_sha = temp_repo.head()
    temp_repo.refs[b"refs/remotes/origin/remote-feature"] = remote_sha

    os.chdir(tmp_path)
    with patch("shortcake.commands.checkout._fetch_branch", return_value=True):
        result = runner.invoke(app, ["checkout", "remote-feature", "--no-adopt"])

    assert result.exit_code == 0
    assert "Checked out 'remote-feature' from remote" in result.output

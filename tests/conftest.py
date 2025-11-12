"""Shared fixtures for all tests."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import respx

from tests.helpers.git_helpers import (
    create_bare_repo,
    create_branch,
    create_commit,
    setup_remote,
)
from tests.helpers.github_helpers import GitHubMocker

type GitEditorScript = Callable[[str], None]


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated config directory."""
    config_home = tmp_path / "config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return config_home


@pytest.fixture
def isolated_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    readme = repo_path / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


@pytest.fixture
def git_editor_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitEditorScript:
    """Create a git editor script that writes predetermined commit messages."""
    script_path = tmp_path / "fake_editor.sh"

    def create_editor(commit_message: str) -> None:
        """Create a script that writes the given commit message."""
        script_content = f"""#!/bin/sh
echo "{commit_message}" > "$1"
"""
        script_path.write_text(script_content)
        script_path.chmod(0o755)
        monkeypatch.setenv("GIT_EDITOR", str(script_path))

    return create_editor


@pytest.fixture
def remote_repo(tmp_path: Path) -> Path:
    """Create a bare git repository simulating a remote.

    This can be used to test push/pull operations and GitHub integration.
    """
    remote_path = tmp_path / "remote_repo.git"
    create_bare_repo(remote_path)
    return remote_path


@pytest.fixture
def repo_with_remote(
    isolated_git_repo: Path,
    remote_repo: Path,
) -> tuple[Path, Path]:
    """Create a local repository with a configured remote.

    Returns:
        Tuple of (local_repo_path, remote_repo_path)
    """
    setup_remote(isolated_git_repo, remote_repo, "origin")
    # Push main branch to remote
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )
    return isolated_git_repo, remote_repo


@pytest.fixture
def github_api_mock(respx_mock: respx.MockRouter) -> GitHubMocker:
    """Provide a GitHub API mocker for testing.

    This fixture provides a convenient interface to mock GitHub API responses
    using the respx library.
    """
    return GitHubMocker(respx_mock, owner="testuser", repo="testrepo")


@pytest.fixture
def multi_branch_stack(isolated_git_repo: Path) -> Callable[[int, str], list[str]]:
    """Factory fixture for creating N-branch stacks.

    Returns a function that creates a linear stack of N branches with commits.

    Example:
        branches = multi_branch_stack(3, "feature")
        # Creates: main -> feature-1 -> feature-2 -> feature-3
    """

    def create_stack(num_branches: int, prefix: str = "branch") -> list[str]:
        """Create a stack of branches.

        Args:
            num_branches: Number of branches to create
            prefix: Prefix for branch names

        Returns:
            List of branch names created (in order from base to tip)
        """
        branch_names = []

        for i in range(1, num_branches + 1):
            branch_name = f"{prefix}-{i}"
            create_branch(isolated_git_repo, branch_name)
            create_commit(
                isolated_git_repo,
                f"Commit for {branch_name}",
                {f"file_{i}.txt": f"Content for {branch_name}\n"},
            )
            branch_names.append(branch_name)

        return branch_names

    return create_stack


@pytest.fixture
def pr_metadata_store() -> dict[str, Any]:
    """Provide a store for tracking PR metadata in tests.

    This can be used to track PR numbers, URLs, and relationships
    between PRs in a stack during testing.

    Example:
        pr_metadata_store["branch-1"] = {"pr_number": 123, "parent": "main"}
    """
    return {}


@pytest.fixture
def mock_github_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Mock a GitHub token in the environment."""
    token = "ghp_test_token_1234567890"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    return token


@pytest.fixture
def sample_pr_response() -> dict[str, Any]:
    """Provide a sample GitHub PR API response for testing."""
    return {
        "number": 123,
        "title": "Test PR",
        "body": "Test PR body",
        "state": "open",
        "head": {
            "ref": "feature-branch",
            "sha": "a" * 40,
        },
        "base": {
            "ref": "main",
            "sha": "b" * 40,
        },
        "html_url": "https://github.com/testuser/testrepo/pull/123",
        "mergeable": True,
    }


@pytest.fixture
def sample_branch_response() -> dict[str, Any]:
    """Provide a sample GitHub branch API response for testing."""
    return {
        "name": "main",
        "commit": {
            "sha": "c" * 40,
            "url": "https://api.github.com/repos/testuser/testrepo/commits/" + "c" * 40,
        },
    }


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (fast, isolated)")
    config.addinivalue_line("markers", "integration: Integration tests (multiple components)")
    config.addinivalue_line("markers", "e2e: End-to-end tests (full workflows)")
    config.addinivalue_line("markers", "github: Tests requiring GitHub API mocking")
    config.addinivalue_line("markers", "slow: Tests that take longer to run")

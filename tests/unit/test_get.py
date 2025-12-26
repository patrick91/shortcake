"""Tests for the get command."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from httpx import Response
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo
from shortcake.github import PullRequest
from tests.helpers.git_helpers import (
    checkout_branch,
    create_branch,
    create_commit,
    get_metadata,
    push_branch,
)

runner = CliRunner()


def test_get_help():
    result = runner.invoke(app, ["get", "--help"])
    assert result.exit_code == 0
    assert "Fetch a branch and its stack from remote" in result.stdout


def test_get_single_branch_from_remote(repo_with_remote: tuple[Path, Path], isolated_config: Path):
    local_repo, remote_repo = repo_with_remote
    git = GitRepo(local_repo)

    # Create a branch on remote by pushing from local
    create_branch(local_repo, "feature-1")
    create_commit(local_repo, "Feature 1 commit", {"feature1.txt": "content1"})
    push_branch(local_repo, "feature-1")

    # Delete local branch to simulate fetching someone else's branch
    checkout_branch(local_repo, "main")
    git.delete_branch("feature-1")

    # Now get the branch
    result = runner.invoke(app, ["get", "feature-1"])
    assert result.exit_code == 0
    assert "feature-1" in result.stdout
    assert "Successfully fetched" in result.stdout

    # Verify branch was created locally
    assert git.branch_exists("feature-1")

    # Verify metadata was set
    metadata = get_metadata(local_repo, "feature-1")
    assert metadata.get("parent") == "main"


def test_get_stack_of_branches(repo_with_remote: tuple[Path, Path], isolated_config: Path):
    local_repo, remote_repo = repo_with_remote
    git = GitRepo(local_repo)

    # Create a stack: main -> feature-1 -> feature-2 -> feature-3
    create_branch(local_repo, "feature-1")
    create_commit(local_repo, "Feature 1", {"f1.txt": "1"})
    push_branch(local_repo, "feature-1")

    create_branch(local_repo, "feature-2")
    create_commit(local_repo, "Feature 2", {"f2.txt": "2"})
    push_branch(local_repo, "feature-2")

    create_branch(local_repo, "feature-3")
    create_commit(local_repo, "Feature 3", {"f3.txt": "3"})
    push_branch(local_repo, "feature-3")

    # Delete local branches
    checkout_branch(local_repo, "main")
    git.delete_branch("feature-3")
    git.delete_branch("feature-2")
    git.delete_branch("feature-1")

    # Get the top branch - should fetch entire stack
    result = runner.invoke(app, ["get", "feature-3"])
    assert result.exit_code == 0
    assert "Found 3 branch(es)" in result.stdout

    # Verify all branches exist locally
    assert git.branch_exists("feature-1")
    assert git.branch_exists("feature-2")
    assert git.branch_exists("feature-3")

    # Verify parent chain
    assert get_metadata(local_repo, "feature-1").get("parent") == "main"
    assert get_metadata(local_repo, "feature-2").get("parent") == "feature-1"
    assert get_metadata(local_repo, "feature-3").get("parent") == "feature-2"


def test_get_branch_already_exists_and_up_to_date(
    repo_with_remote: tuple[Path, Path], isolated_config: Path
):
    local_repo, _ = repo_with_remote

    # Create and push a branch
    create_branch(local_repo, "feature-1")
    create_commit(local_repo, "Feature 1", {"f1.txt": "1"})
    push_branch(local_repo, "feature-1")

    # Get the branch (it already exists locally and is up to date)
    result = runner.invoke(app, ["get", "feature-1"])
    assert result.exit_code == 0
    assert "feature-1" in result.stdout


def test_get_branch_not_on_remote(repo_with_remote: tuple[Path, Path], isolated_config: Path):
    local_repo, _ = repo_with_remote

    result = runner.invoke(app, ["get", "nonexistent-branch"])
    assert result.exit_code == 1
    assert "not found on remote" in result.output


def test_get_no_remote_configured(isolated_git_repo: Path, isolated_config: Path):
    result = runner.invoke(app, ["get", "some-branch"])
    assert result.exit_code == 1
    assert "No remote 'origin' configured" in result.output


def test_get_by_pr_number(
    repo_with_remote: tuple[Path, Path],
    isolated_config: Path,
    mock_github_token: str,
    build_pr_response,
):
    import subprocess
    from unittest.mock import patch

    import respx

    local_repo, remote_repo = repo_with_remote
    git = GitRepo(local_repo)

    # Create and push a branch
    create_branch(local_repo, "feature-from-pr")
    create_commit(local_repo, "PR Feature", {"pr.txt": "content"})
    push_branch(local_repo, "feature-from-pr")

    # Delete local branch
    checkout_branch(local_repo, "main")
    git.delete_branch("feature-from-pr")

    # Set up a fetch URL that looks like GitHub (for API parsing)
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=local_repo,
        check=True,
    )

    # Mock GitHub API and git fetch
    with respx.mock:
        respx.get("https://api.github.com/repos/testuser/testrepo/pulls/42").mock(
            return_value=Response(
                200,
                json=build_pr_response(
                    number=42,
                    title="My PR",
                    head_ref="feature-from-pr",
                    base_ref="main",
                ),
            )
        )

        # Mock fetch to use the local bare repo
        def mock_fetch(self: GitRepo, remote_name: str = "origin") -> None:
            # Temporarily restore local URL for fetch
            subprocess.run(
                ["git", "remote", "set-url", "origin", str(remote_repo)],
                cwd=local_repo,
                check=True,
            )
            # Do the actual fetch with local URL
            remote = self.repo.remote(remote_name)
            remote.fetch()
            # Restore GitHub URL
            subprocess.run(
                ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
                cwd=local_repo,
                check=True,
            )

        with patch.object(GitRepo, "fetch", mock_fetch):
            result = runner.invoke(app, ["get", "42"])

    assert result.exit_code == 0
    assert "Resolving PR #42" in result.stdout
    assert "feature-from-pr" in result.stdout
    assert git.branch_exists("feature-from-pr")


def test_get_force_overwrites_local_changes(
    repo_with_remote: tuple[Path, Path], isolated_config: Path
):
    local_repo, _ = repo_with_remote
    git = GitRepo(local_repo)

    # Create and push a branch
    create_branch(local_repo, "feature-1")
    create_commit(local_repo, "Remote commit", {"remote.txt": "remote"})
    push_branch(local_repo, "feature-1")

    # Reset local branch to before the commit and make a different commit
    # This simulates someone else pushing different changes
    git.repo.git.reset("--hard", "HEAD~1")
    create_commit(local_repo, "Local divergent commit", {"local.txt": "local"})

    # Now local feature-1 has diverged from origin/feature-1

    # Without --force, should fail
    result = runner.invoke(app, ["get", "feature-1"])
    assert result.exit_code == 1
    assert "would be overwritten" in result.output

    # With --force, should succeed
    result = runner.invoke(app, ["get", "feature-1", "--force"])
    assert result.exit_code == 0


def test_get_switches_to_target_branch(repo_with_remote: tuple[Path, Path], isolated_config: Path):
    local_repo, _ = repo_with_remote
    git = GitRepo(local_repo)

    # Create and push a branch
    create_branch(local_repo, "feature-1")
    create_commit(local_repo, "Feature 1", {"f1.txt": "1"})
    push_branch(local_repo, "feature-1")

    # Delete local branch and stay on main
    checkout_branch(local_repo, "main")
    git.delete_branch("feature-1")

    assert git.get_current_branch() == "main"

    # Get the branch
    result = runner.invoke(app, ["get", "feature-1"])
    assert result.exit_code == 0

    # Should have switched to the target branch
    assert git.get_current_branch() == "feature-1"


# Interactive mode tests


def test_get_interactive_no_prs(
    repo_with_remote: tuple[Path, Path],
    isolated_config: Path,
    mock_github_token: str,
):
    import respx

    local_repo, remote_repo = repo_with_remote

    # Set up GitHub URL
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=local_repo,
        check=True,
    )

    with respx.mock:
        # Mock empty PR list
        respx.get("https://api.github.com/repos/testuser/testrepo/pulls").mock(
            return_value=Response(200, json=[])
        )

        result = runner.invoke(app, ["get"])

    assert result.exit_code == 0
    assert "No open PRs found" in result.stdout


def test_get_interactive_with_mine_no_prs(
    repo_with_remote: tuple[Path, Path],
    isolated_config: Path,
    mock_github_token: str,
):
    import respx

    local_repo, remote_repo = repo_with_remote

    # Set up GitHub URL
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=local_repo,
        check=True,
    )

    with respx.mock:
        # Mock PR list with PRs from other users
        respx.get("https://api.github.com/repos/testuser/testrepo/pulls").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "Someone else's PR",
                        "body": "",
                        "html_url": "https://github.com/testuser/testrepo/pull/1",
                        "head": {"ref": "other-feature", "sha": "a" * 40},
                        "base": {"ref": "main", "sha": "b" * 40},
                        "state": "open",
                        "user": {"login": "otheruser"},
                    }
                ],
            )
        )
        # Mock current user
        respx.get("https://api.github.com/user").mock(
            return_value=Response(200, json={"login": "testuser"})
        )

        result = runner.invoke(app, ["get", "--mine"])

    assert result.exit_code == 0
    assert "No open PRs found authored by you" in result.stdout


def test_get_interactive_selects_pr(
    repo_with_remote: tuple[Path, Path],
    isolated_config: Path,
    mock_github_token: str,
):
    import respx

    local_repo, remote_repo = repo_with_remote
    git = GitRepo(local_repo)

    # Create and push a branch
    create_branch(local_repo, "feature-interactive")
    create_commit(local_repo, "Interactive feature", {"int.txt": "content"})
    push_branch(local_repo, "feature-interactive")

    # Delete local branch
    checkout_branch(local_repo, "main")
    git.delete_branch("feature-interactive")

    # Set up GitHub URL for fetch, local for push
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=local_repo,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "set-url", "--push", "origin", str(remote_repo)],
        cwd=local_repo,
        check=True,
    )

    # Create a mock PR to be selected
    mock_pr = PullRequest(
        number=99,
        title="Interactive PR",
        body="",
        html_url="https://github.com/testuser/testrepo/pull/99",
        head_ref="feature-interactive",
        base_ref="main",
        state="open",
        author="testuser",
    )

    with respx.mock:
        # Mock PR list
        respx.get("https://api.github.com/repos/testuser/testrepo/pulls").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "number": 99,
                        "title": "Interactive PR",
                        "body": "",
                        "html_url": "https://github.com/testuser/testrepo/pull/99",
                        "head": {"ref": "feature-interactive", "sha": "a" * 40},
                        "base": {"ref": "main", "sha": "b" * 40},
                        "state": "open",
                        "user": {"login": "testuser"},
                    }
                ],
            )
        )

        # Mock the interactive picker to return our PR
        with patch.object(
            GitRepo,
            "fetch",
            lambda self, remote: subprocess.run(
                ["git", "remote", "set-url", "origin", str(remote_repo)],
                cwd=local_repo,
                check=True,
            )
            or git.repo.remote("origin").fetch()
            or subprocess.run(
                ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
                cwd=local_repo,
                check=True,
            ),
        ):
            with patch("shortcake.commands.get._pick_pr_interactive", return_value=mock_pr):
                result = runner.invoke(app, ["get"])

    assert result.exit_code == 0
    assert "Selected: #99" in result.stdout
    assert git.branch_exists("feature-interactive")

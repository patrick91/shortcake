"""Integration tests for submit workflows with stacked branches."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.git import GitRepo

type GitEditorScript = Callable[[str], None]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def stage_all(repo_path: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)


def get_remote_sha(repo_path: Path, remote: str, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "ls-remote", remote, ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        return result.stdout.strip().split()[0]
    return None


@pytest.mark.integration
def test_submit_default_pushes_all_branches(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    remote_repo: Path,
    git_editor_script: GitEditorScript,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that submit (default) pushes all branches in the stack.

    By default, submit pushes all parent branches to ensure GitHub shows
    correct diffs for stacked PRs.
    """
    git = GitRepo(isolated_git_repo)

    # Set up remote (local bare repo)
    git.add_remote("origin", str(remote_repo))
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Create second branch stacked on first
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Neither branch should be on remote yet
    assert get_remote_sha(remote_repo, ".", "refs/heads/add-feature-1") is None
    assert get_remote_sha(remote_repo, ".", "refs/heads/add-feature-2") is None

    # Set up mock for GitHub API
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    # Change remote URL to GitHub format for API parsing, but keep push URL local
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=isolated_git_repo,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "set-url", "--push", "origin", str(remote_repo)],
        cwd=isolated_git_repo,
        check=True,
    )

    # Dry run should show both branches (default behavior)
    result = runner.invoke(app, ["submit", "--dry-run"])
    assert result.exit_code == 0
    assert "add-feature-1" in result.output
    assert "add-feature-2" in result.output


@pytest.mark.integration
def test_submit_current_only_submits_single_branch(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    remote_repo: Path,
    git_editor_script: GitEditorScript,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test submit --current only submits the current branch.

    Use --current when you've already pushed parent branches and only
    want to update the current branch.
    """
    git = GitRepo(isolated_git_repo)

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Create second branch
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Set mock token
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    # Set up GitHub remote URL for parsing
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=isolated_git_repo,
        check=True,
    )

    # Dry run with --current should only show current branch
    result = runner.invoke(app, ["submit", "--current", "--dry-run"])
    assert result.exit_code == 0
    assert "add-feature-2" in result.output
    # Parent branch should NOT be in the output for --current submit
    assert "add-feature-1 →" not in result.output


@pytest.mark.integration
def test_submit_dry_run_shows_stack_order(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    remote_repo: Path,
    git_editor_script: GitEditorScript,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that submit --dry-run shows branches in correct order.

    Branches should be listed from bottom of stack (closest to main) to top.
    """
    git = GitRepo(isolated_git_repo)

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Create three stacked branches
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    runner.invoke(app, ["create"])

    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    runner.invoke(app, ["create"])

    (isolated_git_repo / "feature3.txt").write_text("feature 3")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 3")
    runner.invoke(app, ["create"])

    # Set mock token
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    # Set up GitHub remote URL for parsing
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=isolated_git_repo,
        check=True,
    )

    result = runner.invoke(app, ["submit", "--dry-run"])
    assert result.exit_code == 0

    # All three branches should be listed (default behavior)
    assert "add-feature-1" in result.output
    assert "add-feature-2" in result.output
    assert "add-feature-3" in result.output

    # Check order: feature-1 should appear before feature-2, which should appear before feature-3
    pos1 = result.output.find("add-feature-1")
    pos2 = result.output.find("add-feature-2")
    pos3 = result.output.find("add-feature-3")
    assert pos1 < pos2 < pos3, "Branches should be in stack order (bottom to top)"


@pytest.mark.integration
def test_submit_stack_includes_children(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    remote_repo: Path,
    git_editor_script: GitEditorScript,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that submit --stack includes child branches.

    When using --stack, all branches in the stack should be submitted,
    including children of the current branch.
    """
    git = GitRepo(isolated_git_repo)

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Create three stacked branches
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    runner.invoke(app, ["create"])

    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    runner.invoke(app, ["create"])

    (isolated_git_repo / "feature3.txt").write_text("feature 3")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 3")
    runner.invoke(app, ["create"])

    # Go back to feature-2 (middle of stack)
    git.checkout_branch("add-feature-2")

    # Set mock token
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    # Set up GitHub remote URL for parsing
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=isolated_git_repo,
        check=True,
    )

    # Default submit should NOT include feature-3 (child)
    result = runner.invoke(app, ["submit", "--dry-run"])
    assert result.exit_code == 0
    assert "add-feature-1" in result.output
    assert "add-feature-2" in result.output
    assert "add-feature-3" not in result.output

    # Submit --stack should include ALL branches
    result = runner.invoke(app, ["submit", "--stack", "--dry-run"])
    assert result.exit_code == 0
    assert "add-feature-1" in result.output
    assert "add-feature-2" in result.output
    assert "add-feature-3" in result.output


def get_commit_sha(repo_path: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_commits_between(repo_path: Path, base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--oneline", f"{base}..{head}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


@pytest.mark.integration
def test_submit_automatically_restacks_after_amend(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    remote_repo: Path,
    git_editor_script: GitEditorScript,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that submit automatically restacks branches when parent was amended."""
    import respx
    from httpx import Response

    git = GitRepo(isolated_git_repo)

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    feature1_original_sha = get_commit_sha(isolated_git_repo, "HEAD")

    # Create second branch stacked on first
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Go back to first branch and amend the commit
    git.checkout_branch("add-feature-1")
    (isolated_git_repo / "feature1.txt").write_text("feature 1 amended")
    stage_all(isolated_git_repo)
    # Use shortcake modify to properly preserve notes
    result = runner.invoke(app, ["modify"])
    assert result.exit_code == 0

    feature1_new_sha = get_commit_sha(isolated_git_repo, "HEAD")
    assert feature1_new_sha != feature1_original_sha

    # Before submit, feature-2 should have 2 commits (old feature-1 + feature-2)
    commits_before = get_commits_between(isolated_git_repo, "add-feature-1", "add-feature-2")
    assert len(commits_before) == 2  # Old parent commit + feature-2 commit

    # Set up GitHub mock
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=isolated_git_repo,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "set-url", "--push", "origin", str(remote_repo)],
        cwd=isolated_git_repo,
        check=True,
    )

    # Mock GitHub API
    with respx.mock:
        # Mock PR lookup (no existing PRs)
        respx.get("https://api.github.com/repos/testuser/testrepo/pulls").mock(
            return_value=Response(200, json=[])
        )

        # Counter for PR numbers
        pr_counter = [0]

        def create_pr_response(request):
            pr_counter[0] += 1
            return Response(
                201,
                json={
                    "number": pr_counter[0],
                    "title": "Test",
                    "body": "",
                    "html_url": f"https://github.com/testuser/testrepo/pull/{pr_counter[0]}",
                    "head": {"ref": "add-feature-1", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                    "state": "open",
                },
            )

        # Mock PR creation
        respx.post("https://api.github.com/repos/testuser/testrepo/pulls").mock(
            side_effect=create_pr_response
        )

        # Mock GET for individual PRs (for updating body with stack info)
        respx.get(url__regex=r".*/repos/testuser/testrepo/pulls/\d+$").mock(
            return_value=Response(
                200,
                json={
                    "number": 1,
                    "title": "Test",
                    "body": "",
                    "html_url": "https://github.com/testuser/testrepo/pull/1",
                    "head": {"ref": "add-feature-1", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                    "state": "open",
                },
            )
        )

        # Mock PATCH for updating PR body
        respx.patch(url__regex=r".*/repos/testuser/testrepo/pulls/\d+$").mock(
            return_value=Response(
                200,
                json={
                    "number": 1,
                    "title": "Test",
                    "body": "## Stack\n...",
                    "html_url": "https://github.com/testuser/testrepo/pull/1",
                    "head": {"ref": "add-feature-1", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                    "state": "open",
                },
            )
        )

        # Submit with --stack should automatically restack feature-2
        result = runner.invoke(app, ["submit", "--stack"])
        assert result.exit_code == 0
        assert "Restacking add-feature-2" in result.output

    # After submit, feature-2 should have only 1 commit on top of feature-1
    commits_after = get_commits_between(isolated_git_repo, "add-feature-1", "add-feature-2")
    assert len(commits_after) == 1  # Only feature-2 commit


@pytest.mark.integration
def test_submit_updates_pr_body_with_stack_info(
    runner: CliRunner,
    isolated_git_repo: Path,
    isolated_config: Path,
    remote_repo: Path,
    git_editor_script: GitEditorScript,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that submit updates PR bodies with stack information."""
    import respx
    from httpx import Response

    git = GitRepo(isolated_git_repo)

    # Set up remote
    git.add_remote("origin", str(remote_repo))
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=isolated_git_repo,
        check=True,
        capture_output=True,
    )

    # Create first branch
    (isolated_git_repo / "feature1.txt").write_text("feature 1")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 1")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Create second branch stacked on first
    (isolated_git_repo / "feature2.txt").write_text("feature 2")
    stage_all(isolated_git_repo)
    git_editor_script("Add feature 2")
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0

    # Set up GitHub mock
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:testuser/testrepo.git"],
        cwd=isolated_git_repo,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "set-url", "--push", "origin", str(remote_repo)],
        cwd=isolated_git_repo,
        check=True,
    )

    # Track what bodies are sent to the API
    updated_bodies: list[str] = []

    # Mock GitHub API
    with respx.mock:
        # Mock PR lookup (no existing PRs)
        respx.get("https://api.github.com/repos/testuser/testrepo/pulls").mock(
            return_value=Response(200, json=[])
        )

        # Counter for PR numbers
        pr_counter = [0]

        def create_pr_response(request):
            pr_counter[0] += 1
            return Response(
                201,
                json={
                    "number": pr_counter[0],
                    "title": "Test",
                    "body": "",
                    "html_url": f"https://github.com/testuser/testrepo/pull/{pr_counter[0]}",
                    "head": {"ref": f"add-feature-{pr_counter[0]}", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                    "state": "open",
                },
            )

        # Mock PR creation
        respx.post("https://api.github.com/repos/testuser/testrepo/pulls").mock(
            side_effect=create_pr_response
        )

        # Mock GET for individual PRs
        respx.get(url__regex=r".*/repos/testuser/testrepo/pulls/\d+$").mock(
            return_value=Response(
                200,
                json={
                    "number": 1,
                    "title": "Test",
                    "body": "",
                    "html_url": "https://github.com/testuser/testrepo/pull/1",
                    "head": {"ref": "add-feature-1", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                    "state": "open",
                },
            )
        )

        # Mock PATCH for updating PR body - capture the body
        def update_pr_response(request):
            import json

            body_data = json.loads(request.content)
            if "body" in body_data:
                updated_bodies.append(body_data["body"])
            return Response(
                200,
                json={
                    "number": 1,
                    "title": "Test",
                    "body": body_data.get("body", ""),
                    "html_url": "https://github.com/testuser/testrepo/pull/1",
                    "head": {"ref": "add-feature-1", "sha": "abc123"},
                    "base": {"ref": "main", "sha": "def456"},
                    "state": "open",
                },
            )

        respx.patch(url__regex=r".*/repos/testuser/testrepo/pulls/\d+$").mock(
            side_effect=update_pr_response
        )

        # Submit the stack
        result = runner.invoke(app, ["submit", "--stack"])
        assert result.exit_code == 0
        assert "Updating PR descriptions with stack info" in result.output

    # Verify stack info was added to PR bodies
    assert len(updated_bodies) == 2  # Both PRs should be updated

    # Check that the stack info contains expected content
    for body in updated_bodies:
        assert "## Stack" in body
        assert "#1" in body or "#2" in body  # PR numbers
        assert "main" in body  # Base branch

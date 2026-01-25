"""Tests for submit command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake._github import GitHubClient, PRInfo
from shortcake.cli import app
from shortcake.commands.submit import (
    STACK_END_MARKER,
    STACK_START_MARKER,
    PRAction,
    SubmitError,
    _build_stack_section,
    _get_commit_title,
    _submit,
    _update_pr_body_with_stack,
)

runner = CliRunner()


# Helper to set up origin remote
def setup_origin_remote(repo: Repo, url: str = "git@github.com:owner/repo.git") -> None:
    """Configure origin remote for a repo."""
    config = repo.get_config()
    config.set((b"remote", b"origin"), b"url", url.encode())
    config.write_to_path()


# Tests for helper functions


def test_get_commit_title(repo_with_tracked_feature: Repo) -> None:
    """Test getting commit title from branch."""
    title = _get_commit_title(repo_with_tracked_feature, "feature")
    assert title == "feat: add feature"


def test_build_stack_section() -> None:
    """Test building stack visualization section."""
    stack_branches = ["branch_a", "branch_b", "branch_c"]
    pr_numbers = {"branch_a": 1, "branch_b": 2, "branch_c": 3}

    section = _build_stack_section(stack_branches, "branch_b", pr_numbers, "owner")

    assert STACK_START_MARKER in section
    assert STACK_END_MARKER in section
    assert "## Stack" in section
    assert "**#2** (`branch_b`) <-- this PR" in section
    assert "#1 (`branch_a`)" in section
    assert "#3 (`branch_c`)" in section


def test_build_stack_section_missing_pr() -> None:
    """Test stack section when some branches don't have PRs."""
    stack_branches = ["branch_a", "branch_b"]
    pr_numbers = {"branch_a": 1}  # branch_b has no PR

    section = _build_stack_section(stack_branches, "branch_a", pr_numbers, "owner")

    assert "#1 (`branch_a`)" in section or "**#1** (`branch_a`)" in section
    assert "(no PR) (`branch_b`)" in section


def test_update_pr_body_with_stack_no_markers() -> None:
    """Test prepending stack section to body without markers."""
    existing_body = "Original description"
    stack_section = f"{STACK_START_MARKER}\n## Stack\n- #1\n{STACK_END_MARKER}"

    result = _update_pr_body_with_stack(existing_body, stack_section)

    assert result.startswith(STACK_START_MARKER)
    assert "Original description" in result
    assert result.index(STACK_END_MARKER) < result.index("Original description")


def test_update_pr_body_with_stack_existing_markers() -> None:
    """Test replacing existing stack section."""
    existing_body = (
        f"{STACK_START_MARKER}\nOld stack\n{STACK_END_MARKER}\n\nDescription"
    )
    stack_section = f"{STACK_START_MARKER}\n## Stack\n- #1\n{STACK_END_MARKER}"

    result = _update_pr_body_with_stack(existing_body, stack_section)

    assert "Old stack" not in result
    assert "## Stack" in result
    assert "Description" in result


def test_update_pr_body_with_stack_empty_body() -> None:
    """Test with empty existing body."""
    existing_body = ""
    stack_section = f"{STACK_START_MARKER}\n## Stack\n{STACK_END_MARKER}"

    result = _update_pr_body_with_stack(existing_body, stack_section)

    assert result == stack_section


# Tests for _submit function


def test_submit_error_detached_head(temp_repo: Repo, tmp_path: Path) -> None:
    """Test submit fails in detached HEAD state."""
    # Detach HEAD by removing the symbolic ref
    head_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs.remove_if_equals(b"HEAD", temp_repo.refs.read_ref(b"HEAD"))
    temp_repo.refs.add_if_new(b"HEAD", head_sha)

    with pytest.raises(SubmitError, match="detached HEAD"):
        _submit(temp_repo)


def test_submit_error_uncommitted_changes(
    repo_with_tracked_feature: Repo, tmp_path: Path
) -> None:
    """Test submit fails with uncommitted changes."""
    # Create uncommitted changes
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    porcelain.add(repo_with_tracked_feature, paths=[str(test_file)])

    with pytest.raises(SubmitError, match="uncommitted changes"):
        _submit(repo_with_tracked_feature)


def test_submit_error_no_remote(repo_with_tracked_feature: Repo) -> None:
    """Test submit fails without origin remote."""
    with pytest.raises(SubmitError, match="No origin remote"):
        _submit(repo_with_tracked_feature)


def test_submit_error_no_token(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit fails without GitHub token."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with (
        patch("shortcake.commands.submit.get_github_token", return_value=None),
        pytest.raises(SubmitError, match="No GitHub token"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_error_invalid_remote_url(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit fails with non-GitHub remote URL."""
    setup_origin_remote(repo_with_tracked_feature, "git@gitlab.com:owner/repo.git")
    monkeypatch.setenv("GH_TOKEN", "test-token")

    with pytest.raises(SubmitError, match="Cannot determine GitHub repo"):
        _submit(repo_with_tracked_feature)


def test_submit_error_untracked_branch(
    repo_with_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit fails when branch is not tracked."""
    setup_origin_remote(repo_with_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    with pytest.raises(SubmitError, match="not tracked"):
        _submit(repo_with_feature)


def test_submit_dry_run(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit dry run shows preview."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    result = _submit(repo_with_tracked_feature, dry_run=True)

    assert result.stack_branches == ["feature"]
    assert len(result.branch_results) == 0  # No actual results in dry run


def test_submit_creates_pr(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit creates a new PR."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_pr = PRInfo(
        number=123,
        url="https://github.com/owner/repo/pull/123",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    # First call: None (no PR exists), second call: mock_pr (after creation)
    mock_client.get_pr_for_branch.side_effect = [None, mock_pr]
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert len(result.branch_results) == 1
    assert result.branch_results[0].action == PRAction.CREATED
    assert result.branch_results[0].pr_number == 123


def test_submit_updates_existing_pr(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit updates existing PR."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body="Existing body",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert len(result.branch_results) == 1
    assert result.branch_results[0].action == PRAction.UPDATED


def test_submit_updates_pr_base(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit updates PR base when parent changed."""
    setup_origin_remote(repo_with_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # PR for branch_b exists but has wrong base
    mock_pr_b = PRInfo(
        number=789,
        url="https://github.com/owner/repo/pull/789",
        base="main",  # Wrong base - should be branch_a
        title="feat: branch b",
        body="",
        state="open",
        is_draft=False,
    )
    mock_pr_a = PRInfo(
        number=788,
        url="https://github.com/owner/repo/pull/788",
        base="main",
        title="feat: branch a",
        body="",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = lambda b: (
        mock_pr_a if b == "branch_a" else mock_pr_b
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_stack)

    # Verify update_pr was called to update the base
    update_calls = [c for c in mock_client.update_pr.call_args_list if c[1].get("base")]
    assert len(update_calls) >= 1


def test_submit_handles_auth_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 401 authentication error."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "invalid-token")

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Bad credentials"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        pytest.raises(SubmitError, match="authentication failed"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_handles_rate_limit(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 403 rate limit error."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "API rate limit exceeded"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        pytest.raises(SubmitError, match="rate limit"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_handles_403_non_rate_limit(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 403 error that's not rate limit."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Repository access blocked"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        pytest.raises(SubmitError, match="forbidden"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_handles_422_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 422 validation error."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.text = "Validation failed"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = httpx.HTTPStatusError(
        "422", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].error is not None
    error_msg = result.branch_results[0].error
    assert "422" in error_msg or "Validation" in error_msg


def test_submit_handles_other_http_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles other HTTP errors."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].error is not None
    assert "500" in result.branch_results[0].error


def test_submit_draft_pr(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit creates draft PR when requested."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_pr = PRInfo(
        number=123,
        url="https://github.com/owner/repo/pull/123",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=True,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = [None, mock_pr]
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature, draft=True)

    # Verify draft=True was passed to create_pr
    mock_client.create_pr.assert_called_once()
    call_kwargs = mock_client.create_pr.call_args[1]
    assert call_kwargs["draft"] is True


def test_submit_stack_body_update_error_non_fatal(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that errors updating PR body with stack are non-fatal."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_pr = PRInfo(
        number=123,
        url="https://github.com/owner/repo/pull/123",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=False,
    )

    call_count = [0]

    def mock_get_pr(branch: str) -> PRInfo:
        call_count[0] += 1
        if call_count[0] <= 1:
            # First call during PR check
            return mock_pr
        # Second call during body update - raise error
        raise httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock())

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = mock_get_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        # Should not raise - body update error is non-fatal
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].action == PRAction.UPDATED


# CLI tests


def test_cli_submit_help() -> None:
    """Test submit command help."""
    result = runner.invoke(app, ["submit", "--help"])

    assert result.exit_code == 0
    assert "Push branches and create/update GitHub PRs" in result.output


def test_cli_submit_error(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI submit shows error."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["submit"])

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_cli_submit_dry_run(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI submit --dry-run."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    result = runner.invoke(app, ["submit", "--dry-run"])

    assert result.exit_code == 0
    assert "Would submit" in result.output


def test_cli_submit_success(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI submit success output."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_pr = PRInfo(
        number=123,
        url="https://github.com/owner/repo/pull/123",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = [None, mock_pr]
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit"])

    assert result.exit_code == 0
    assert "Created" in result.output


def test_cli_submit_with_errors(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI submit exits with code 1 when there are errors."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Server error"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit"])

    assert result.exit_code == 1
    assert "error" in result.output.lower()


def test_cli_submit_draft_flag(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI submit --draft flag."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_pr = PRInfo(
        number=123,
        url="https://github.com/owner/repo/pull/123",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=True,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = [None, mock_pr]
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit", "--draft"])

    assert result.exit_code == 0
    mock_client.create_pr.assert_called_once()
    assert mock_client.create_pr.call_args[1]["draft"] is True


def test_cli_submit_updated_only(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI submit when only updating existing PRs (no new PRs created)."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_pr = PRInfo(
        number=123,
        url="https://github.com/owner/repo/pull/123",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    # Always return existing PR - no new PRs created
    mock_client.get_pr_for_branch.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=True),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit"])

    assert result.exit_code == 0
    assert "Updated" in result.output
    # Should not have created any PRs
    mock_client.create_pr.assert_not_called()

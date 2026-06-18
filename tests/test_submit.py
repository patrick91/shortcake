"""Tests for submit command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from shortcake._github import GitHubClient, PRInfo
from shortcake._pr_stack import (
    SHORTCAKE_URL,
    STACK_END_MARKER,
    STACK_HEADING,
    STACK_START_MARKER,
    _build_stack_section,
    _parse_all_prs_from_body,
    _parse_merged_prs_from_body,
    _parse_stack_order_from_body,
    _update_pr_body_with_stack,
)
from shortcake.cli import app
from shortcake.commands.restack import RestackError, RestackResult
from shortcake.commands.submit import (
    PRAction,
    SubmitError,
    _get_commit_title,
    _submit,
)
from tests._git_helpers import Repo, add_paths, commit, get_ref, set_ref, set_remote

runner = CliRunner()


@pytest.fixture(autouse=True)
def _mock_restack():
    """Mock _restack to return no-op result by default."""
    with patch(
        "shortcake.commands.submit._restack",
        return_value=RestackResult(restacked_branches=[]),
    ):
        yield


# Helper to set up origin remote
def setup_origin_remote(repo: Repo, url: str = "git@github.com:owner/repo.git") -> None:
    """Configure origin remote for a repo."""
    set_remote(repo, "origin", url)


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
    # The heading carries a 🍰 link back to the project.
    assert STACK_HEADING in section
    assert SHORTCAKE_URL in section


def test_build_stack_section_missing_pr() -> None:
    """Test stack section when some branches don't have PRs."""
    stack_branches = ["branch_a", "branch_b"]
    pr_numbers = {"branch_a": 1}  # branch_b has no PR

    section = _build_stack_section(stack_branches, "branch_a", pr_numbers, "owner")

    assert "#1 (`branch_a`)" in section or "**#1** (`branch_a`)" in section
    assert "(no PR) (`branch_b`)" in section


def test_build_stack_section_merged_pr() -> None:
    """Test stack section with merged PR numbers."""
    stack_branches = ["branch_a", "branch_b"]
    pr_numbers = {"branch_a": 1}  # branch_b has no open PR
    merged_pr_numbers = {"branch_b": 5}  # but branch_b has a merged PR

    section = _build_stack_section(
        stack_branches, "branch_a", pr_numbers, "owner", merged_pr_numbers
    )

    assert "#1 (`branch_a`)" in section or "**#1** (`branch_a`)" in section
    assert "#5 (merged)" in section
    assert "branch_b" in section


def test_parse_merged_prs_from_body_basic() -> None:
    """Test parsing merged PRs from a stack section."""
    body = f"""{STACK_START_MARKER}
## Stack

- #100 (`top-branch`)
- **#99** (`current-branch`) <-- this PR
- #42 (merged) (`merged-branch`)
- #41 (merged) (`another-merged`)
{STACK_END_MARKER}

Some other content here."""

    result = _parse_merged_prs_from_body(body)

    assert result == {"merged-branch": 42, "another-merged": 41}


def test_parse_merged_prs_from_body_no_markers() -> None:
    """Test parsing merged PRs when no stack section exists."""
    body = "Just a regular PR body without stack section"

    result = _parse_merged_prs_from_body(body)

    assert result == {}


def test_parse_merged_prs_from_body_no_merged() -> None:
    """Test parsing when stack section has no merged PRs."""
    body = f"""{STACK_START_MARKER}
## Stack

- #100 (`branch-a`)
- **#99** (`branch-b`) <-- this PR
{STACK_END_MARKER}"""

    result = _parse_merged_prs_from_body(body)

    assert result == {}


def test_parse_all_prs_from_body_basic() -> None:
    """Test parsing all PRs from a stack section."""
    body = f"""{STACK_START_MARKER}
## Stack

- #100 (`top-branch`)
- **#99** (`current-branch`) <-- this PR
- #42 (`another-branch`)
{STACK_END_MARKER}

Some other content here."""

    result = _parse_all_prs_from_body(body)

    assert result == {"top-branch": 100, "current-branch": 99, "another-branch": 42}


def test_parse_all_prs_from_body_excludes_no_pr() -> None:
    """Test that (no PR) entries are excluded."""
    body = f"""{STACK_START_MARKER}
## Stack

- #100 (`branch-a`)
- (no PR) (`branch-b`)
- **#99** (`branch-c`) <-- this PR
{STACK_END_MARKER}"""

    result = _parse_all_prs_from_body(body)

    assert result == {"branch-a": 100, "branch-c": 99}
    assert "branch-b" not in result


def test_parse_all_prs_from_body_excludes_merged() -> None:
    """Test that merged PRs are also captured (they have PR numbers)."""
    body = f"""{STACK_START_MARKER}
## Stack

- #100 (`branch-a`)
- #42 (merged) (`merged-branch`)
{STACK_END_MARKER}"""

    result = _parse_all_prs_from_body(body)

    # Note: merged PRs are NOT captured by _parse_all_prs_from_body
    # because the pattern doesn't match "(merged)" suffix
    assert result == {"branch-a": 100}


def test_parse_all_prs_from_body_no_markers() -> None:
    """Test parsing all PRs when no stack section exists."""
    body = "Just a regular PR body without stack section"

    result = _parse_all_prs_from_body(body)

    assert result == {}


def test_parse_stack_order_from_body_basic() -> None:
    """Test parsing stack order from a stack section."""
    body = f"""{STACK_START_MARKER}
## Stack

- #100 (`top-branch`)
- **#99** (`current-branch`) <-- this PR
- #42 (merged) (`merged-branch`)
- (no PR) (`unsubmitted-branch`)
{STACK_END_MARKER}

Some other content."""

    result = _parse_stack_order_from_body(body)

    # Should return branches in display order (top to bottom)
    assert result == [
        "top-branch",
        "current-branch",
        "merged-branch",
        "unsubmitted-branch",
    ]


def test_parse_stack_order_from_body_no_markers() -> None:
    """Test parsing stack order when no stack section exists."""
    body = "Regular PR body without stack"

    result = _parse_stack_order_from_body(body)

    assert result == []


def test_parse_stack_order_from_body_empty_stack() -> None:
    """Test parsing when stack section exists but is empty."""
    body = f"""{STACK_START_MARKER}
## Stack

{STACK_END_MARKER}"""

    result = _parse_stack_order_from_body(body)

    assert result == []


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
    head_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", head_sha)

    with pytest.raises(SubmitError, match="detached HEAD"):
        _submit(temp_repo)


def test_submit_warns_uncommitted_changes(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit warns but continues with uncommitted changes."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Create uncommitted changes
    test_file = tmp_path / "uncommitted.txt"
    test_file.write_text("uncommitted")
    add_paths(repo_with_tracked_feature, test_file)

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    # Should show warning but continue (dry-run to avoid needing full mocks)
    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        result = runner.invoke(app, ["submit", "--dry-run"])

    assert result.exit_code == 0
    assert "Warning: You have uncommitted changes" in result.output


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

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        result = _submit(repo_with_tracked_feature, dry_run=True)

    assert result.stack_branches == ["feature"]
    assert len(result.branch_results) == 0  # No actual results in dry run


def test_submit_dry_run_shows_create_new_pr(
    repo_with_tracked_feature: Repo,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test submit dry run shows 'create new PR' for branches without PRs."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        _submit(repo_with_tracked_feature, dry_run=True)

    captured = capsys.readouterr()
    assert "create new PR" in captured.out
    assert "feature" in captured.out


def test_submit_dry_run_shows_update_pr(
    repo_with_tracked_feature: Repo,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test submit dry run shows 'update PR' for branches with existing PRs."""
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
    mock_client.get_pr_for_branch.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        _submit(repo_with_tracked_feature, dry_run=True)

    captured = capsys.readouterr()
    assert "update PR #123" in captured.out
    assert "feature" in captured.out


def test_submit_dry_run_shows_skip_merged(
    repo_with_tracked_feature: Repo,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test submit dry run shows 'skip - already merged' for merged branches."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = True
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        _submit(repo_with_tracked_feature, dry_run=True)

    captured = capsys.readouterr()
    assert "skip - already merged" in captured.out
    assert "feature" in captured.out


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
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert len(result.branch_results) == 1
    assert result.branch_results[0].action == PRAction.CREATED
    assert result.branch_results[0].pr_number == 123


def test_submit_push_failure(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles push failure with error message."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "shortcake.commands.submit.push_branch",
            return_value=(False, "remote has diverged (use --force to overwrite)"),
        ),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert len(result.branch_results) == 1
    assert result.branch_results[0].action == PRAction.SKIPPED
    assert (
        result.branch_results[0].error
        == "remote has diverged (use --force to overwrite)"
    )


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
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        pytest.raises(SubmitError, match="forbidden"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_planning_non_fatal_http_error_falls_back(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that non-fatal HTTP errors during planning fall back to create."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

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
    # Planning fails with 500
    mock_client.get_pr_for_branch.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=mock_response
    )
    # But execution succeeds
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    # Should have fallen back to create action
    assert result.branch_results[0].action == PRAction.CREATED


def test_submit_planning_network_error_falls_back(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that network errors during planning fall back to create."""
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
    # Planning fails with network error
    mock_client.get_pr_for_branch.side_effect = httpx.ConnectError("Connection refused")
    # But execution succeeds
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    # Should have fallen back to create action
    assert result.branch_results[0].action == PRAction.CREATED


def test_submit_handles_401_during_create_pr(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 401 error during PR creation (not planning)."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Bad credentials"

    mock_client = MagicMock(spec=GitHubClient)
    # Planning succeeds
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    # Execution fails with 401
    mock_client.create_pr.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        pytest.raises(SubmitError, match="authentication failed"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_handles_rate_limit_during_create_pr(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 403 rate limit error during PR creation."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "API rate limit exceeded"

    mock_client = MagicMock(spec=GitHubClient)
    # Planning succeeds
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    # Execution fails with 403 rate limit
    mock_client.create_pr.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        pytest.raises(SubmitError, match="rate limit"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_handles_403_forbidden_during_create_pr(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 403 forbidden error during PR creation."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Repository access blocked"

    mock_client = MagicMock(spec=GitHubClient)
    # Planning succeeds
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    # Execution fails with 403 forbidden
    mock_client.create_pr.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        pytest.raises(SubmitError, match="forbidden"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_handles_422_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles 422 validation error during PR creation."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.text = "Validation failed"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.side_effect = httpx.HTTPStatusError(
        "422", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].error is not None
    error_msg = result.branch_results[0].error
    assert "422" in error_msg or "Validation" in error_msg


def test_submit_handles_other_http_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles other HTTP errors during PR creation."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
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
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
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
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit"])

    assert result.exit_code == 0
    assert "Updated" in result.output
    # Should not have created any PRs
    mock_client.create_pr.assert_not_called()


def test_submit_skips_branch_with_merged_pr(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit skips branches that have merged PRs."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None  # No open PR
    mock_client.has_merged_pr.return_value = True  # But has merged PR
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert len(result.branch_results) == 1
    assert result.branch_results[0].action == PRAction.SKIPPED
    # Should not have tried to create a PR
    mock_client.create_pr.assert_not_called()


def test_submit_handles_network_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles network errors (DNS, timeout, connection)."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.side_effect = httpx.ConnectError("Connection refused")
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].error is not None
    assert "Network error" in result.branch_results[0].error


def test_submit_handles_timeout_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit handles timeout errors."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.side_effect = httpx.TimeoutException("Request timeout")
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].error is not None
    assert "Network error" in result.branch_results[0].error


def test_submit_stack_body_update_network_error_non_fatal(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that network errors updating PR body with stack are non-fatal."""
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
        # Second call during body update - raise network error
        raise httpx.ConnectError("Connection refused")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = mock_get_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        # Should not raise - network error during body update is non-fatal
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].action == PRAction.UPDATED


def test_submit_merged_pr_lookup_error_ignored(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that errors looking up merged PRs are gracefully ignored."""
    setup_origin_remote(repo_with_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # PR only for branch_b, not branch_a
    mock_pr_b = PRInfo(
        number=789,
        url="https://github.com/owner/repo/pull/789",
        base="branch_a",
        title="feat: branch b",
        body="",
        state="open",
        is_draft=False,
    )

    import os

    os.chdir(tmp_path)

    mock_client = MagicMock(spec=GitHubClient)
    # branch_a has no PR, branch_b has PR
    mock_client.get_pr_for_branch.side_effect = lambda b: (
        None if b == "branch_a" else mock_pr_b
    )
    # Merged PR lookup fails with network error
    mock_client.get_merged_pr_number.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        # Should complete - errors looking up merged PRs are ignored
        result = _submit(repo_with_stack)

    # Should have result for branch_b
    assert any(br.branch == "branch_b" for br in result.branch_results)


def test_submit_preserves_merged_prs_from_existing_body(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merged historical PRs are kept below active stack branches."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Existing PR body has historical merged PRs from an older stack.
    existing_body = f"""{STACK_START_MARKER}
## Stack

- **#456** (`feature`) <-- this PR
- #100 (merged) (`old-merged-branch`)
- #99 (merged) (`another-old-branch`)
{STACK_END_MARKER}

Original description."""

    mock_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body=existing_body,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    # Track what body is updated with
    updated_bodies: list[str] = []

    def track_update(pr_num: int, body: str | None = None, base: str | None = None):
        if body is not None:
            updated_bodies.append(body)

    mock_client.update_pr.side_effect = track_update

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature)

    # Verify the updated body preserves the merged PRs.
    assert len(updated_bodies) > 0
    updated_body = updated_bodies[-1]
    assert "#100 (merged)" in updated_body
    assert "old-merged-branch" in updated_body
    assert "#99 (merged)" in updated_body
    assert "another-old-branch" in updated_body
    assert "**#456** (`feature`) <-- this PR" in updated_body


def test_submit_moves_top_merged_historical_branch_below_active_stack(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merged historical branches are kept below active stack branches."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Historical stack order has a merged branch at the top of display order.
    # It should move below the active branch when the stack is regenerated.
    existing_body = f"""{STACK_START_MARKER}
## Stack

- #100 (merged) (`top-merged-branch`)
- **#456** (`feature`) <-- this PR
{STACK_END_MARKER}

Description."""

    mock_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body=existing_body,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    updated_bodies: list[str] = []

    def track_update(pr_num: int, body: str | None = None, base: str | None = None):
        if body is not None:
            updated_bodies.append(body)

    mock_client.update_pr.side_effect = track_update

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature)

    # Verify the merged branch is preserved below the active branch.
    assert len(updated_bodies) > 0
    updated_body = updated_bodies[-1]
    assert "#100 (merged)" in updated_body
    assert "top-merged-branch" in updated_body
    assert "**#456** (`feature`) <-- this PR" in updated_body

    feature_pos = updated_body.find("`feature`")
    merged_pos = updated_body.find("`top-merged-branch`")
    assert feature_pos > 0
    assert merged_pos > 0
    assert feature_pos < merged_pos


def test_submit_places_merged_branches_below_active_stack_order(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merged-only historical stack order is retained below active branches."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Existing PR body has historical stack order with merged branches.
    existing_body = f"""{STACK_START_MARKER}
## Stack

- **#456** (`feature`) <-- this PR
- #100 (merged) (`first-merged`)
- #99 (merged) (`second-merged`)
{STACK_END_MARKER}

Description."""

    mock_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body=existing_body,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    updated_bodies: list[str] = []

    def track_update(pr_num: int, body: str | None = None, base: str | None = None):
        if body is not None:
            updated_bodies.append(body)

    mock_client.update_pr.side_effect = track_update

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature)

    # Verify merged branches remain below the active stack branch.
    assert len(updated_bodies) > 0
    updated_body = updated_bodies[-1]
    assert "**#456** (`feature`) <-- this PR" in updated_body
    assert "first-merged" in updated_body
    assert "second-merged" in updated_body

    feature_pos = updated_body.find("`feature`")
    first_merged_pos = updated_body.find("`first-merged`")
    second_merged_pos = updated_body.find("`second-merged`")
    assert feature_pos < first_merged_pos < second_merged_pos


def test_submit_preserves_historical_prs_from_existing_body(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that non-merged PRs from existing stack section are preserved.

    This handles the case where a branch doesn't exist locally but its PR
    number is already recorded in the stack visualization.
    """
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Existing PR body has a branch with PR that doesn't exist locally
    existing_body = f"""{STACK_START_MARKER}
## Stack

- #789 (`parent-branch`)
- **#456** (`feature`) <-- this PR
{STACK_END_MARKER}

Description."""

    mock_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body=existing_body,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)

    # Return the mock PR only for 'feature' branch, return None for 'parent-branch'
    # to simulate that the branch doesn't exist on remote either
    def get_pr_side_effect(branch: str):
        if branch == "feature":
            return mock_pr
        return None  # 'parent-branch' doesn't have an open PR on GitHub

    mock_client.get_pr_for_branch.side_effect = get_pr_side_effect
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    updated_bodies: list[str] = []

    def track_update(pr_num: int, body: str | None = None, base: str | None = None):
        if body is not None:
            updated_bodies.append(body)

    mock_client.update_pr.side_effect = track_update

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature)

    # Verify the PR number from the historical stack is preserved
    assert len(updated_bodies) > 0
    updated_body = updated_bodies[-1]

    # The parent-branch PR number should be preserved from the existing body
    assert "#789" in updated_body
    assert "`parent-branch`" in updated_body


def test_submit_marks_historical_pr_merged_when_github_reports_merged(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Historical PR entries are marked merged when GitHub says they merged.

    This covers old stack bodies where the branch was listed as an open PR
    when the body was last written, but has since merged and disappeared
    locally.
    """
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    existing_body = f"""{STACK_START_MARKER}
## Stack

- #789 (`merged-parent-branch`)
- **#456** (`feature`) <-- this PR
{STACK_END_MARKER}

Description."""

    mock_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body=existing_body,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)

    def get_pr_side_effect(branch: str):
        if branch == "feature":
            return mock_pr
        return None

    mock_client.get_pr_for_branch.side_effect = get_pr_side_effect
    mock_client.get_merged_pr_number.side_effect = lambda branch: {
        "merged-parent-branch": 789,
    }.get(branch)
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    updated_bodies: list[str] = []

    def track_update(pr_num: int, body: str | None = None, base: str | None = None):
        if body is not None:
            updated_bodies.append(body)

    mock_client.update_pr.side_effect = track_update

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature)

    assert len(updated_bodies) > 0
    updated_body = updated_bodies[-1]
    assert "`merged-parent-branch`" in updated_body
    assert "#789 (merged)" in updated_body
    assert "**#456** (`feature`) <-- this PR" in updated_body

    feature_pos = updated_body.find("`feature`")
    merged_pos = updated_body.find("`merged-parent-branch`")
    assert feature_pos < merged_pos


def test_submit_looks_up_historical_branch_on_github(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that historical branches not in local repo are looked up on GitHub.

    When a branch is in the stack visualization but not locally, and it's not
    in the parsed PR numbers, we should try to look it up via GitHub API.
    """
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Existing PR body has a branch without a PR number (maybe it was (no PR) before)
    existing_body = f"""{STACK_START_MARKER}
## Stack

- (no PR) (`parent-branch`)
- **#456** (`feature`) <-- this PR
{STACK_END_MARKER}

Description."""

    mock_feature_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body=existing_body,
        state="open",
        is_draft=False,
    )

    mock_parent_pr = PRInfo(
        number=789,
        url="https://github.com/owner/repo/pull/789",
        base="main",
        title="feat: parent feature",
        body="",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)

    # Return the appropriate PR for each branch
    def get_pr_side_effect(branch: str):
        if branch == "feature":
            return mock_feature_pr
        if branch == "parent-branch":
            return mock_parent_pr  # Found on GitHub!
        return None

    mock_client.get_pr_for_branch.side_effect = get_pr_side_effect
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    updated_bodies: list[str] = []

    def track_update(pr_num: int, body: str | None = None, base: str | None = None):
        if body is not None:
            updated_bodies.append(body)

    mock_client.update_pr.side_effect = track_update

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature)

    # Verify that parent-branch was looked up on GitHub and its PR number included
    assert len(updated_bodies) > 0
    updated_body = updated_bodies[-1]

    # The parent-branch should now have its PR number from GitHub lookup
    assert "#789" in updated_body
    assert "`parent-branch`" in updated_body


def test_submit_historical_branch_lookup_error_ignored(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that errors when looking up historical branches are ignored.

    When a GitHub API error occurs while looking up a historical branch,
    the error should be silently ignored and the branch shown as (no PR).
    """
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Existing PR body has a branch without a PR number
    existing_body = f"""{STACK_START_MARKER}
## Stack

- (no PR) (`parent-branch`)
- **#456** (`feature`) <-- this PR
{STACK_END_MARKER}

Description."""

    mock_feature_pr = PRInfo(
        number=456,
        url="https://github.com/owner/repo/pull/456",
        base="main",
        title="feat: add feature",
        body=existing_body,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)

    # Return PR for feature, raise error for parent-branch
    def get_pr_side_effect(branch: str):
        if branch == "feature":
            return mock_feature_pr
        if branch == "parent-branch":
            # Simulate GitHub API error
            raise httpx.HTTPStatusError(
                "Not found",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            )
        return None

    mock_client.get_pr_for_branch.side_effect = get_pr_side_effect
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    updated_bodies: list[str] = []

    def track_update(pr_num: int, body: str | None = None, base: str | None = None):
        if body is not None:
            updated_bodies.append(body)

    mock_client.update_pr.side_effect = track_update

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        # Should not raise - error is silently ignored
        _submit(repo_with_tracked_feature)

    # Verify submit completed and parent-branch shows (no PR)
    assert len(updated_bodies) > 0
    updated_body = updated_bodies[-1]
    assert "`parent-branch`" in updated_body
    assert "(no PR)" in updated_body


# Tests for restack integration


def test_submit_calls_restack(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit calls _restack before pushing."""
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
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.return_value = mock_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    restack_mock = MagicMock(return_value=RestackResult(restacked_branches=["feature"]))

    with (
        patch("shortcake.commands.submit._restack", restack_mock),
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    restack_mock.assert_called_once_with(repo_with_tracked_feature)
    assert len(result.branch_results) == 1


def test_submit_restack_conflict_raises_submit_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit raises SubmitError on restack conflict."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    restack_mock = MagicMock(
        return_value=RestackResult(restacked_branches=[], conflict_branch="feature")
    )

    with (
        patch("shortcake.commands.submit._restack", restack_mock),
        pytest.raises(SubmitError, match="Conflict while restacking"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_restack_error_raises_submit_error(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit converts RestackError to SubmitError."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    restack_mock = MagicMock(side_effect=RestackError("Restack already in progress."))

    with (
        patch("shortcake.commands.submit._restack", restack_mock),
        pytest.raises(SubmitError, match="Restack already in progress"),
    ):
        _submit(repo_with_tracked_feature)


def test_submit_skips_restack_on_dry_run(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit skips restack when doing dry run."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    restack_mock = MagicMock(return_value=RestackResult(restacked_branches=[]))

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit._restack", restack_mock),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature, dry_run=True)

    restack_mock.assert_not_called()


def test_submit_resolves_merged_parent_for_existing_pr(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit resolves parent to merged target when parent was deleted."""
    from shortcake._trailers import Trailers

    # Create branch with trailer pointing to a non-existent parent
    # (simulating a parent that was merged and deleted locally)
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="deleted-parent")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    # "deleted-parent" does NOT exist as a local branch
    setup_origin_remote(temp_repo)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # PR already exists with base "main" (GitHub auto-retargeted)
    mock_pr = PRInfo(
        number=42,
        url="https://github.com/owner/repo/pull/42",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = mock_pr
    mock_client.get_merged_pr_base.return_value = "main"
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(temp_repo)

    # Parent resolved to "main", which matches existing PR base
    # so update_pr should NOT be called with base= (no base change needed)
    base_update_calls = [
        c for c in mock_client.update_pr.call_args_list if c[1].get("base")
    ]
    assert len(base_update_calls) == 0
    assert len(result.branch_results) == 1
    assert result.branch_results[0].action == PRAction.UPDATED


def test_submit_resolves_merged_parent_for_new_pr(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit creates PR with resolved base when parent was merged."""
    from shortcake._trailers import Trailers

    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="deleted-parent")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    setup_origin_remote(temp_repo)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    new_pr = PRInfo(
        number=99,
        url="https://github.com/owner/repo/pull/99",
        base="main",
        title="feat: add feature",
        body="",
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.get_merged_pr_base.return_value = "main"
    mock_client.create_pr.return_value = new_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(temp_repo)

    # PR should be created with base="main" (resolved from merged parent)
    mock_client.create_pr.assert_called_once()
    call_kwargs = mock_client.create_pr.call_args[1]
    assert call_kwargs["base"] == "main"
    assert result.branch_results[0].action == PRAction.CREATED


def test_submit_merged_parent_resolution_skipped_when_parent_exists(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that parent resolution is NOT triggered when parent exists locally."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    new_pr = PRInfo(
        number=1,
        url="https://github.com/owner/repo/pull/1",
        base="main",
        title="feat",
        body="",
        state="open",
        is_draft=False,
    )
    mock_client.create_pr.return_value = new_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_tracked_feature)

    # get_merged_pr_base should NOT be called since "main" exists locally
    mock_client.get_merged_pr_base.assert_not_called()


def test_submit_merged_parent_resolution_api_error_ignored(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that API errors during parent resolution are handled gracefully."""
    from shortcake._trailers import Trailers

    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="deleted-parent")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)

    setup_origin_remote(temp_repo)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    # API error when trying to resolve merged parent
    mock_client.get_merged_pr_base.side_effect = httpx.ConnectError("timeout")
    new_pr = PRInfo(
        number=1,
        url="https://github.com/owner/repo/pull/1",
        base="deleted-parent",
        title="feat",
        body="",
        state="open",
        is_draft=False,
    )
    mock_client.create_pr.return_value = new_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        # Should not raise - falls back to original parent
        _submit(temp_repo)

    # create_pr was called with original parent (fallback)
    mock_client.create_pr.assert_called_once()
    call_kwargs = mock_client.create_pr.call_args[1]
    assert call_kwargs["base"] == "deleted-parent"


def test_submit_422_base_not_found_error_message(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 422 error about missing base gives helpful error message."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.text = (
        '{"message":"Validation Failed","errors":'
        '[{"message":"Proposed base branch \'main\' was not found"}]}'
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.side_effect = httpx.HTTPStatusError(
        "422", request=MagicMock(), response=mock_response
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_tracked_feature)

    assert result.branch_results[0].error is not None
    assert "not found on GitHub" in result.branch_results[0].error
    assert "sc sync" in result.branch_results[0].error


def test_submit_updates_moved_away_branch_prs(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit updates PR descriptions of branches that moved away.

    Scenario: stack was main → branch_a → branch_b.
    After `sc move branch_b -p main`, the stacks become:
      main → branch_a  (stack 1)
      main → branch_b  (stack 2)

    When submitting from branch_a, the PR for branch_b should also be updated
    to show its new (solo) stack, not the old stack that included branch_a.
    """
    from shortcake.commands.move import _move

    setup_origin_remote(repo_with_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    # Move branch_b to have parent=main (splits the stack)
    from tests._git_helpers import switch_branch

    switch_branch(repo_with_stack, "branch_a")
    _move(repo_with_stack, "branch_b", "main")

    # Set up mock PRs with old stack body on branch_b
    old_stack_body = (
        f"{STACK_START_MARKER}\n"
        "## Stack\n"
        "\n"
        "- **#20** (`branch_b`) <-- this PR\n"
        "- #10 (`branch_a`)\n"
        f"{STACK_END_MARKER}\n"
        "\n"
        "Original branch_b description"
    )

    old_stack_body_a = (
        f"{STACK_START_MARKER}\n"
        "## Stack\n"
        "\n"
        "- #20 (`branch_b`)\n"
        "- **#10** (`branch_a`) <-- this PR\n"
        f"{STACK_END_MARKER}\n"
        "\n"
        "branch_a body"
    )

    mock_pr_a = PRInfo(
        number=10,
        url="https://github.com/owner/repo/pull/10",
        base="main",
        title="feat: branch a",
        body=old_stack_body_a,
        state="open",
        is_draft=False,
    )
    mock_pr_b = PRInfo(
        number=20,
        url="https://github.com/owner/repo/pull/20",
        base="branch_a",  # Old base, before the move
        title="feat: branch b",
        body=old_stack_body,
        state="open",
        is_draft=False,
    )

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = lambda b: {
        "branch_a": mock_pr_a,
        "branch_b": mock_pr_b,
    }.get(b)
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        _submit(repo_with_stack)

    # Find update_pr calls for branch_b's PR (#20)
    body_updates_for_b = [
        call
        for call in mock_client.update_pr.call_args_list
        if call[0][0] == 20 and call[1].get("body") is not None
    ]

    assert len(body_updates_for_b) >= 1, (
        "branch_b's PR body was not updated after it moved to a different stack"
    )

    # The new body for branch_b should NOT contain branch_a
    new_body = body_updates_for_b[-1][1]["body"]
    assert "branch_a" not in new_body, (
        f"branch_b's PR body still references branch_a after move: {new_body}"
    )
    # It should contain branch_b
    assert "branch_b" in new_body
    # It should preserve the original description
    assert "Original branch_b description" in new_body

    base_updates_for_b = [
        call
        for call in mock_client.update_pr.call_args_list
        if call[0][0] == 20 and call[1].get("base") == "main"
    ]
    assert base_updates_for_b, "branch_b's PR base was not updated after move"

    # branch_a's PR should NOT contain branch_b (it moved away)
    body_updates_for_a = [
        call
        for call in mock_client.update_pr.call_args_list
        if call[0][0] == 10 and call[1].get("body") is not None
    ]
    assert len(body_updates_for_a) >= 1
    new_body_a = body_updates_for_a[-1][1]["body"]
    assert "branch_b" not in new_body_a, (
        f"branch_a's PR body still references branch_b after move: {new_body_a}"
    )

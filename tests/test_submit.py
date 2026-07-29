"""Tests for submit command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from shortcake._github import GitHubClient, PRInfo
from shortcake._output import get_rich_toolkit
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
from shortcake._stack_view import RowState, StackRenderer, StackRow
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.restack import RestackError, RestackResult
from shortcake.commands.submit import (
    BranchPlan,
    BranchSubmitResult,
    PRAction,
    SubmitError,
    SubmitResult,
    _ask_scope,
    _build_branch_plans,
    _get_commit_title,
    _plan_heading,
    _rows_for_execution,
    _should_ask_scope,
    _show_submit_plan,
    _start_execution,
    _submit,
    _submit_footer,
)
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    commit_files,
    create_branch,
    get_branch_head,
    get_ref,
    set_ref,
    set_remote,
    switch_branch,
)

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


@pytest.fixture
def repo_with_three_branch_stack(repo_with_stack: Repo, tmp_path: Path) -> Repo:
    """Extend the standard stack and check out its middle branch."""
    create_branch(
        repo_with_stack,
        "branch_c",
        get_branch_head(repo_with_stack, "branch_b"),
        checkout=True,
    )
    message = Trailers(parent_branch="branch_b").apply_to("feat: branch c")
    commit_files(repo_with_stack, {tmp_path / "c.txt": "branch c content"}, message)
    switch_branch(repo_with_stack, "branch_b")
    return repo_with_stack


# Tests for helper functions


def test_get_commit_title(repo_with_tracked_feature: Repo) -> None:
    """Test getting commit title from branch."""
    title = _get_commit_title(repo_with_tracked_feature, "feature")
    assert title == "feat: add feature"


def _row(output: str, branch: str) -> str:
    """The plan-tree row for a branch, with column padding collapsed.

    Status lives in a right-hand column now, so the padding depends on the
    longest branch name; assertions should not encode it.
    """
    line = next(
        line
        for line in output.splitlines()
        if line.strip().endswith(branch) or f" {branch} " in line + " "
    )
    return " ".join(line.split())


def _plan_toolkit() -> MagicMock:
    """MagicMock toolkit with a real console, so layout can measure a width."""
    toolkit = MagicMock()
    toolkit.console = Console(width=100, height=40)
    return toolkit


def test_submit_plan_renders_downward_and_dims_excluded_branches(
    repo_with_three_branch_stack: Repo,
) -> None:
    """The preview follows the ls graph style and de-emphasizes exclusions."""
    toolkit = _plan_toolkit()
    plans = [
        BranchPlan(
            branch="branch_a",
            action=PRAction.UPDATED,
            existing_pr_number=12,
            existing_pr_url="https://github.com/owner/repo/pull/12",
        ),
        BranchPlan(branch="branch_b", action=PRAction.CREATED),
        BranchPlan(branch="branch_c", action=PRAction.CREATED),
    ]

    _show_submit_plan(
        repo_with_three_branch_stack,
        toolkit,
        ["branch_a", "branch_b", "branch_c"],
        ["branch_a", "branch_b"],
        "branch_b",
        plans=plans,
    )

    renderables = [call.args[0] for call in toolkit.print.call_args_list if call.args]
    lines = [str(renderable) for renderable in renderables]
    base_index = next(index for index, line in enumerate(lines) if "main" in line)
    branch_a_index = next(
        index for index, line in enumerate(lines) if "branch_a" in line
    )
    branch_b_index = next(
        index for index, line in enumerate(lines) if "branch_b" in line
    )
    assert base_index < branch_a_index < branch_b_index
    assert "● branch_a" in lines[branch_a_index]
    assert "update PR #12" in lines[branch_a_index]
    # the current branch keeps ◉ while planning, and is bold rather than labelled
    assert "◉ branch_b" in lines[branch_b_index]
    assert "create PR" in lines[branch_b_index]
    assert any(
        span.style is not None and getattr(span.style, "bold", None)
        for span in renderables[branch_b_index].spans
    )
    pr_line = renderables[branch_a_index]
    pr_span = next(
        span
        for span in pr_line.spans
        if getattr(span.style, "link", None) == "https://github.com/owner/repo/pull/12"
    )
    assert pr_span.style.color.name == "cyan"
    assert pr_span.style.underline is True
    excluded = next(
        renderable for renderable in renderables if "branch_c" in str(renderable)
    )
    assert "◯ branch_c" in str(excluded)
    assert "not submitted" in str(excluded)
    assert any(
        getattr(span.style, "color", None) is not None
        and span.style.color.name == "bright_black"
        for span in excluded.spans
    )


def test_submit_plan_preserves_forked_stack_shape(repo_with_fork: Repo) -> None:
    """Sibling branches use tree connectors instead of appearing sequential."""
    toolkit = _plan_toolkit()
    plans = [
        BranchPlan(branch="branch_a", action=PRAction.CREATED),
        BranchPlan(branch="branch_b", action=PRAction.CREATED),
        BranchPlan(branch="branch_c", action=PRAction.CREATED),
    ]

    _show_submit_plan(
        repo_with_fork,
        toolkit,
        ["branch_a", "branch_b", "branch_c"],
        ["branch_a", "branch_c"],
        "branch_c",
        plans=plans,
    )

    lines = [str(call.args[0]) for call in toolkit.print.call_args_list if call.args]
    # arms now get the same `│` breathing line a linear chain gets
    assert any("├─◯ branch_b" in line and "not submitted" in line for line in lines)
    assert any("└─◉ branch_c" in line and "create PR" in line for line in lines)


def test_submit_plan_handles_empty_or_actionless_plans(
    repo_with_tracked_feature: Repo,
) -> None:
    """The renderer is optional and can display a branch without an action."""
    toolkit = _plan_toolkit()
    _show_submit_plan(
        repo_with_tracked_feature,
        toolkit,
        [],
        [],
        "feature",
    )
    toolkit.echo.assert_not_called()

    _show_submit_plan(
        repo_with_tracked_feature,
        toolkit,
        ["feature"],
        ["feature"],
        "feature",
    )
    lines = [str(call.args[0]) for call in toolkit.print.call_args_list if call.args]
    feature_line = next(line for line in lines if "feature" in line)
    assert "◉ feature" in feature_line
    # no plan means no status column at all
    assert feature_line.rstrip().endswith("feature")


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


def test_cli_submit_defaults_to_current_and_downstack(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain submit includes ancestors but excludes upstack branches."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit._is_interactive", return_value=False),
        patch("shortcake.commands.submit.typer.confirm") as confirm_mock,
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit", "--dry-run"])

    assert result.exit_code == 0
    assert "Would submit 2 branch(es)" in result.output
    assert "branch_a (create new PR)" in result.output
    assert "branch_b (create new PR)" in result.output
    assert "branch_c (create new PR)" not in result.output
    assert _row(result.output, "branch_c") == "◯ branch_c not submitted"
    assert _row(result.output, "branch_b") == "◉ branch_b create PR"
    assert _row(result.output, "branch_a") == "● branch_a create PR"
    assert _row(result.output, "main") == "◯ main (base)"
    assert "● 2 selected · ○ 1 upstack branch not selected" in result.output
    confirm_mock.assert_not_called()
    assert [call.args[0] for call in mock_client.get_pr_for_branch.call_args_list] == [
        "branch_a",
        "branch_b",
    ]


def test_submit_downstack_excludes_sibling_branch(
    repo_with_fork: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default selection follows ancestors and does not include a sibling."""
    setup_origin_remote(repo_with_fork)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        result = _submit(repo_with_fork, dry_run=True)

    assert [plan.branch for plan in result.planned] == ["branch_a", "branch_c"]


def test_submit_fills_gaps_in_precomputed_plan(
    repo_with_three_branch_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execution fetches any selected branch missing from a preview plan."""
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        result = _submit(
            repo_with_three_branch_stack,
            dry_run=True,
            precomputed_plans=[
                BranchPlan(
                    branch="branch_a",
                    action=PRAction.CREATED,
                    parent="main",
                )
            ],
        )

    assert [plan.branch for plan in result.planned] == ["branch_a", "branch_b"]


def test_cli_submit_stack_submits_every_branch(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--stack retains the previous whole-stack behavior."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        result = runner.invoke(app, ["submit", "--stack", "--dry-run"])

    assert result.exit_code == 0
    assert "Would submit 3 branch(es)" in result.output
    assert "branch_a (create new PR)" in result.output
    assert "branch_b (create new PR)" in result.output
    assert "branch_c (create new PR)" in result.output
    assert _row(result.output, "branch_c") == "● branch_c create PR"
    assert "○ 1 upstack branch not selected" not in result.output


def test_cli_submit_interactively_offers_whole_stack(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interactive user can expand plain submit to the full stack."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit._is_interactive", return_value=True),
        patch.object(type(get_rich_toolkit().console), "is_terminal", True),
        patch("shortcake.commands.submit.pick_scope", return_value="stack") as picker,
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit", "--dry-run"])

    assert result.exit_code == 0
    picker.assert_called_once()
    # the picker replaces the plan tree, so it is not printed separately
    assert "Submit plan:" not in result.output
    assert "Would submit 3 branch(es)" in result.output
    assert "branch_c (create new PR)" in result.output


def test_cli_submit_declining_upstack_keeps_downstack_selection(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choosing the downstack scope submits only ancestors through current."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit._is_interactive", return_value=True),
        patch.object(type(get_rich_toolkit().console), "is_terminal", True),
        patch("shortcake.commands.submit.pick_scope", return_value="downstack"),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit", "--dry-run"], input="n\n")

    assert result.exit_code == 0
    assert "Would submit 2 branch(es)" in result.output
    assert "branch_a (create new PR)" in result.output
    assert "branch_b (create new PR)" in result.output
    assert "branch_c (create new PR)" not in result.output


def test_cli_submit_cancel_scope_does_nothing(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel is a real option now: nothing is pushed and no PR is touched."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("shortcake.commands.submit._is_interactive", return_value=True),
        patch.object(type(get_rich_toolkit().console), "is_terminal", True),
        patch("shortcake.commands.submit.pick_scope", return_value="cancel"),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        patch("shortcake.commands.submit.push_branch") as push,
    ):
        result = runner.invoke(app, ["submit"])

    assert result.exit_code == 0
    assert "Cancelled" in result.output
    push.assert_not_called()


def test_submit_downstack_limits_push_and_pr_updates(
    repo_with_three_branch_stack: Repo,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Downstack submit pushes ancestors but leaves upstack PRs untouched."""
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    prs = {
        "branch_a": PRInfo(
            number=1,
            url="https://github.com/owner/repo/pull/1",
            base="main",
            title="feat: branch a",
            body="",
            state="open",
            is_draft=False,
        ),
        "branch_b": PRInfo(
            number=2,
            url="https://github.com/owner/repo/pull/2",
            base="branch_a",
            title="feat: branch b",
            body="",
            state="open",
            is_draft=False,
        ),
        "branch_c": PRInfo(
            number=3,
            url="https://github.com/owner/repo/pull/3",
            base="branch_b",
            title="feat: branch c",
            body="",
            state="open",
            is_draft=False,
        ),
    }
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = prs.get
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    restack_mock = MagicMock(return_value=RestackResult(restacked_branches=[]))

    with (
        patch("shortcake.commands.submit._restack", restack_mock),
        patch(
            "shortcake.commands.submit.push_branch", return_value=(True, None)
        ) as push_mock,
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_three_branch_stack, submit_stack=False)

    pushed_branches = [call.args[1] for call in push_mock.call_args_list]
    assert pushed_branches == ["branch_a", "branch_b"]
    assert restack_mock.call_args.kwargs["branches"] == ["branch_a", "branch_b"]
    assert [branch.branch for branch in result.branch_results] == [
        "branch_a",
        "branch_b",
    ]
    body_update_numbers = [
        call.args[0]
        for call in mock_client.update_pr.call_args_list
        if "body" in call.kwargs
    ]
    assert body_update_numbers == [1, 2]

    captured = capsys.readouterr()
    assert "Submit plan:" in captured.out
    assert _row(captured.out, "branch_c") == "◯ branch_c not submitted"
    assert _row(captured.out, "branch_b") == "◉ branch_b update PR #2"
    assert _row(captured.out, "branch_a") == "● branch_a update PR #1"

    updated_bodies = [
        call.kwargs["body"]
        for call in mock_client.update_pr.call_args_list
        if "body" in call.kwargs
    ]
    assert updated_bodies
    assert all("branch_c" not in body for body in updated_bodies)


def test_submit_downstack_creates_parent_before_current(
    repo_with_three_branch_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new current PR is created only after its downstack base is pushed."""
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    created_prs: dict[str, PRInfo] = {}

    def create_pr(**kwargs: object) -> PRInfo:
        head = str(kwargs["head"])
        pr = PRInfo(
            number=len(created_prs) + 1,
            url=f"https://github.com/owner/repo/pull/{len(created_prs) + 1}",
            base=str(kwargs["base"]),
            title=str(kwargs["title"]),
            body="",
            state="open",
            is_draft=bool(kwargs["draft"]),
        )
        created_prs[head] = pr
        return pr

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.side_effect = created_prs.get
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.side_effect = create_pr
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "shortcake.commands.submit.push_branch", return_value=(True, None)
        ) as push_mock,
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(repo_with_three_branch_stack)

    assert [call.args[1] for call in push_mock.call_args_list] == [
        "branch_a",
        "branch_b",
    ]
    assert [call.kwargs["head"] for call in mock_client.create_pr.call_args_list] == [
        "branch_a",
        "branch_b",
    ]
    assert [call.kwargs["base"] for call in mock_client.create_pr.call_args_list] == [
        "main",
        "branch_a",
    ]
    assert [branch.branch for branch in result.branch_results] == [
        "branch_a",
        "branch_b",
    ]
    updated_bodies = [
        call.kwargs["body"]
        for call in mock_client.update_pr.call_args_list
        if "body" in call.kwargs
    ]
    assert updated_bodies
    assert all("branch_c" not in body for body in updated_bodies)
    assert all("(no PR)" not in body for body in updated_bodies)


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


def test_submit_stealth_dry_run_shows_push_only_plan(
    repo_with_tracked_feature: Repo,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test stealth dry run does not need GitHub and shows push-only work."""
    setup_origin_remote(repo_with_tracked_feature, "git@gitlab.com:owner/repo.git")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with (
        patch("shortcake.commands.submit.get_github_token") as token_mock,
        patch("shortcake.commands.submit.GitHubClient") as client_mock,
    ):
        result = _submit(repo_with_tracked_feature, dry_run=True, stealth=True)

    assert result.stack_branches == ["feature"]
    assert len(result.branch_results) == 0
    token_mock.assert_not_called()
    client_mock.assert_not_called()

    captured = capsys.readouterr()
    assert "Would push 1 branch(es) without creating PRs" in captured.out
    assert "feature (push only)" in captured.out


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


def test_submit_stealth_pushes_without_github_api(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test stealth pushes branches without token lookup or PR API calls."""
    setup_origin_remote(repo_with_tracked_feature, "git@gitlab.com:owner/repo.git")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with (
        patch("shortcake.commands.submit.get_github_token") as token_mock,
        patch("shortcake.commands.submit.GitHubClient") as client_mock,
        patch(
            "shortcake.commands.submit.push_branch", return_value=(True, None)
        ) as push_mock,
    ):
        result = _submit(repo_with_tracked_feature, stealth=True)

    token_mock.assert_not_called()
    client_mock.assert_not_called()
    push_mock.assert_called_once_with(
        repo_with_tracked_feature, "feature", force_with_lease=True
    )

    assert len(result.branch_results) == 1
    assert result.branch_results[0].action == PRAction.PUSHED
    assert result.branch_results[0].pr_number is None


def test_submit_stealth_respects_force_flag(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test stealth forwards --force to push_branch."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    with patch(
        "shortcake.commands.submit.push_branch", return_value=(True, None)
    ) as push_mock:
        _submit(repo_with_tracked_feature, stealth=True, force=True)

    push_mock.assert_called_once_with(
        repo_with_tracked_feature, "feature", force_with_lease=False
    )


def test_submit_stealth_rejects_draft(
    repo_with_tracked_feature: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test stealth rejects draft mode because it does not create PRs."""
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    with pytest.raises(SubmitError, match="--draft cannot be used with --stealth"):
        _submit(repo_with_tracked_feature, draft=True, stealth=True)


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
        _submit(repo_with_stack, submit_stack=True)

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
    import re

    result = runner.invoke(app, ["submit", "--help"])

    assert result.exit_code == 0
    # Strip ANSI codes: CI terminals force color, which splits flag names
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "Submit through the current diff" in output
    assert "--stack" in output
    assert "--stealth" in output


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
    assert "1 PR created" in result.output


def test_cli_submit_stealth_success(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI submit --stealth only pushes branches."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature, "git@gitlab.com:owner/repo.git")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.get_github_token") as token_mock,
        patch("shortcake.commands.submit.GitHubClient") as client_mock,
    ):
        result = runner.invoke(app, ["submit", "--stealth"])

    assert result.exit_code == 0
    assert "1 branch pushed" in result.output
    assert "Created" not in result.output
    token_mock.assert_not_called()
    client_mock.assert_not_called()


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
    assert "1 updated" in result.output
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

    restack_mock.assert_called_once()
    assert restack_mock.call_args.args == (repo_with_tracked_feature,)
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
        _submit(repo_with_stack, submit_stack=True)

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


# --- submit --json ---


def test_cli_submit_json_creates_pr(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit --json emits the result envelope with PR info."""
    import json

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
        result = runner.invoke(app, ["submit", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["stack"] == ["feature"]
    branch = document["data"]["branches"][0]
    assert branch["action"] == "created"
    assert branch["pr"] == 123
    assert branch["url"] == "https://github.com/owner/repo/pull/123"
    assert branch["error"] is None


def test_cli_submit_json_dry_run_planned(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit --json --dry-run reports the plan without acting."""
    import json

    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        result = runner.invoke(app, ["submit", "--dry-run", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["planned"] == [
        {"branch": "feature", "action": "created", "pr": None}
    ]
    assert document["data"]["branches"] == []


def test_cli_submit_json_error_envelope(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit --json failures use the error envelope."""
    import json

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["submit", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "submit_failed"
    assert "No origin remote" in document["error"]["message"]


def test_cli_submit_json_stealth_push_failure(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test stealth push failures land in the envelope and exit 1."""
    import json

    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)

    with patch(
        "shortcake.commands.submit.push_branch",
        return_value=(False, "remote has diverged"),
    ):
        result = runner.invoke(app, ["submit", "--stealth", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    branch = document["data"]["branches"][0]
    assert branch["action"] == "skipped"
    assert branch["error"] == "remote has diverged"


def test_cli_submit_json_stealth_dry_run(
    repo_with_tracked_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test stealth dry-run --json reports push-only plan."""
    import json

    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)

    result = runner.invoke(app, ["submit", "--stealth", "--dry-run", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["planned"] == [
        {"branch": "feature", "action": "pushed", "pr": None}
    ]


def test_should_ask_scope_only_prompts_when_it_can_change_something() -> None:
    """No TTY, JSON output, or nothing extra to offer means no prompt."""
    stack = ["a", "b", "c"]
    down = ["a", "b"]
    ask = _should_ask_scope

    assert ask(
        stack, down, stack=False, json_output=False, interactive=True, forks=False
    )
    # a pipe or CI takes the flags at face value rather than hanging
    assert not ask(
        stack, down, stack=False, json_output=False, interactive=False, forks=False
    )
    assert not ask(
        stack, down, stack=False, json_output=True, interactive=True, forks=False
    )
    assert not ask(
        [], [], stack=False, json_output=False, interactive=True, forks=False
    )
    # nothing upstack to offer
    assert not ask(
        stack, stack, stack=False, json_output=False, interactive=True, forks=False
    )
    # --stack asks only on a fork, where it sweeps in a sibling arm
    assert not ask(
        stack, down, stack=True, json_output=False, interactive=True, forks=False
    )
    assert ask(stack, down, stack=True, json_output=False, interactive=True, forks=True)


def _footer_renderer(rows: list[StackRow]) -> StackRenderer:
    return StackRenderer(rows, "h", Console(width=100, height=40))


def test_submit_footer_reports_elapsed_once_it_is_meaningful() -> None:
    rows = [StackRow("main", state=RowState.BASE), StackRow("a", parent="main")]
    renderer = _footer_renderer(rows)
    renderer.started_at -= 42
    result = SubmitResult()
    result.branch_results = [
        BranchSubmitResult(branch="a", action=PRAction.CREATED, pr_number=1, pr_url="u")
    ]
    head = _submit_footer(result, renderer, draft=False, excluded=0)[0].plain
    assert "42s" in head


def test_submit_footer_lists_every_tip_when_the_stack_forks() -> None:
    """ "Top of stack" is meaningless with more than one leaf."""
    rows = [
        StackRow("main", state=RowState.BASE),
        StackRow("a", parent="main"),
        StackRow("b", parent="a"),
        StackRow("c", parent="a"),
    ]
    result = SubmitResult()
    result.branch_results = [
        BranchSubmitResult(
            branch=name, action=PRAction.CREATED, pr_number=index, pr_url=f"u{index}"
        )
        for index, name in enumerate(["a", "b", "c"], start=1)
    ]
    lines = [
        line.plain
        for line in _submit_footer(
            result, _footer_renderer(rows), draft=False, excluded=0
        )
    ]
    assert any("2 tips" in line for line in lines)
    assert any(line.strip().startswith("#2") for line in lines)
    assert any(line.strip().startswith("#3") for line in lines)
    assert not any("Top of stack" in line for line in lines)


def test_submit_footer_points_at_the_upstack_you_skipped() -> None:
    rows = [StackRow("main", state=RowState.BASE), StackRow("a", parent="main")]
    result = SubmitResult()
    result.branch_results = [
        BranchSubmitResult(branch="a", action=PRAction.CREATED, pr_number=1, pr_url="u")
    ]
    lines = [
        line.plain
        for line in _submit_footer(
            result, _footer_renderer(rows), draft=False, excluded=2
        )
    ]
    assert any("2 upstack branches not submitted" in line for line in lines)


def test_submit_with_explicit_branches_submits_just_that_arm(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The picker's "just my arm" is neither downstack nor the whole stack."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shortcake.commands.submit.GitHubClient", return_value=mock_client):
        result = _submit(
            repo_with_three_branch_stack,
            dry_run=True,
            explicit_branches=["branch_a", "branch_c"],
        )

    assert [plan.branch for plan in result.planned] == ["branch_a", "branch_c"]


def test_execution_rows_seeded_with_plan_labels_for_the_folded_block(
    repo_with_three_branch_stack: Repo,
) -> None:
    """The live block opens *as* the plan, so it is not printed twice."""
    plans = [
        BranchPlan(branch="branch_a", action=PRAction.CREATED),
        BranchPlan(branch="branch_b", action=PRAction.CREATED),
    ]
    rows, _ = _rows_for_execution(
        repo_with_three_branch_stack,
        ["branch_a", "branch_b", "branch_c"],
        ["branch_a", "branch_b"],
        "branch_b",
        plans=plans,
        draft=True,
    )
    labels = {row.branch: row.label.plain for row in rows}
    assert labels["branch_a"] == "create draft PR"
    assert labels["branch_c"] == "not submitted"

    # without plans the rows start blank, ready for progress
    rows, _ = _rows_for_execution(
        repo_with_three_branch_stack,
        ["branch_a", "branch_b", "branch_c"],
        ["branch_a", "branch_b"],
        "branch_b",
    )
    assert {row.label.plain for row in rows if row.state is RowState.PENDING} == {""}


def test_start_execution_switches_the_block_from_plan_to_progress() -> None:
    rows = [
        StackRow("main", state=RowState.BASE),
        StackRow("a", parent="main", state=RowState.PENDING, label=Text("create PR")),
    ]
    renderer = StackRenderer(
        rows, "Submit plan · 1 branch", Console(width=100, height=40), planning=True
    )
    _start_execution(renderer, "Submitting 1 branch to owner/repo")

    assert renderer.planning is False
    assert renderer.header == "Submitting 1 branch to owner/repo"
    assert rows[1].label.plain == ""


def test_cli_submit_prints_the_plan_once_when_the_block_is_folded(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a TTY the plan is the live block's first frame, not a second tree."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.return_value = PRInfo(
        number=1,
        url="https://github.com/owner/repo/pull/1",
        base="main",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(type(get_rich_toolkit().console), "is_terminal", True),
        patch("shortcake.commands.submit._is_interactive", return_value=False),
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["submit", "--stack"])

    assert result.exit_code == 0
    assert "Submit plan:" not in result.output


def test_plan_heading_states_count_and_draftness() -> None:
    assert _plan_heading(1, draft=False) == "Submit plan · 1 branch"
    assert _plan_heading(3, draft=True) == "Submit plan · 3 branches · draft"


def test_cli_submit_stealth_folds_the_plan_into_the_live_block(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--stealth gets the same single block: plan first, then progress."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_tracked_feature)

    with (
        patch.object(type(get_rich_toolkit().console), "is_terminal", True),
        patch("shortcake.commands.submit._is_interactive", return_value=False),
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
    ):
        result = runner.invoke(app, ["submit", "--stealth"])

    assert result.exit_code == 0
    assert "Push plan:" not in result.output
    assert "1 branch pushed" in result.output


def test_build_branch_plans_reports_each_lookup(
    repo_with_three_branch_stack: Repo,
) -> None:
    """One API call per branch, so the caller can show the tree filling in."""
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False

    seen: list[tuple[str, bool]] = []
    _build_branch_plans(
        repo_with_three_branch_stack,
        mock_client,
        get_rich_toolkit(),
        ["branch_a", "branch_b"],
        ["branch_a", "branch_b", "branch_c"],
        progress=lambda branch, plan: seen.append((branch, plan is not None)),
    )

    # each branch is announced before its lookup and again once resolved
    assert seen == [
        ("branch_a", False),
        ("branch_a", True),
        ("branch_b", False),
        ("branch_b", True),
    ]


def test_planning_tally_does_not_flicker_while_branches_are_checked() -> None:
    """An ACTIVE row is still selected; the count must not tick down."""
    rows = [
        StackRow("main", state=RowState.BASE),
        StackRow("a", parent="main", state=RowState.PENDING),
        StackRow("b", parent="a", state=RowState.PENDING),
    ]
    renderer = StackRenderer(
        rows, "Submit plan", Console(width=100, height=40), planning=True
    )
    before = renderer.progress_footer().plain
    rows[1].state = RowState.ACTIVE
    assert renderer.progress_footer().plain == before == "  ● 2 selected"


def test_submit_folds_precomputed_plans_into_the_block(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plans handed in already still populate the block's first frame."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.create_pr.return_value = PRInfo(
        number=9,
        url="https://github.com/owner/repo/pull/9",
        base="main",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    precomputed = [
        BranchPlan(branch="branch_a", action=PRAction.CREATED, parent="main"),
        BranchPlan(branch="branch_b", action=PRAction.CREATED, parent="branch_a"),
    ]

    with (
        patch("shortcake.commands.submit.push_branch", return_value=(True, None)),
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
    ):
        result = _submit(
            repo_with_three_branch_stack,
            precomputed_plans=precomputed,
            fold_plan=True,
        )

    assert [r.branch for r in result.branch_results] == ["branch_a", "branch_b"]
    # the plans were reused rather than re-fetched per branch
    assert mock_client.get_pr_for_branch.call_count <= len(precomputed)


def _capturing_picker(captured: dict, scope: str = "downstack"):
    """pick_scope stub that runs load_plans, as the real picker does."""

    def fake(console, rows, header, downstack, *, stack, labels=None, load_plans=None):
        seen: list[dict[str, RowState]] = []
        load_plans(lambda: seen.append({r.branch: r.state for r in rows}))
        captured["frames"] = seen
        captured["labels"] = {name: text.plain for name, text in labels.items()}
        captured["final"] = {r.branch: r.state for r in rows}
        return scope

    return fake


def test_ask_scope_looks_up_plans_with_the_tree_already_drawn(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each row shows "checking…" and settles, instead of a blank screen."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)
    monkeypatch.setenv("GH_TOKEN", "test-token")

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_pr_for_branch.return_value = None
    mock_client.has_merged_pr.return_value = False
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    captured: dict = {}
    with (
        patch("shortcake.commands.submit.GitHubClient", return_value=mock_client),
        patch("shortcake.commands.submit.pick_scope", _capturing_picker(captured)),
    ):
        scope, selected = _ask_scope(
            repo_with_three_branch_stack,
            get_rich_toolkit(),
            ["branch_a", "branch_b", "branch_c"],
            ["branch_a", "branch_b"],
            "branch_b",
            stack=False,
            stealth=False,
            draft=False,
        )

    assert scope == "downstack"
    assert selected == ["branch_a", "branch_b"]
    # every branch was announced as in-flight at some point during the load
    assert any(RowState.ACTIVE in frame.values() for frame in captured["frames"])
    # and nothing is left mid-lookup
    assert RowState.ACTIVE not in captured["final"].values()
    # an excluded row keeps its excluded label rather than a plan label
    assert captured["final"]["branch_c"] is RowState.EXCLUDED
    assert set(captured["labels"]) == {"branch_a", "branch_b", "branch_c"}


def test_ask_scope_stealth_needs_no_github_lookup(
    repo_with_three_branch_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--stealth pushes only, so there is nothing to ask GitHub about."""
    monkeypatch.chdir(tmp_path)
    setup_origin_remote(repo_with_three_branch_stack)

    captured: dict = {}
    with (
        patch("shortcake.commands.submit.GitHubClient") as client,
        patch(
            "shortcake.commands.submit.pick_scope", _capturing_picker(captured, "stack")
        ),
    ):
        scope, _ = _ask_scope(
            repo_with_three_branch_stack,
            get_rich_toolkit(),
            ["branch_a", "branch_b", "branch_c"],
            ["branch_a", "branch_b"],
            "branch_b",
            stack=True,
            stealth=True,
            draft=False,
        )

    assert scope == "stack"
    client.assert_not_called()
    assert set(captured["labels"].values()) == {"push only"}

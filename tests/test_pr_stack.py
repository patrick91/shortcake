"""Tests for _pr_stack module."""

from unittest.mock import MagicMock, patch

import httpx

from shortcake._github import GitHubClient, PRInfo, PullRequestStackMembership
from shortcake._pr_stack import (
    STACK_END_MARKER,
    STACK_START_MARKER,
    _remove_stack_pr_descriptions,
    _remove_stack_section,
    _sync_pr_descriptions_for_branches,
    _sync_stack_pr_descriptions,
)
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_branch_head,
    switch_branch,
)


def _make_mock_gh() -> MagicMock:
    mock = MagicMock(spec=GitHubClient)
    mock.get_pr_for_branch.return_value = None
    mock.get_merged_pr_number.return_value = None
    mock.get_merged_pr_base.return_value = None
    return mock


def _membership(position: int = 1) -> PullRequestStackMembership:
    return PullRequestStackMembership(
        id=100,
        number=7,
        size=2,
        position=position,
        base_ref="main",
        base_sha="abc",
    )


def test_remove_managed_stack_section_preserves_user_body() -> None:
    body = (
        f"{STACK_START_MARKER}\n## Stack\n\n- #1 (`a`)\n"
        f"{STACK_END_MARKER}\n\nUser body\n"
    )

    assert _remove_stack_section(body) == "User body\n"
    assert _remove_stack_section("  User body  ") == "  User body  "


def test_remove_stack_descriptions_updates_only_managed_bodies() -> None:
    gh = _make_mock_gh()
    managed = PRInfo(
        1,
        "url",
        "main",
        "title",
        f"{STACK_START_MARKER}\nmap\n{STACK_END_MARKER}\n\nDescription",
        "open",
        False,
    )
    untouched = PRInfo(2, "url", "main", "title", "Description", "open", False)

    _remove_stack_pr_descriptions(gh, [managed, untouched])

    gh.update_pr.assert_called_once_with(1, body="Description")


def test_native_stack_defers_base_sync_and_removes_body_map(
    repo_with_stack: Repo,
) -> None:
    gh = _make_mock_gh()
    pr_a = PRInfo(
        1,
        "url",
        "wrong-base",
        "title",
        f"{STACK_START_MARKER}\nmap\n{STACK_END_MARKER}",
        "open",
        False,
        stack=_membership(),
    )
    pr_b = PRInfo(
        2,
        "url",
        "branch_a",
        "title",
        "",
        "open",
        False,
        stack=_membership(2),
    )
    gh.get_pr_for_branch.side_effect = lambda branch: {
        "branch_a": pr_a,
        "branch_b": pr_b,
    }.get(branch)

    pending = _sync_stack_pr_descriptions(
        repo_with_stack,
        gh,
        "owner",
        ["branch_a", "branch_b"],
        sync_bases=True,
    )

    assert pending is True
    gh.update_pr.assert_called_once_with(1, body="")
    assert not any("base" in call.kwargs for call in gh.update_pr.call_args_list)


def test_sync_stack_pr_descriptions_empty_branches(
    repo_with_stack: Repo,
) -> None:
    """No-op when stack_branches is empty."""
    gh = _make_mock_gh()
    _sync_stack_pr_descriptions(repo_with_stack, gh, "owner", [])
    gh.get_pr_for_branch.assert_not_called()


def test_sync_bases_skips_branch_with_no_parent(
    temp_repo: Repo, tmp_path: Repo
) -> None:
    """sync_bases skips branches whose parent trailer is missing."""
    # Create a branch with no Shortcake-Parent trailer
    create_branch(
        temp_repo, "untracked", get_branch_head(temp_repo, "main"), checkout=True
    )
    commit_files(temp_repo, {tmp_path / "u.txt": "content"}, "untracked commit")

    gh = _make_mock_gh()
    pr = PRInfo(
        number=1,
        url="url",
        base="main",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    gh.get_pr_for_branch.return_value = pr

    _sync_stack_pr_descriptions(temp_repo, gh, "owner", ["untracked"], sync_bases=True)
    # update_pr should only be called for body, not for base
    for call in gh.update_pr.call_args_list:
        assert "base" not in call[1]


def test_sync_bases_resolves_merged_parent(
    repo_with_stack: Repo,
) -> None:
    """sync_bases resolves a parent that was merged into another branch."""
    switch_branch(repo_with_stack, "branch_a")
    gh = _make_mock_gh()

    pr_b = PRInfo(
        number=20,
        url="url",
        base="branch_a",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    # branch_a has parent "main" via trailer so it's in all_branches.
    # To trigger merged_base lookup, we need a branch whose parent is NOT
    # in all_branches. Simulate by patching get_branch_parent.
    pr_orphan = PRInfo(
        number=30,
        url="url",
        base="deleted-branch",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    gh.get_pr_for_branch.side_effect = lambda b: {
        "branch_a": pr_b,
        "orphan": pr_orphan,
    }.get(b)
    gh.get_merged_pr_base.return_value = "main"

    with patch("shortcake._pr_stack.git") as mock_git:
        mock_git.get_all_local_branches.return_value = ["main", "branch_a"]
        mock_git.get_branch_parent.side_effect = lambda repo, b, ab: {
            "branch_a": "main",
            "orphan": "deleted-branch",
        }.get(b)

        _sync_stack_pr_descriptions(
            repo_with_stack,
            gh,
            "owner",
            ["branch_a", "orphan"],
            sync_bases=True,
        )

    # orphan's base should have been updated to "main" (resolved from merged parent)
    base_updates = [
        c for c in gh.update_pr.call_args_list if c[1].get("base") == "main"
    ]
    assert base_updates


def test_sync_bases_merged_parent_lookup_error_is_non_fatal(
    repo_with_stack: Repo,
) -> None:
    """Error looking up merged parent base is caught gracefully."""
    switch_branch(repo_with_stack, "branch_a")
    gh = _make_mock_gh()

    pr_orphan = PRInfo(
        number=30,
        url="url",
        base="old-base",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    gh.get_pr_for_branch.side_effect = lambda b: {"orphan": pr_orphan}.get(b)
    gh.get_merged_pr_base.side_effect = httpx.RequestError("network down")

    with patch("shortcake._pr_stack.git") as mock_git:
        mock_git.get_all_local_branches.return_value = ["main", "branch_a"]
        mock_git.get_branch_parent.return_value = "deleted-branch"

        # Should not raise despite network error on merged_base lookup
        _sync_stack_pr_descriptions(
            repo_with_stack,
            gh,
            "owner",
            ["orphan"],
            sync_bases=True,
        )

    # Base updated to unresolved "deleted-branch" (since merged_base lookup failed)
    base_updates = [
        c for c in gh.update_pr.call_args_list if c[1].get("base") is not None
    ]
    assert base_updates
    assert base_updates[0][1]["base"] == "deleted-branch"


def test_sync_finds_merged_pr_numbers(
    repo_with_stack: Repo,
) -> None:
    """Branches without open PRs get their merged PR number looked up."""
    switch_branch(repo_with_stack, "branch_a")
    gh = _make_mock_gh()

    pr_a = PRInfo(
        number=10,
        url="url",
        base="main",
        title="t",
        body="body",
        state="open",
        is_draft=False,
    )
    # branch_b has no open PR and no known PR number
    gh.get_pr_for_branch.side_effect = lambda b: {
        "branch_a": pr_a,
    }.get(b)
    gh.get_merged_pr_number.side_effect = lambda b: {
        "branch_b": 99,
    }.get(b)

    _sync_stack_pr_descriptions(
        repo_with_stack,
        gh,
        "owner",
        ["branch_a", "branch_b"],
    )

    # The body update for branch_a should include branch_b's merged PR
    body_updates = [
        c for c in gh.update_pr.call_args_list if c[1].get("body") is not None
    ]
    assert body_updates
    body = body_updates[-1][1]["body"]
    assert "#99 (merged)" in body


def test_sync_handles_new_branch_not_in_historical_positions(
    repo_with_stack: Repo,
) -> None:
    """Branches added after the historical stack are handled in ordering."""
    switch_branch(repo_with_stack, "branch_a")
    gh = _make_mock_gh()

    # PR body has historical stack with branch_a only.
    # branch_b is a new local branch not in historical positions.
    stack_body = (
        f"{STACK_START_MARKER}\n"
        "## Stack\n"
        "\n"
        "- #5 (merged) (`old_branch`)\n"
        "- **#10** (`branch_a`) <-- this PR\n"
        f"{STACK_END_MARKER}"
    )
    pr_a = PRInfo(
        number=10,
        url="url",
        base="main",
        title="t",
        body=stack_body,
        state="open",
        is_draft=False,
    )
    pr_b = PRInfo(
        number=20,
        url="url",
        base="branch_a",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    gh.get_pr_for_branch.side_effect = lambda b: {
        "branch_a": pr_a,
        "branch_b": pr_b,
    }.get(b)

    _sync_stack_pr_descriptions(
        repo_with_stack,
        gh,
        "owner",
        ["branch_a", "branch_b"],
    )

    # branch_b is new (not in historical positions), so the insertion loop
    # hits the `continue` on line 239. Verify both branches appear in output.
    body_updates = [
        c for c in gh.update_pr.call_args_list if c[1].get("body") is not None
    ]
    assert body_updates
    body = body_updates[-1][1]["body"]
    assert "branch_a" in body
    assert "branch_b" in body


def test_sync_places_confirmed_merged_historical_branch_below_active_stack(
    repo_with_stack: Repo,
) -> None:
    """Historical merged branches are kept below the active stack."""
    switch_branch(repo_with_stack, "branch_a")
    gh = _make_mock_gh()

    stack_body = (
        f"{STACK_START_MARKER}\n"
        "## Stack\n"
        "\n"
        "- #30 (`old-merged-branch`)\n"
        "- **#10** (`branch_a`) <-- this PR\n"
        f"{STACK_END_MARKER}"
    )
    pr_a = PRInfo(
        number=10,
        url="url",
        base="main",
        title="t",
        body=stack_body,
        state="open",
        is_draft=False,
    )
    pr_b = PRInfo(
        number=20,
        url="url",
        base="branch_a",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )

    gh.get_pr_for_branch.side_effect = lambda b: {
        "branch_a": pr_a,
        "branch_b": pr_b,
    }.get(b)
    gh.get_merged_pr_number.side_effect = lambda b: {
        "old-merged-branch": 30,
    }.get(b)

    _sync_stack_pr_descriptions(
        repo_with_stack,
        gh,
        "owner",
        ["branch_a", "branch_b"],
    )

    body_updates = [
        c for c in gh.update_pr.call_args_list if c[1].get("body") is not None
    ]
    assert body_updates
    body = body_updates[-1][1]["body"]
    assert "branch_a" in body
    assert "branch_b" in body
    assert "#30 (merged)" in body
    assert "`old-merged-branch`" in body

    branch_b_pos = body.find("`branch_b`")
    branch_a_pos = body.find("`branch_a`")
    merged_pos = body.find("`old-merged-branch`")
    assert branch_b_pos < branch_a_pos < merged_pos


def test_sync_inserts_historical_branch_after_lookup_error(
    repo_with_stack: Repo,
) -> None:
    """Historical branch lookup errors fall back to parsed PR info."""
    switch_branch(repo_with_stack, "branch_a")
    gh = _make_mock_gh()

    stack_body = (
        f"{STACK_START_MARKER}\n"
        "## Stack\n"
        "\n"
        "- #20 (`branch_b`)\n"
        "- #30 (`old_branch`)\n"
        f"{STACK_END_MARKER}"
    )
    pr_a = PRInfo(
        number=10,
        url="url",
        base="main",
        title="t",
        body=stack_body,
        state="open",
        is_draft=False,
    )
    pr_b = PRInfo(
        number=20,
        url="url",
        base="branch_a",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    gh.get_pr_for_branch.side_effect = lambda b: {
        "branch_a": pr_a,
        "branch_b": pr_b,
    }.get(b)

    def get_merged_pr_number(branch: str) -> int | None:
        if branch == "old_branch":
            raise httpx.RequestError("network down")
        return None

    gh.get_merged_pr_number.side_effect = get_merged_pr_number

    _sync_stack_pr_descriptions(
        repo_with_stack,
        gh,
        "owner",
        ["branch_a", "branch_b"],
    )

    body_updates = [
        c for c in gh.update_pr.call_args_list if c[1].get("body") is not None
    ]
    assert body_updates
    body = body_updates[-1][1]["body"]
    branch_b_pos = body.find("`branch_b`")
    old_pos = body.find("`old_branch`")
    branch_a_pos = body.find("`branch_a`")
    assert branch_b_pos < old_pos < branch_a_pos
    assert "#30" in body


def test_sync_pr_descriptions_for_branches_deduplicates_stacks(
    repo_with_stack: Repo,
) -> None:
    """Duplicate stacks from multiple branches are synced only once."""
    switch_branch(repo_with_stack, "branch_a")
    gh = _make_mock_gh()

    pr_a = PRInfo(
        number=10,
        url="url",
        base="main",
        title="t",
        body="",
        state="open",
        is_draft=False,
    )
    gh.get_pr_for_branch.side_effect = lambda b: {"branch_a": pr_a}.get(b)

    # Both branch_a and branch_b are in the same stack, so syncing both
    # should only call _sync_stack_pr_descriptions once.
    _sync_pr_descriptions_for_branches(
        repo_with_stack,
        gh,
        "owner",
        ["branch_a", "branch_b"],
    )

    # get_pr_for_branch is called once per branch in the stack (from
    # _sync_stack_pr_descriptions), not twice.
    branch_lookups = [c[0][0] for c in gh.get_pr_for_branch.call_args_list]
    # Should see branch_a and branch_b exactly once each (from a single sync)
    assert branch_lookups.count("branch_a") == 1
    assert branch_lookups.count("branch_b") == 1

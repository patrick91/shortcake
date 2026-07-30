"""Tests for materializing GitHub-native stacks during checkout."""

from pathlib import Path
from unittest.mock import patch

import pytest

from shortcake import _git as git
from shortcake._git import RebaseResult
from shortcake._github import (
    NativeStack,
    NativeStackPullRequest,
    PRInfo,
)
from shortcake._native_stack_checkout import (
    NativeStackCheckoutError,
    _delete_created_refs,
    checkout_native_stack,
)
from shortcake._restack_state import RestackState
from tests._git_helpers import (
    Repo,
    commit_files,
    create_branch,
    get_branch_head,
    get_ref,
    set_ref,
    set_remote,
    switch_branch,
)


def _native_stack(
    branches: list[tuple[int, str, bytes]],
    *,
    base: str = "main",
) -> NativeStack:
    return NativeStack(
        id=1,
        number=7,
        node_id="STACK_1",
        url="https://api.github.com/repos/owner/repo/stacks/7",
        base_ref=base,
        is_open=True,
        created_at="2026-07-30T00:00:00Z",
        pull_requests=tuple(
            NativeStackPullRequest(
                number=number,
                state="open",
                is_draft=False,
                merged_at=None,
                head_ref=branch,
                head_sha=sha.decode(),
            )
            for number, branch, sha in branches
        ),
    )


def _pr(number: int, branch: str) -> PRInfo:
    return PRInfo(
        number=number,
        url=f"https://github.com/owner/repo/pull/{number}",
        base="main",
        title=branch,
        body="",
        state="open",
        is_draft=False,
        head_ref=branch,
    )


def _configure_remote(repo: Repo) -> None:
    set_remote(repo, "origin", "git@github.com:owner/repo.git")


def _set_remote_refs(repo: Repo, branches: list[str]) -> None:
    for branch in branches:
        set_ref(
            repo,
            f"refs/remotes/origin/{branch}",
            get_branch_head(repo, branch),
        )


def test_checkout_remote_only_native_stack(
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    """Missing branches are created, rebased, and given parent trailers."""
    create_branch(
        temp_repo,
        "lower",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    commit_files(temp_repo, {tmp_path / "lower.txt": "lower"}, "lower")
    lower_sha = get_branch_head(temp_repo, "lower")
    create_branch(temp_repo, "upper", lower_sha, checkout=True)
    commit_files(temp_repo, {tmp_path / "upper.txt": "upper"}, "upper")
    upper_sha = get_branch_head(temp_repo, "upper")

    _configure_remote(temp_repo)
    _set_remote_refs(temp_repo, ["main", "lower", "upper"])
    switch_branch(temp_repo, "main")
    git.delete_branch(temp_repo, "lower")
    git.delete_branch(temp_repo, "upper")
    native = _native_stack([(11, "lower", lower_sha), (12, "upper", upper_sha)])

    with patch("shortcake._native_stack_checkout.fetch_remote", return_value=True):
        result = checkout_native_stack(temp_repo, _pr(12, "upper"), native)

    assert result.stack_number == 7
    assert result.created_branches == ["lower", "upper"]
    assert result.rewritten_branches == ["lower", "upper"]
    assert git.get_current_branch(temp_repo) == "upper"
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "lower", all_branches) == "main"
    assert git.get_branch_parent(temp_repo, "upper", all_branches) == "lower"
    assert not RestackState.exists(temp_repo)

    with patch("shortcake._native_stack_checkout.fetch_remote", return_value=True):
        repeated = checkout_native_stack(temp_repo, _pr(12, "upper"), native)

    assert repeated.created_branches == []
    assert repeated.rewritten_branches == []


def test_checkout_already_tracked_stack_is_noop(
    repo_with_stack: Repo,
) -> None:
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["main", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ]
    )

    with patch("shortcake._native_stack_checkout.fetch_remote", return_value=True):
        result = checkout_native_stack(repo_with_stack, _pr(12, "branch_b"), native)

    assert result.rewritten_branches == []
    assert result.created_branches == []
    assert git.get_current_branch(repo_with_stack) == "branch_b"


def test_delete_created_refs_rolls_back_in_reverse(temp_repo: Repo) -> None:
    main_sha = get_branch_head(temp_repo, "main")
    create_branch(temp_repo, "created-a", main_sha)
    create_branch(temp_repo, "created-b", main_sha)

    _delete_created_refs(temp_repo, ["created-a", "created-b"])

    assert not git.branch_exists(temp_repo, "created-a")
    assert not git.branch_exists(temp_repo, "created-b")


def test_checkout_rolls_back_created_refs_when_checkout_fails(
    repo_with_stack: Repo,
) -> None:
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["main", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ]
    )

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        patch(
            "shortcake._native_stack_checkout.git.switch_branch",
            side_effect=ValueError("checked out elsewhere"),
        ),
        pytest.raises(NativeStackCheckoutError, match="checked out elsewhere"),
    ):
        checkout_native_stack(repo_with_stack, _pr(12, "branch_b"), native)


def test_checkout_reparent_requires_force(
    repo_with_stack: Repo,
) -> None:
    main_sha = get_branch_head(repo_with_stack, "main")
    create_branch(repo_with_stack, "release", main_sha)
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["release", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ],
        base="release",
    )

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        pytest.raises(NativeStackCheckoutError, match="Re-run with --force"),
    ):
        checkout_native_stack(repo_with_stack, _pr(12, "branch_b"), native)

    with patch("shortcake._native_stack_checkout.fetch_remote", return_value=True):
        result = checkout_native_stack(
            repo_with_stack,
            _pr(12, "branch_b"),
            native,
            force=True,
        )

    assert result.rewritten_branches == ["branch_a", "branch_b"]
    all_branches = set(git.get_all_local_branches(repo_with_stack))
    assert git.get_branch_parent(repo_with_stack, "branch_a", all_branches) == "release"


def test_checkout_keeps_state_when_final_checkout_fails(
    repo_with_stack: Repo,
) -> None:
    main_sha = get_branch_head(repo_with_stack, "main")
    create_branch(repo_with_stack, "release", main_sha)
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["release", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ],
        base="release",
    )

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        patch(
            "shortcake._native_stack_checkout._rebase_branch",
            return_value=RebaseResult(True),
        ),
        patch("shortcake._native_stack_checkout._update_branch_trailer"),
        patch(
            "shortcake._native_stack_checkout.git.switch_branch",
            side_effect=ValueError("checked out elsewhere"),
        ),
        pytest.raises(NativeStackCheckoutError, match="checked out elsewhere"),
    ):
        checkout_native_stack(
            repo_with_stack,
            _pr(12, "branch_b"),
            native,
            force=True,
        )

    assert RestackState.exists(repo_with_stack)


def test_checkout_preserves_local_changes_in_an_already_tracked_stack(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["main", "branch_a", "branch_b"])
    remote_a = get_ref(repo_with_stack, "refs/remotes/origin/branch_a")
    switch_branch(repo_with_stack, "branch_a")
    commit_files(repo_with_stack, {tmp_path / "local.txt": "local"}, "local")
    native = _native_stack(
        [
            (11, "branch_a", remote_a),
            (
                12,
                "branch_b",
                get_ref(repo_with_stack, "refs/remotes/origin/branch_b"),
            ),
        ]
    )

    with patch("shortcake._native_stack_checkout.fetch_remote", return_value=True):
        result = checkout_native_stack(repo_with_stack, _pr(12, "branch_b"), native)

    assert result.rewritten_branches == []
    assert git.get_current_branch(repo_with_stack) == "branch_b"


def test_checkout_rejects_divergent_branch_that_needs_reparenting(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    main_sha = get_branch_head(repo_with_stack, "main")
    create_branch(repo_with_stack, "release", main_sha)
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["release", "branch_a", "branch_b"])
    remote_a = get_ref(repo_with_stack, "refs/remotes/origin/branch_a")
    switch_branch(repo_with_stack, "branch_a")
    commit_files(repo_with_stack, {tmp_path / "local.txt": "local"}, "local")
    native = _native_stack(
        [
            (11, "branch_a", remote_a),
            (
                12,
                "branch_b",
                get_ref(repo_with_stack, "refs/remotes/origin/branch_b"),
            ),
        ],
        base="release",
    )

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        pytest.raises(NativeStackCheckoutError, match="differs from 'origin/branch_a'"),
    ):
        checkout_native_stack(
            repo_with_stack,
            _pr(12, "branch_b"),
            native,
            force=True,
        )


def test_checkout_reports_fetch_failure(
    repo_with_stack: Repo,
) -> None:
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["main", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ]
    )

    with (
        patch(
            "shortcake._native_stack_checkout.fetch_remote",
            return_value=False,
        ),
        pytest.raises(NativeStackCheckoutError, match="Failed to fetch"),
    ):
        checkout_native_stack(repo_with_stack, _pr(12, "branch_b"), native)


def test_checkout_conflict_keeps_restack_state(
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    create_branch(
        temp_repo,
        "lower",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    commit_files(temp_repo, {tmp_path / "lower.txt": "lower"}, "lower")
    lower_sha = get_branch_head(temp_repo, "lower")
    create_branch(temp_repo, "upper", lower_sha, checkout=True)
    commit_files(temp_repo, {tmp_path / "upper.txt": "upper"}, "upper")
    upper_sha = get_branch_head(temp_repo, "upper")
    _configure_remote(temp_repo)
    _set_remote_refs(temp_repo, ["main", "lower", "upper"])
    native = _native_stack([(11, "lower", lower_sha), (12, "upper", upper_sha)])

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        patch(
            "shortcake._native_stack_checkout._rebase_branch",
            return_value=RebaseResult(False, error_output="stopped"),
        ),
    ):
        result = checkout_native_stack(temp_repo, _pr(12, "upper"), native)

    assert result.conflict_branch == "lower"
    state = RestackState.load(temp_repo)
    assert state is not None
    assert state.completion_branch == "upper"


@pytest.mark.parametrize(
    ("patched", "message"),
    [
        ("current", "detached HEAD"),
        ("dirty", "uncommitted changes"),
        ("rebase", "rebase in progress"),
        ("state", "Restack already in progress"),
        ("remote", "No remote 'origin'"),
    ],
)
def test_checkout_preconditions(
    repo_with_stack: Repo,
    patched: str,
    message: str,
) -> None:
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["main", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ]
    )
    patches = {
        "current": patch(
            "shortcake._native_stack_checkout.git.get_current_branch",
            return_value=None,
        ),
        "dirty": patch(
            "shortcake._native_stack_checkout.git.has_uncommitted_changes",
            return_value=True,
        ),
        "rebase": patch(
            "shortcake._native_stack_checkout.git.is_rebase_in_progress",
            return_value=True,
        ),
        "state": patch(
            "shortcake._native_stack_checkout.RestackState.exists",
            return_value=True,
        ),
        "remote": patch(
            "shortcake._native_stack_checkout.git.has_remote",
            return_value=False,
        ),
    }

    with patches[patched], pytest.raises(NativeStackCheckoutError, match=message):
        checkout_native_stack(repo_with_stack, _pr(12, "branch_b"), native)


def test_checkout_rejects_stack_without_open_prs(
    repo_with_stack: Repo,
) -> None:
    _configure_remote(repo_with_stack)
    native = _native_stack(
        [(11, "branch_a", get_branch_head(repo_with_stack, "branch_a"))]
    )
    closed = native.pull_requests[0]
    native = NativeStack(
        **{
            **native.__dict__,
            "pull_requests": (
                NativeStackPullRequest(
                    number=closed.number,
                    state="closed",
                    is_draft=False,
                    merged_at="2026-07-30T12:00:00Z",
                    head_ref=closed.head_ref,
                    head_sha=closed.head_sha,
                ),
            ),
        }
    )

    with pytest.raises(NativeStackCheckoutError, match="has no open pull requests"):
        checkout_native_stack(repo_with_stack, _pr(12, "branch_a"), native)


def test_checkout_reports_missing_remote_branch(
    repo_with_stack: Repo,
) -> None:
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["main", "branch_a"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ]
    )

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        pytest.raises(NativeStackCheckoutError, match=r"origin/branch_b.*not found"),
    ):
        checkout_native_stack(repo_with_stack, _pr(12, "branch_b"), native)


def test_checkout_selects_top_when_requested_pr_is_not_open(
    repo_with_stack: Repo,
) -> None:
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["main", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ]
    )

    with patch("shortcake._native_stack_checkout.fetch_remote", return_value=True):
        checkout_native_stack(repo_with_stack, _pr(10, "already-merged"), native)

    assert git.get_current_branch(repo_with_stack) == "branch_b"


@pytest.mark.parametrize(
    ("helper", "message"),
    [
        ("merge_base", "shares no history"),
        ("ancestor", "is not based on"),
    ],
)
def test_checkout_validates_native_branch_history(
    repo_with_stack: Repo,
    helper: str,
    message: str,
) -> None:
    main_sha = get_branch_head(repo_with_stack, "main")
    create_branch(repo_with_stack, "release", main_sha)
    _configure_remote(repo_with_stack)
    _set_remote_refs(repo_with_stack, ["release", "branch_a", "branch_b"])
    native = _native_stack(
        [
            (11, "branch_a", get_branch_head(repo_with_stack, "branch_a")),
            (12, "branch_b", get_branch_head(repo_with_stack, "branch_b")),
        ],
        base="release",
    )
    target = (
        "shortcake._native_stack_checkout.git.get_merge_base"
        if helper == "merge_base"
        else "shortcake._native_stack_checkout.git.is_ancestor"
    )

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        patch(target, return_value=False if helper == "ancestor" else None),
        pytest.raises(NativeStackCheckoutError, match=message),
    ):
        checkout_native_stack(
            repo_with_stack,
            _pr(12, "branch_b"),
            native,
            force=True,
        )


def test_checkout_conflict_path_shows_resolution_instructions(
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    create_branch(
        temp_repo,
        "lower",
        get_branch_head(temp_repo, "main"),
        checkout=True,
    )
    commit_files(temp_repo, {tmp_path / "lower.txt": "lower"}, "lower")
    lower_sha = get_branch_head(temp_repo, "lower")
    create_branch(temp_repo, "upper", lower_sha, checkout=True)
    commit_files(temp_repo, {tmp_path / "upper.txt": "upper"}, "upper")
    upper_sha = get_branch_head(temp_repo, "upper")
    _configure_remote(temp_repo)
    _set_remote_refs(temp_repo, ["main", "lower", "upper"])
    native = _native_stack([(11, "lower", lower_sha), (12, "upper", upper_sha)])

    with (
        patch("shortcake._native_stack_checkout.fetch_remote", return_value=True),
        patch(
            "shortcake._native_stack_checkout._rebase_branch",
            return_value=RebaseResult(False, conflict=True),
        ),
        patch(
            "shortcake._native_stack_checkout.git.is_rebase_in_progress",
            side_effect=[False, True],
        ),
    ):
        result = checkout_native_stack(temp_repo, _pr(12, "upper"), native)

    assert result.conflict_branch == "lower"

"""Tests for `sc sync`'s cleanup review.

The load-bearing behaviour: it must not report a branch as safe to delete when
the local copy is the only one left, and every figure it shows must name what
it applies to.
"""

from unittest.mock import patch

import pytest
from rich.console import Console
from rich.live import Live

from shortcake.commands._sync_review import (
    CLOSED,
    MERGED,
    SQUASH_MERGED,
    StaleBranch,
    breakdown,
    is_lossy,
    pick_cleanup,
    reason_label,
    render_review,
    review_options,
    selected_branches,
)


def merged(name: str = "a", **kwargs) -> StaleBranch:
    return StaleBranch(branch=name, reason=MERGED, **kwargs)


def closed(name: str = "c", *, pushed: bool = True, **kwargs) -> StaleBranch:
    return StaleBranch(branch=name, reason=CLOSED, pr=4675, pushed=pushed, **kwargs)


def plain(renderable) -> str:
    console = Console(width=100)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


# -- safety -----------------------------------------------------------


def test_only_a_closed_pr_with_no_remote_copy_loses_work() -> None:
    """Merged is safe; closed-but-pushed is recoverable; closed-and-gone is not."""
    assert not is_lossy(merged())
    assert not is_lossy(StaleBranch("b", SQUASH_MERGED, pr=1))
    assert not is_lossy(closed(pushed=True))
    assert is_lossy(closed(pushed=False))


def test_merged_branch_is_safe_even_without_a_remote_copy() -> None:
    """The commits are in the trunk, so the local branch is redundant."""
    assert not is_lossy(merged(pushed=False))


def test_safe_only_option_appears_only_when_something_would_be_lost() -> None:
    scopes = lambda stale: [s for s, _ in review_options(stale)]  # noqa: E731

    assert scopes([merged("a"), merged("b")]) == ["all", "none", "cancel"]
    assert scopes([merged("a"), closed(pushed=True)]) == ["all", "none", "cancel"]
    assert scopes([merged("a"), closed(pushed=False)]) == [
        "all",
        "safe",
        "none",
        "cancel",
    ]
    # nothing safe to fall back to
    assert scopes([closed(pushed=False)]) == ["all", "none", "cancel"]


def test_option_label_reads_naturally_for_one_branch() -> None:
    assert review_options([merged()])[0][1] == "Delete it"
    assert review_options([merged("a"), merged("b")])[0][1] == "Delete all 2"


def test_selected_branches_per_scope() -> None:
    stale = [merged("a"), closed("c", pushed=False)]
    assert selected_branches(stale, "all") == ["a", "c"]
    assert selected_branches(stale, "safe") == ["a"]
    assert selected_branches(stale, "none") == []
    assert selected_branches(stale, "cancel") == []


# -- what it says -----------------------------------------------------


def test_reason_label_names_the_pr_and_the_worktree() -> None:
    assert reason_label(merged(), "main").plain == "merged into main"
    assert (
        reason_label(StaleBranch("b", SQUASH_MERGED, pr=42), "main").plain
        == "squash-merged · #42"
    )
    assert reason_label(closed(), "main").plain == "closed · #4675"
    assert (
        reason_label(merged(worktrees=["~/wt/a"]), "main").plain
        == "merged into main · worktree"
    )


def test_breakdown_names_what_each_action_applies_to() -> None:
    """No bare counts: "reparent 1" left you asking reparent what, onto what."""
    stale = [merged("alpha", worktrees=["~/wt/alpha"]), closed("gamma")]
    text = plain(breakdown(stale, ["beta"], "main")[0])
    lines = plain(
        render_review(stale, ["beta"], 0, trunk="main", target=None, trunk_note=None)
    )

    assert "Delete 2 local branches" in text or "Delete 2 local branches" in lines
    assert "alpha" in lines and "gamma" in lines
    assert "Remove 1 worktree" in lines
    assert "~/wt/alpha" in lines
    # reparenting is a note, not a section, and never a bare number
    assert "1 branch above is rebased onto its new parent." in lines


def test_breakdown_omits_sections_that_do_not_apply() -> None:
    lines = plain(
        render_review([merged("a")], [], 0, trunk="main", target=None, trunk_note=None)
    )
    assert "Remove" not in lines
    assert "rebased" not in lines


def test_review_escalates_the_question_only_when_work_would_be_lost() -> None:
    safe = plain(
        render_review([merged("a")], [], 0, trunk="main", target=None, trunk_note=None)
    )
    assert "Delete the local copies?" in safe
    assert "gone for good" not in safe

    risky = plain(
        render_review(
            [merged("a"), closed("c", pushed=False)],
            [],
            0,
            trunk="main",
            target=None,
            trunk_note=None,
        )
    )
    assert "1 branch would be gone for good." in risky
    assert "⚠ not on origin" in risky


def test_review_header_states_the_repo_and_the_trunk_result() -> None:
    lines = plain(
        render_review(
            [merged("a")],
            [],
            0,
            trunk="main",
            target="owner/repo",
            trunk_note="fast-forwarded to abc1234",
        )
    )
    assert "owner/repo" in lines
    assert "fast-forwarded to abc1234" in lines


def test_plural_agreement_in_the_reparent_note() -> None:
    one = plain(
        render_review(
            [merged("a")], ["b"], 0, trunk="main", target=None, trunk_note=None
        )
    )
    many = plain(
        render_review(
            [merged("a")], ["b", "c"], 0, trunk="main", target=None, trunk_note=None
        )
    )
    assert "1 branch above is rebased onto its new parent." in one
    assert "2 branches above are rebased onto their new parents." in many


# -- interaction ------------------------------------------------------


def _keys(*presses: str):
    sequence = iter(presses)
    return lambda: next(sequence)


def test_pick_cleanup_returns_the_highlighted_scope() -> None:
    console = Console(width=100, height=40)
    stale = [merged("a"), closed("c", pushed=False)]
    with patch("shortcake.commands._sync_review.getchar", _keys("\x1b[C", "\r")):
        assert pick_cleanup(console, stale, [], trunk="main") == "safe"


def test_pick_cleanup_wraps_backwards() -> None:
    console = Console(width=100, height=40)
    with patch("shortcake.commands._sync_review.getchar", _keys("\x1b[D", "\r")):
        assert pick_cleanup(console, [merged("a")], [], trunk="main") == "cancel"


def test_pick_cleanup_ignores_unrelated_keys() -> None:
    console = Console(width=100, height=40)
    with patch("shortcake.commands._sync_review.getchar", _keys("z", "\r")):
        assert pick_cleanup(console, [merged("a")], [], trunk="main") == "all"


@pytest.mark.parametrize("key", ["q", "\x03", "\x04"])
def test_pick_cleanup_treats_quit_keys_as_cancel(key: str) -> None:
    console = Console(width=100, height=40)
    with patch("shortcake.commands._sync_review.getchar", _keys(key)):
        assert pick_cleanup(console, [merged("a")], [], trunk="main") == "cancel"


def test_pick_cleanup_draws_before_reading_a_key() -> None:
    """The block must be on screen before it blocks waiting for input."""
    console = Console(width=100, height=40)
    order: list[str] = []
    real_refresh = Live.refresh

    def spy(self):
        order.append("draw")
        return real_refresh(self)

    def key():
        order.append("key")
        return "\r"

    with (
        patch.object(Live, "refresh", spy),
        patch("shortcake.commands._sync_review.getchar", key),
    ):
        pick_cleanup(console, [merged("a")], [], trunk="main")

    assert order[: order.index("key")].count("draw") >= 1

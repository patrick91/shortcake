"""Tests for the interactive submit scope picker.

The picker replaced a bare ``typer.confirm``. Two behaviours are load-bearing
and easy to regress: it must never crop its own options off-screen, and "just
my arm" must not walk back down into the sibling arm.
"""

from unittest.mock import patch

import pytest
from rich.console import Console
from rich.live import Live
from rich.text import Text

from shortcake._stack_view import RowState, StackRenderer, StackRow
from shortcake.commands._submit_picker import (
    apply_scope,
    inline_options,
    lineage_of,
    option_lines,
    pick_scope,
    picker_parts,
    render_picker,
    scope_options,
    summary_line,
    tips_of,
)

LONG = [f"branch-with-a-fairly-long-name-{index:02d}" for index in range(20)]


def linear_rows(count: int = 6) -> list[StackRow]:
    rows = [StackRow("main", state=RowState.BASE, label=Text("(base)"))]
    parent = "main"
    for name in LONG[:count]:
        rows.append(StackRow(name, parent=parent, label=Text("")))
        parent = name
    return rows


def forked_rows() -> list[StackRow]:
    """main -> a -> b, then b forks into (c -> d) and (e -> f)."""
    rows = [
        StackRow("main", state=RowState.BASE, label=Text("(base)")),
        StackRow("a", parent="main"),
        StackRow("b", parent="a"),
        StackRow("c", parent="b"),
        StackRow("d", parent="c"),
        StackRow("e", parent="b"),
        StackRow("f", parent="e"),
    ]
    for row in rows[1:]:
        row.state = RowState.PENDING
        row.label = Text("")
    return rows


def renderer_for(rows, width=100, height=40) -> StackRenderer:
    return StackRenderer(
        rows, "Submit plan", Console(width=width, height=height), planning=True
    )


# -- stack shape ------------------------------------------------------


def test_tips_of_finds_every_leaf() -> None:
    assert {row.branch for row in tips_of(forked_rows())} == {"d", "f"}
    assert [row.branch for row in tips_of(linear_rows(3))] == [LONG[2]]


def test_lineage_excludes_the_sibling_arm() -> None:
    """Regression: descending from the *ancestors* pulls in the whole stack."""
    rows = forked_rows()
    assert lineage_of(rows, "e") == {"main", "a", "b", "e", "f"}
    assert lineage_of(rows, "c") == {"main", "a", "b", "c", "d"}


def test_apply_scope_previews_each_choice() -> None:
    rows = forked_rows()

    apply_scope(rows, "stack", 3, "e")
    assert all(r.state is RowState.PENDING for r in rows[1:])

    apply_scope(rows, "downstack", 3, "e")
    included = [r.branch for r in rows if r.state is RowState.PENDING]
    assert included == ["a", "b", "c"]

    apply_scope(rows, "lineage", 3, "e")
    included = [r.branch for r in rows if r.state is RowState.PENDING]
    assert included == ["a", "b", "e", "f"]
    assert [r.branch for r in rows if r.state is RowState.EXCLUDED] == ["c", "d"]


def test_apply_scope_lineage_without_a_current_branch_falls_back() -> None:
    rows = linear_rows(3)
    apply_scope(rows, "lineage", 2, None)
    assert [r.branch for r in rows if r.state is RowState.PENDING] == LONG[:2]


# -- options ----------------------------------------------------------


def test_options_without_stack_offer_the_upstack_delta() -> None:
    rows = linear_rows(5)
    for row in rows[1:]:
        row.state = RowState.PENDING
    options, warning = scope_options(rows, 2, stack=False)

    assert warning is None
    assert [scope for scope, _, _ in options] == ["downstack", "stack", "cancel"]
    assert options[0][2] == "2 branches"
    assert "3 more" in options[1][2]  # the delta, not the total


def test_options_with_stack_on_a_fork_warn_about_the_other_arm() -> None:
    rows = forked_rows()
    rows[5].is_current = True  # "e"
    options, warning = scope_options(rows, 3, stack=True)

    assert warning is not None and "2 arms" in warning
    assert [scope for scope, _, _ in options] == ["stack", "lineage", "cancel"]
    assert options[0][2] == "6 branches · 2 arms"
    assert options[1][2] == "4 branches"  # my arm only, base excluded


def test_options_with_stack_and_no_current_branch() -> None:
    options, _ = scope_options(forked_rows(), 3, stack=True)
    assert options[1][2] == "6 branches"


def test_options_mention_arms_on_a_fork_without_stack() -> None:
    rows = forked_rows()
    _, _ = scope_options(rows, 3, stack=False)
    options, _ = scope_options(rows, 3, stack=False)
    assert "2 arms" in options[1][2]


# -- pieces -----------------------------------------------------------


def test_summary_line_counts_both_sides() -> None:
    rows = forked_rows()
    apply_scope(rows, "downstack", 3, "e")
    line = summary_line(rows).plain
    assert "3 selected" in line
    assert "3 not submitted" in line


def test_summary_line_omits_the_excluded_half_when_empty() -> None:
    rows = forked_rows()
    apply_scope(rows, "stack", 3, "e")
    assert "not submitted" not in summary_line(rows).plain


def test_option_lines_mark_the_cursor() -> None:
    options, _ = scope_options(linear_rows(3), 2, stack=False)
    lines = [line.plain for line in option_lines(options, 1)]
    assert lines[1].startswith("  ❯ ")  # noqa: RUF001
    assert not lines[0].startswith("  ❯ ")  # noqa: RUF001


def test_inline_options_put_everything_on_one_line() -> None:
    options, _ = scope_options(linear_rows(3), 2, stack=False)
    line = inline_options(options, 0).plain
    assert line.count("·") == 2
    assert "❯ Through the current branch" in line  # noqa: RUF001


# -- layout tiers -----------------------------------------------------


def test_full_tier_shows_every_branch_with_breathing_lines() -> None:
    rows = linear_rows(6)
    options, _ = scope_options(rows, 3, stack=False)
    parts = picker_parts(renderer_for(rows), options, 0, None, "full", 40)
    text = "\n".join(part.plain for part in parts)
    assert all(branch in text for branch in LONG[:6])
    assert "│" in text


def test_dense_tier_keeps_every_branch_and_drops_spacing() -> None:
    """Spacing is given up before any branch is hidden.

    The question is "submit the whole stack?"; it cannot be answered against a
    stack you cannot see.
    """
    rows = linear_rows(20)
    options, _ = scope_options(rows, 8, stack=False)
    renderer = renderer_for(rows, height=30)

    assert picker_parts(renderer, options, 0, None, "full", 30) is None
    dense = picker_parts(renderer, options, 0, None, "dense", 30)
    text = "\n".join(part.plain for part in dense)
    assert all(branch in text for branch in LONG)  # nothing hidden
    assert not any(part.plain.strip() == "│" for part in dense)


def test_scroll_tier_hides_branches_only_when_dense_will_not_fit() -> None:
    rows = linear_rows(20)
    options, _ = scope_options(rows, 8, stack=False)
    renderer = renderer_for(rows, height=18)

    assert picker_parts(renderer, options, 0, None, "dense", 18) is None
    scroll = picker_parts(renderer, options, 0, None, "scroll", 18)
    text = "\n".join(part.plain for part in scroll)
    assert "below" in text  # a collapsed run appears


def test_scroll_tier_declines_when_the_tree_would_be_pointless() -> None:
    rows = linear_rows(20)
    options, _ = scope_options(rows, 8, stack=False)
    assert picker_parts(renderer_for(rows), options, 0, None, "scroll", 12) is None


def test_compact_tier_sheds_by_priority_but_keeps_the_options() -> None:
    rows = linear_rows(20)
    options, _ = scope_options(rows, 8, stack=False)
    renderer = renderer_for(rows)

    for height in (8, 4, 3, 2, 1):
        parts = picker_parts(renderer, options, 0, "careful!", "compact", height)
        assert len(parts) <= max(height, 1)
        # whatever else goes, the choice itself survives
        assert "Through the current branch" in parts[-1].plain


def test_warning_is_rendered_above_the_question() -> None:
    rows = forked_rows()
    options, warning = scope_options(rows, 3, stack=True)
    parts = picker_parts(renderer_for(rows), options, 0, warning, "full", 40)
    text = [part.plain for part in parts]
    assert any("2 arms" in line for line in text)
    assert text.index("  What should I submit?") > next(
        index for index, line in enumerate(text) if "2 arms" in line
    )


@pytest.mark.parametrize("height", list(range(1, 41)))
def test_picker_never_overflows_the_terminal(height: int) -> None:
    """Cropping the options would leave you choosing blind."""
    rows = linear_rows(20)
    options, warning = scope_options(rows, 8, stack=False)
    renderer = renderer_for(rows, height=height)
    console = Console(width=100, height=height)
    with console.capture() as capture:
        console.print(render_picker(renderer, options, 0, warning))
    printed = [line for line in capture.get().splitlines() if line.strip()]
    assert len(printed) <= height


# -- interaction ------------------------------------------------------


def _keys(*presses: str):
    """getchar stub that replays a fixed key sequence."""
    sequence = iter(presses)
    return lambda: next(sequence)


def test_pick_scope_returns_the_highlighted_option_on_enter() -> None:
    rows = linear_rows(4)
    console = Console(width=100, height=40)
    with patch("shortcake.commands._submit_picker.getchar", _keys("\x1b[B", "\r")):
        scope = pick_scope(console, rows, "Submit plan", 2, stack=False)
    assert scope == "stack"
    assert all(row.state is RowState.PENDING for row in rows[1:])


def test_pick_scope_wraps_around_with_the_up_key() -> None:
    rows = linear_rows(4)
    console = Console(width=100, height=40)
    with patch("shortcake.commands._submit_picker.getchar", _keys("\x1b[A", "\r")):
        scope = pick_scope(console, rows, "Submit plan", 2, stack=False)
    assert scope == "cancel"


def test_pick_scope_ignores_unrelated_keys() -> None:
    rows = linear_rows(4)
    console = Console(width=100, height=40)
    with patch("shortcake.commands._submit_picker.getchar", _keys("z", "\x1b[B", "\r")):
        scope = pick_scope(console, rows, "Submit plan", 2, stack=False)
    assert scope == "stack"


@pytest.mark.parametrize("key", ["\x03", "\x04"])
def test_pick_scope_treats_interrupt_keys_as_cancel(key: str) -> None:
    rows = linear_rows(4)
    console = Console(width=100, height=40)
    with patch("shortcake.commands._submit_picker.getchar", _keys(key)):
        assert pick_scope(console, rows, "Submit plan", 2, stack=False) == "cancel"


def test_pick_scope_previews_the_highlighted_option_before_choosing() -> None:
    """Moving the cursor updates the tree, not just the option list."""
    rows = forked_rows()
    rows[5].is_current = True
    console = Console(width=100, height=40)
    seen: list[list[str]] = []

    def record():
        seen.append([r.branch for r in rows if r.state is RowState.PENDING])
        return next(presses)

    presses = iter(["\x1b[B", "\r"])
    with patch("shortcake.commands._submit_picker.getchar", record):
        scope = pick_scope(console, rows, "Submit plan", 3, stack=True)

    assert scope == "lineage"
    assert seen[0] == ["a", "b", "c", "d", "e", "f"]  # whole stack previewed
    assert seen[1] == ["a", "b", "e", "f"]  # then just my arm


def test_apply_scope_updates_the_status_column_too() -> None:
    """Regression: flipping state alone leaves a stale label.

    `state` and `label` are separate on StackRow, so a row that comes into
    scope kept reading "not submitted" and one that left it kept promising
    "create PR".
    """
    rows = linear_rows(3)
    for row in rows[1:]:
        row.state = RowState.PENDING
    labels = {name: Text(f"create PR {name[-2:]}") for name in LONG[:3]}

    apply_scope(rows, "downstack", 2, LONG[1], labels)
    assert [row.label.plain for row in rows[1:]] == [
        "create PR 00",
        "create PR 01",
        "not submitted",
    ]

    apply_scope(rows, "stack", 2, LONG[1], labels)
    assert [row.label.plain for row in rows[1:]] == [
        "create PR 00",
        "create PR 01",
        "create PR 02",
    ]


def test_apply_scope_without_labels_leaves_the_column_alone() -> None:
    rows = linear_rows(2)
    rows[1].label = Text("kept")
    apply_scope(rows, "stack", 2, None)
    assert rows[1].label.plain == "kept"


def test_pick_scope_draws_before_loading_plans() -> None:
    """Regression: looking up PRs first left the terminal blank.

    One API call per branch ran before the Live opened, so the menu appeared
    seconds after the command did.
    """
    rows = linear_rows(3)
    console = Console(width=100, height=40)
    order: list[str] = []

    def load_plans(redraw):
        order.append("load")
        redraw()

    real_live_enter = Live.__enter__

    def spy_enter(self):
        order.append("draw")
        return real_live_enter(self)

    with (
        patch.object(Live, "__enter__", spy_enter),
        patch("shortcake.commands._submit_picker.getchar", _keys("\r")),
    ):
        pick_scope(console, rows, "Submit plan", 2, stack=False, load_plans=load_plans)

    assert order == ["draw", "load"]

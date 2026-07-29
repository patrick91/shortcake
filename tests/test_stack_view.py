"""Tests for the shared stack renderer.

Three of these guard bugs that only appear in *live* rendering and are invisible
in a single static frame: the viewport must not jump when nothing is in flight,
the layout must not depend on live state, and no frame may exceed the terminal.
"""

from unittest.mock import patch

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from shortcake._stack_view import (
    SPINNER,
    SPINNER_INTERVAL_MS,
    STATUS_COLUMN,
    AppendStackView,
    LiveStackView,
    RowState,
    SilentStackView,
    StackRenderer,
    StackRow,
    build_layout,
    elide,
    marker_text,
    render_row,
)

BRANCHES = [f"branch-with-a-fairly-long-name-{index:02d}" for index in range(20)]


def linear_rows(count: int = 6, *, base: bool = True) -> list[StackRow]:
    rows = []
    if base:
        rows.append(StackRow("main", state=RowState.BASE, label=Text("(base)")))
    parent = "main" if base else None
    for name in BRANCHES[:count]:
        rows.append(StackRow(name, parent=parent, label=Text("")))
        parent = name
    return rows


def forked_rows() -> list[StackRow]:
    """main -> a -> b, then b forks into (c -> d) and (e -> f)."""
    return [
        StackRow("main", state=RowState.BASE, label=Text("(base)")),
        StackRow("a", parent="main", label=Text("")),
        StackRow("b", parent="a", label=Text("")),
        StackRow("c", parent="b", label=Text("")),
        StackRow("d", parent="c", label=Text("")),
        StackRow("e", parent="b", label=Text("")),
        StackRow("f", parent="e", label=Text("")),
    ]


def renderer_for(rows, width=100, height=40, planning=False) -> StackRenderer:
    return StackRenderer(
        rows, "header", Console(width=width, height=height), planning=planning
    )


def plain(lines) -> list[str]:
    return [line.plain for line in lines]


# -- layout -----------------------------------------------------------


def test_elide_keeps_head_and_tail() -> None:
    assert elide("short", 20) == "short"
    assert elide("abcdefghij", 5) == "ab…ij"
    assert elide("abcdefghij", 1) == "…"


def test_build_layout_linear_chain_uses_breathing_lines() -> None:
    items = build_layout(linear_rows(3))
    assert [item.row.branch for item in items] == ["main", *BRANCHES[:3]]
    assert items[0].lead is None
    assert all(item.lead == "  │" for item in items[1:])
    assert all(item.prefix == "  " for item in items)


def test_build_layout_fork_indents_arms_and_keeps_breathing_lines() -> None:
    items = {item.row.branch: item for item in build_layout(forked_rows())}
    assert items["c"].prefix == "  ├─"
    assert items["e"].prefix == "  └─"
    # arms get the same `│` a linear chain gets, so a fork is not denser
    assert items["c"].lead == "  │"
    assert items["e"].lead == "  │"
    # children indent against their arm's connector
    assert items["d"].prefix == "  │ "
    assert items["f"].prefix == "    "


def test_build_layout_row_without_known_parent_is_a_root() -> None:
    rows = [StackRow("orphan", parent="deleted-branch")]
    assert [item.row.branch for item in build_layout(rows)] == ["orphan"]


# -- row rendering ----------------------------------------------------


def test_marker_reflects_state() -> None:
    def marker(state, **kwargs):
        row = StackRow("x", state=state, **kwargs)
        return marker_text(row, planning=False, frame=0).plain

    assert marker(RowState.BASE) == "◯"
    assert marker(RowState.EXCLUDED) == "◯"
    assert marker(RowState.SKIPPED) == "◌"
    assert marker(RowState.FAILED) == "✗"
    assert marker(RowState.PENDING) == "○"
    assert marker(RowState.NOT_ATTEMPTED) == "○"
    assert marker(RowState.DONE) == "●"
    assert marker(RowState.ACTIVE) in SPINNER


def test_marker_carries_current_branch_only_while_planning() -> None:
    row = StackRow("x", is_current=True)
    assert marker_text(row, planning=True, frame=0).plain == "◉"
    # once work starts the marker means state, not "you are here"
    assert marker_text(row, planning=False, frame=0).plain == "○"
    assert marker_text(row, planning=True, frame=0).plain == "◉"


def test_selected_rows_are_not_dimmed_by_the_connector_prefix() -> None:
    """Regression: Text(prefix, style=...) styles the *whole* Text.

    Building the row that way makes every later append inherit the dim style,
    so selected branches render identically to excluded ones.
    """
    rows = [
        StackRow("main", state=RowState.BASE),
        StackRow("selected", parent="main", state=RowState.PENDING),
        StackRow("skipped", parent="selected", state=RowState.EXCLUDED),
    ]
    items = build_layout(rows)
    width = renderer_for(rows, planning=True).label_width()

    def name_style(item):
        text = render_row(item, width, planning=True, frame=0)
        # no span at all means the name inherits the default (readable) style
        return next(
            (
                span.style
                for span in text.spans
                if text.plain[span.start : span.end].strip() == item.row.branch
            ),
            None,
        )

    assert name_style(items[1]) is None  # readable
    assert name_style(items[2]).color.name == "bright_black"  # dim
    # the base branch stays readable too — dimming makes it look excluded
    assert name_style(items[0]) is None


def test_row_without_label_has_no_trailing_padding() -> None:
    rows = linear_rows(2)
    rows[1].label = None
    items = build_layout(rows)
    line = render_row(items[1], 60, planning=False, frame=0)
    assert line.plain == line.plain.rstrip()


def test_long_names_elide_against_their_connector_prefix() -> None:
    rows = forked_rows()
    for row in rows[1:]:
        row.branch = "x" * 80
        row.parent = rows[rows.index(row) - 1].branch if False else row.parent
    renderer = renderer_for(rows, width=50)
    for line in renderer.tree_lines():
        assert len(line.plain) <= 50


# -- alignment --------------------------------------------------------


def test_label_width_does_not_depend_on_live_state() -> None:
    """Regression: sizing the column from current statuses slides rows sideways.

    "creating PR…" is 12 wide and "#4671" is 5, so a live-derived width changes
    as branches finish.
    """
    rows = linear_rows(4)
    renderer = renderer_for(rows)
    before = renderer.label_width()

    rows[1].state = RowState.ACTIVE
    rows[1].label = Text("creating PR…")
    rows[2].state = RowState.DONE
    rows[2].label = Text("#4671")
    assert renderer.label_width() == before


def test_label_width_is_capped_by_terminal_width() -> None:
    renderer = renderer_for(linear_rows(3), width=40)
    assert renderer.label_width() == 40 - 2 - STATUS_COLUMN - 1


def test_status_column_aligns_across_fork_depths() -> None:
    rows = forked_rows()
    for row in rows[1:]:
        row.state = RowState.DONE
        row.label = Text("#42")
    lines = plain(renderer_for(rows).tree_lines())
    columns = {line.index("#42") for line in lines if "#42" in line}
    assert len(columns) == 1


# -- frontier ---------------------------------------------------------


def test_frontier_is_defined_when_nothing_is_active() -> None:
    """Regression: anchoring on "the active row" flashes on every transition.

    Between finishing one branch and starting the next nothing is ACTIVE, so an
    active-row lookup must fall back to some other row, teleporting the
    viewport for a frame.
    """
    rows = linear_rows(4)
    renderer = renderer_for(rows)

    rows[1].state = RowState.ACTIVE
    active = renderer.frontier()
    rows[1].state = RowState.DONE  # the gap: nothing is active now
    gap = renderer.frontier()
    rows[2].state = RowState.ACTIVE
    resumed = renderer.frontier()

    assert active == 1
    assert gap == 2 == resumed  # moved forward exactly one row, never backward


def test_frontier_skips_base_and_excluded_rows() -> None:
    rows = linear_rows(3)
    rows[1].state = RowState.EXCLUDED
    assert renderer_for(rows).frontier() == 2


def test_frontier_returns_last_row_when_everything_finished() -> None:
    rows = linear_rows(3)
    for row in rows[1:]:
        row.state = RowState.DONE
    assert renderer_for(rows).frontier() == 3


# -- windowing --------------------------------------------------------


def test_short_stacks_are_never_windowed() -> None:
    renderer = renderer_for(linear_rows(3))
    items = renderer.layout()
    assert renderer.window(items, budget=2) == set(range(len(items)))


def test_window_scrolls_monotonically_and_never_jumps() -> None:
    """The viewport only moves forward, including across transition gaps."""
    rows = linear_rows(20)
    renderer = renderer_for(rows, height=20)
    starts = []
    for row in rows[1:]:
        for state in (RowState.ACTIVE, RowState.DONE):
            row.state = state
            if state is RowState.ACTIVE:
                row.label = Text("creating PR…")
            starts.append(min(renderer.window(renderer.layout(), budget=12)))
    assert starts == sorted(starts)


def test_window_pins_fork_points_and_arm_heads() -> None:
    """A visible row's ├─/└─ must never dangle off a hidden parent."""
    rows = forked_rows()
    for row in rows[1:5]:
        row.state = RowState.DONE
    rows[6].state = RowState.ACTIVE
    renderer = renderer_for(rows, height=16)
    items = renderer.layout()
    keep = renderer.window(items, budget=6)
    visible = {items[i].row.branch for i in keep}
    assert "b" in visible  # fork point
    assert "e" in visible  # arm head that "f" indents against


def test_window_does_not_pin_the_whole_ancestry() -> None:
    """Regression: in a stack the ancestry is everything above it.

    Pinning it keeps every row, so windowing never engages at all.
    """
    rows = linear_rows(20)
    for row in rows[1:10]:
        row.state = RowState.DONE
    rows[10].state = RowState.ACTIVE
    renderer = renderer_for(rows, height=20)
    items = renderer.layout()
    assert len(renderer.window(items, budget=12)) < len(items)


def test_centered_window_shows_both_sides_of_the_anchor() -> None:
    rows = linear_rows(20)
    renderer = renderer_for(rows, height=20)
    items = renderer.layout()
    keep = renderer.window(items, anchor=10, budget=12, place="center")
    assert min(keep) < 10 < max(keep)


def test_dense_window_fits_more_rows_than_spaced() -> None:
    rows = linear_rows(20)
    for row in rows[1:10]:
        row.state = RowState.DONE
    rows[10].state = RowState.ACTIVE
    renderer = renderer_for(rows, height=30)
    items = renderer.layout()
    spaced = renderer.window(items, budget=12, dense=False)
    dense = renderer.window(items, budget=12, dense=True)
    assert len(dense) > len(spaced)


def test_collapsed_runs_are_labelled_by_direction() -> None:
    rows = linear_rows(20)
    for row in rows[1:10]:
        row.state = RowState.DONE
    rows[10].state = RowState.ACTIVE
    renderer = renderer_for(rows, height=20)
    lines = plain(
        renderer.tree_lines(window=True, budget=12, labels=("above", "below"))
    )
    assert any("above" in line for line in lines)
    assert any("below" in line for line in lines)


def test_dense_drops_breathing_lines_but_keeps_fork_connectors() -> None:
    rows = forked_rows()
    dense = plain(renderer_for(rows).tree_lines(dense=True))
    assert not any(line.strip() == "│" for line in dense)
    assert any("├─" in line for line in dense)
    assert any("└─" in line for line in dense)


def test_detail_line_is_rendered_under_its_row() -> None:
    rows = linear_rows(2)
    rows[1].state = RowState.FAILED
    rows[1].detail = "remote: non-fast-forward"
    lines = plain(renderer_for(rows).tree_lines())
    assert any("remote: non-fast-forward" in line for line in lines)


# -- progress rendering -----------------------------------------------


def test_counters_ignore_base_and_excluded_rows() -> None:
    rows = linear_rows(4)
    rows[1].state = RowState.EXCLUDED
    rows[2].state = RowState.DONE
    assert renderer_for(rows).counters() == (1, 3)


def test_active_line_carries_branch_and_status() -> None:
    rows = linear_rows(4)
    rows[1].state = RowState.DONE
    rows[2].state = RowState.ACTIVE
    rows[2].label = Text("creating PR…")
    line = renderer_for(rows).active_line(0).plain
    assert "creating PR…" in line
    assert "1/4" in line


def test_progress_never_exceeds_the_terminal_height() -> None:
    """Live crops past the terminal, so a frame must fit whatever the height."""
    rows = linear_rows(20)
    for row in rows[1:8]:
        row.state = RowState.DONE
    rows[8].state = RowState.ACTIVE
    for height in range(1, 41):
        renderer = renderer_for(rows, height=height)
        console = Console(width=100, height=height)
        with console.capture() as capture:
            console.print(renderer.render(0, running=True))
        printed = [line for line in capture.get().splitlines() if line.strip()]
        assert len(printed) <= height, f"overflowed at height={height}"


def test_progress_degrades_to_a_single_line_on_a_tiny_terminal() -> None:
    rows = linear_rows(20)
    for row in rows[1:8]:
        row.state = RowState.DONE
    rows[8].state = RowState.ACTIVE
    rows[8].label = Text("creating PR…")
    assert renderer_for(rows, height=6).progress_parts(0, "full", 6) is None
    assert renderer_for(rows, height=6).progress_parts(0, "compact", 6) is None
    minimal = renderer_for(rows, height=6).progress_parts(0, "minimal", 6)
    assert len(minimal) == 1


def test_final_frame_is_not_truncated_on_a_short_terminal() -> None:
    """Cropping a transient frame is fine; cropping the result loses PR links."""
    rows = linear_rows(20)
    for row in rows[1:]:
        row.state = RowState.DONE
        row.label = Text("#1")
    renderer = renderer_for(rows, height=8)
    renderer.footer = [Text("✓ done"), Text("  https://example.test/pull/1")]
    console = Console(width=100, height=8)
    with console.capture() as capture:
        console.print(renderer.render(0, running=False))
    output = capture.get()
    assert all(row.branch in output for row in rows)
    assert "https://example.test/pull/1" in output


# -- views ------------------------------------------------------------


def test_live_view_renders_through_the_renderer() -> None:
    rows = linear_rows(3)
    console = Console(width=100, height=40)
    view = LiveStackView(renderer_for(rows), console)
    view.sync()  # no-op: the refresh thread repaints
    assert view.get_renderable() is not None
    view.finish([Text("✓ done")])
    assert view.renderer.footer[0].plain == "✓ done"


def test_append_view_streams_each_row_once_as_it_finishes() -> None:
    rows = linear_rows(3)
    console = Console(width=100, height=40)
    renderer = StackRenderer(rows, "Submitting", console)
    view = AppendStackView(renderer, console)

    with console.capture() as capture, view:
        rows[1].state = RowState.DONE
        rows[1].label = Text("#1")
        view.sync()
        view.sync()  # already printed; must not repeat
        rows[2].state = RowState.FAILED
        rows[2].detail = "push rejected"
        view.sync()
        view.finish([Text("✓ done")])
    output = capture.get()

    assert output.count(BRANCHES[0]) == 1
    assert "push rejected" in output
    assert "✓ done" in output


def test_silent_view_prints_nothing() -> None:
    rows = linear_rows(3)
    console = Console(width=100, height=40)
    view = SilentStackView(StackRenderer(rows, "Submitting", console))
    with console.capture() as capture, view:
        rows[1].state = RowState.DONE
        view.sync()
        view.finish([Text("✓ done")])
    assert capture.get() == ""


def test_toolkit_picks_the_view_for_the_mode() -> None:
    from shortcake._output import get_rich_toolkit

    rows = linear_rows(2)

    json_view, _ = get_rich_toolkit(json_output=True).stack_view(rows, "h")
    assert isinstance(json_view, SilentStackView)

    toolkit = get_rich_toolkit()
    piped_view, _ = toolkit.stack_view(rows, "h")
    assert isinstance(piped_view, AppendStackView)

    with patch.object(type(toolkit.console), "is_terminal", True):
        tty_view, _ = toolkit.stack_view(rows, "h")
    assert isinstance(tty_view, LiveStackView)


def test_planning_footer_reports_the_selection_not_a_counter() -> None:
    """Before anything runs, "0/8 · 0s" says nothing useful."""
    rows = linear_rows(4)
    rows[4].state = RowState.EXCLUDED
    renderer = renderer_for(rows, planning=True)
    assert renderer.progress_footer().plain == (
        "  ● 3 selected · ○ 1 upstack branch not selected"
    )

    renderer.planning = False
    assert "0/3" in renderer.progress_footer().plain


def test_planning_footer_omits_the_excluded_half_when_empty() -> None:
    renderer = renderer_for(linear_rows(3), planning=True)
    assert renderer.progress_footer().plain == "  ● 3 selected"


def test_spinner_frames_fit_the_marker_column() -> None:
    """Single-width, or the name column shifts on every tick."""
    assert all(cell_len(frame) == 1 for frame in SPINNER)


def test_spinner_is_paced_by_its_own_interval() -> None:
    """Regression: a flat frame rate strobes a two-frame pulse.

    Ten frames/sec suits a ten-frame rotation; on a two-frame pulse it flips
    five times a second.
    """
    rows = linear_rows(2)
    rows[1].state = RowState.ACTIVE
    console = Console(width=100, height=40)
    view = LiveStackView(StackRenderer(rows, "h", console), console)

    seconds_per_cycle = len(SPINNER) * SPINNER_INTERVAL_MS / 1000
    assert 0.6 <= seconds_per_cycle <= 1.5

    with patch("shortcake._stack_view.time.monotonic") as clock:
        view.renderer.started_at = 0.0
        clock.return_value = 0.0
        first = view.get_renderable()
        # still inside the first frame's interval
        clock.return_value = (SPINNER_INTERVAL_MS - 10) / 1000
        assert _spinner_of(view.get_renderable()) == _spinner_of(first)
        # just past it
        clock.return_value = (SPINNER_INTERVAL_MS + 10) / 1000
        assert _spinner_of(view.get_renderable()) != _spinner_of(first)


def _spinner_of(renderable) -> str:
    for part in renderable.renderables:
        text = getattr(part, "plain", "")
        for char in text:
            if char in SPINNER:
                return char
    raise AssertionError("no spinner frame rendered")

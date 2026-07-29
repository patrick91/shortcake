"""Interactive scope picker for `sc submit`.

Replaces a bare ``typer.confirm`` that asked "Also submit upstack branches?"
*after* printing the plan tree, then reprinted the whole tree. Two problems it
fixes beyond the redraw: answering "no" still submitted the downstack (there was
no way out but Ctrl-C), and the count reported the stack total rather than how
many extra branches you were being asked about.

The tree above the options previews the highlighted choice, so the effect of
each option is visible before choosing it.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.style import Style
from rich.text import Text
from rich_toolkit._input_handler import TextInputHandler
from rich_toolkit.container import getchar
from rich_toolkit.menu import Menu

from shortcake._stack_view import DIM, RowState, StackRenderer, StackRow

Option = tuple[str, str, str]


def tips_of(rows: list[StackRow]) -> list[StackRow]:
    """Branches with no children. More than one means the stack forks."""
    parents = {row.parent for row in rows if row.parent}
    return [
        row
        for row in rows
        if row.state is not RowState.BASE and row.branch not in parents
    ]


def lineage_of(rows: list[StackRow], current: str) -> set[str]:
    """Ancestors + self + descendants of ``current``, excluding sibling arms.

    This is what "my stack" means on a fork: everything below you and
    everything you lead to, but not the other arm off a shared parent.
    """
    by_name = {row.branch: row for row in rows}
    keep = {current}

    parent = by_name[current].parent
    while parent is not None and parent in by_name:
        keep.add(parent)
        parent = by_name[parent].parent

    # Descend from `current` only. Expanding from everything in `keep` walks
    # back down through the ancestors and picks up the sibling arm — that is,
    # the whole stack, which is exactly what this option exists to avoid.
    frontier = {current}
    while frontier:
        frontier = {
            row.branch
            for row in rows
            if row.parent in frontier and row.branch not in keep
        }
        keep |= frontier
    return keep


def _set_scope(row: StackRow, included: bool, labels: dict[str, Text] | None) -> None:
    """Move one row in or out of scope, status column included.

    ``state`` and ``label`` are separate on :class:`StackRow`, so flipping the
    state alone leaves a stale label — a selected row still reading "not
    submitted", or an excluded one still promising "create PR".
    """
    row.state = RowState.PENDING if included else RowState.EXCLUDED
    if labels is None:
        return
    row.label = (
        labels.get(row.branch, Text(""))
        if included
        else Text("not submitted", style=DIM)
    )


def apply_scope(
    rows: list[StackRow],
    scope: str,
    downstack: int,
    current: str | None,
    labels: dict[str, Text] | None = None,
) -> None:
    """Preview a scope choice on the plan tree.

    ``labels`` maps a branch to the status column it should show while in
    scope; pass it so the column tracks the highlighted option.
    """
    if scope == "lineage" and current is not None:
        keep = lineage_of(rows, current)
        for row in rows:
            if row.state is not RowState.BASE:
                _set_scope(row, row.branch in keep, labels)
        return

    index = 0
    for row in rows:
        if row.state is RowState.BASE:
            continue
        _set_scope(row, scope == "stack" or index < downstack, labels)
        index += 1


def scope_options(
    rows: list[StackRow], downstack: int, *, stack: bool
) -> tuple[list[Option], str | None]:
    """Options and a warning line for the two questions this picker asks."""
    total = sum(1 for row in rows if row.state is not RowState.BASE)
    arms = len(tips_of(rows))

    if stack:
        current = next((row.branch for row in rows if row.is_current), None)
        base = {row.branch for row in rows if row.state is RowState.BASE}
        mine = len(lineage_of(rows, current) - base) if current else total
        return (
            [
                ("stack", "Submit the whole stack", f"{total} branches · {arms} arms"),
                ("lineage", "Just my arm", f"{mine} branches"),
                ("cancel", "Cancel", ""),
            ],
            f"--stack covers {arms} arms, not only the branch you're on.",
        )

    extra = total - downstack
    hint = f"{total} branches · {extra} more"
    if arms > 1:
        hint += f" · {arms} arms"
    return (
        [
            ("downstack", "Through the current branch", f"{downstack} branches"),
            ("stack", "The whole stack", hint),
            ("cancel", "Cancel", ""),
        ],
        None,
    )


def summary_line(rows: list[StackRow]) -> Text:
    """One-line stand-in for the tree when the terminal is too short."""
    selected = sum(1 for row in rows if row.state is RowState.PENDING)
    skipped = sum(1 for row in rows if row.state is RowState.EXCLUDED)
    line = Text("  ")
    line.append(f"● {selected} selected")
    if skipped:
        line.append(f"   ◯ {skipped} not submitted", style=DIM)
    return line


def option_lines(options: list[Option], cursor: int) -> list[Text]:
    lines = []
    for index, (_, label, hint) in enumerate(options):
        active = index == cursor
        line = Text("  ")
        line.append("❯ " if active else "  ", style=Style(color="cyan"))  # noqa: RUF001
        line.append(
            label.ljust(28),
            style=Style(color="cyan", bold=True) if active else None,
        )
        if hint:
            line.append(hint, style=DIM)
        line.rstrip()
        lines.append(line)
    return lines


def inline_options(options: list[Option], cursor: int) -> Text:
    """Every option on one line — the floor layout for a tiny terminal."""
    line = Text("  ")
    for index, (_, label, _) in enumerate(options):
        if index:
            line.append("  ·  ", style=DIM)
        if index == cursor:
            line.append(f"❯ {label}", style=Style(color="cyan", bold=True))  # noqa: RUF001
        else:
            line.append(label, style=DIM)
    return line


def picker_parts(
    renderer: StackRenderer,
    options: list[Option],
    cursor: int,
    warning: str | None,
    tier: str,
    height: int,
) -> list[RenderableType] | None:
    """Build one layout tier, or None if it does not fit ``height``."""
    if tier == "compact":
        # The floor: sheds by priority so the options line always survives.
        tagged: list[tuple[int, RenderableType]] = [
            (0, Text(renderer.header)),
            (1, summary_line(renderer.rows)),
        ]
        if warning:
            tagged.append((2, Text(f"  {warning}", style=Style(color="yellow"))))
        tagged.append((3, inline_options(options, cursor)))
        for priority in (0, 1, 2):
            if len(tagged) <= height:
                break
            tagged = [entry for entry in tagged if entry[0] != priority]
        return [renderable for _, renderable in tagged]

    blank: list[RenderableType] = [Text("")]
    parts: list[RenderableType] = [Text(renderer.header), *blank]

    if tier == "summary":
        parts.append(summary_line(renderer.rows))
    else:
        # header, blank, blank, [warning + blank], question, blank, options
        budget = height - (5 + len(options) + (2 if warning else 0))
        if tier == "scroll" and budget < 7:
            return None
        items = renderer.layout()
        current = next((i for i, item in enumerate(items) if item.row.is_current), None)
        parts.extend(
            renderer.tree_lines(
                window=tier == "scroll",
                anchor=current,
                budget=budget,
                place="center",
                labels=("above", "below"),
                dense=tier in ("dense", "scroll"),
            )
        )

    parts.extend(blank)
    if warning:
        parts.append(Text(f"  {warning}", style=Style(color="yellow")))
        parts.extend(blank)
    parts.append(Text("  What should I submit?"))
    parts.extend(blank)
    parts.extend(option_lines(options, cursor))

    return parts if len(parts) <= height else None


def render_picker(
    renderer: StackRenderer,
    options: list[Option],
    cursor: int,
    warning: str | None,
) -> RenderableType:
    """Richest layout that actually fits, measured rather than estimated.

    Order matters: spacing is given up *before* any branch is hidden. The
    question is "submit the whole stack?" and it cannot be answered against a
    stack you cannot see. Dense drops a linear tree to half its height.
    """
    height = renderer.console.height
    for tier in ("full", "dense", "scroll", "summary", "compact"):
        parts = picker_parts(renderer, options, cursor, warning, tier, height)
        if parts is not None:
            return Group(*parts)
    return Group(  # pragma: no cover - compact always fits
        *picker_parts(renderer, options, cursor, warning, "compact", height)
    )


def pick_scope(
    console: Console,
    rows: list[StackRow],
    header: str,
    downstack: int,
    *,
    stack: bool,
    labels: dict[str, Text] | None = None,
    load_plans: Callable[[Callable[[], None]], None] | None = None,
) -> str:
    """Ask once, with the tree previewing the highlighted option.

    ``load_plans`` is run *after* the block is on screen and handed a redraw
    callable. Looking up PRs before opening the Live left the terminal blank
    for as long as the API took — one call per branch — so the menu appeared
    seconds after the command did.

    Returns the chosen scope: ``downstack``, ``stack``, ``lineage`` or
    ``cancel``.
    """
    options, warning = scope_options(rows, downstack, stack=stack)
    current = next((row.branch for row in rows if row.is_current), None)
    renderer = StackRenderer(rows, header, console, planning=True)

    cursor = 0
    with Live(console=console, auto_refresh=False, transient=True) as live:

        def redraw() -> None:
            live.update(render_picker(renderer, options, cursor, warning))
            live.refresh()

        if load_plans is not None:
            redraw()  # the stack is on screen before any network happens
            load_plans(redraw)

        while True:
            apply_scope(rows, options[cursor][0], downstack, current, labels)
            live.update(render_picker(renderer, options, cursor, warning))
            live.refresh()
            try:
                key = getchar()
            except KeyboardInterrupt:  # pragma: no cover - needs a real tty
                return "cancel"
            if key in Menu.DOWN_KEYS:
                cursor = (cursor + 1) % len(options)
            elif key in Menu.UP_KEYS:
                cursor = (cursor - 1) % len(options)
            elif key == TextInputHandler.ENTER_KEY:
                scope = options[cursor][0]
                apply_scope(rows, scope, downstack, current, labels)
                return scope
            elif key in ("\x03", "\x04"):
                return "cancel"

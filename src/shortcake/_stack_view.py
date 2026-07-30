"""Shared stack rendering: row model, tree layout, and progress views.

The stack tree is drawn once and then *becomes* the progress display: rows keep
their place while their markers and status column change. `submit` and `restack`
share this; the row/connector layout is also what `_tree.py` builds on.

Three views implement the same interface so call sites never branch on mode:

* :class:`LiveStackView` — TTY. Subclasses rich-toolkit's ``Progress`` for the
  ``rich.live.Live`` machinery and rewrites the block in place.
* :class:`AppendStackView` — non-TTY. ``Live`` only emits a *final* frame when
  piped, so streaming output needs its own append-only path.
* :class:`SilentStackView` — JSON mode. Renders nothing; the command emits one
  document itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.style import Style
from rich.text import Text
from rich_toolkit.progress import Progress
from rich_toolkit.styles import MinimalStyle

#: Marker shown while a row is working: a striped circle pulsing to an empty
#: one. Circle-family on purpose — it sits beside ◯ ● ○ ◌ ✗ ◉, where braille
#: dots read as a different typeface. Frames must stay single-width or the name
#: column shifts on every tick.
#:
#: ``○`` is also the queued marker. That overlap is deliberate and was chosen
#: over a lookalike glyph; don't "fix" it without asking.
SPINNER = "◍○"

#: Milliseconds per frame. Two frames driven at a ten-frame rotation's rate
#: strobes instead of breathing, so the pulse is paced by its own interval
#: rather than a shared frame rate.
SPINNER_INTERVAL_MS = 450

#: Widest the status column ever gets ("not submitted", "not attempted").
#: Fixed on purpose: sizing it from the *current* statuses resizes the name
#: column mid-run and slides every row sideways.
STATUS_COLUMN = 14

DIM = Style(color="bright_black")


class RowState(Enum):
    """What a row is doing, independent of the words in its status column."""

    BASE = auto()
    """The branch the stack lands on. Not acted upon."""

    EXCLUDED = auto()
    """In the stack but deliberately not part of this operation."""

    PENDING = auto()
    ACTIVE = auto()
    DONE = auto()
    SKIPPED = auto()
    FAILED = auto()
    NOT_ATTEMPTED = auto()
    """Never started because an earlier branch failed."""


#: States that will not change again this run.
TERMINAL_STATES = frozenset(
    {RowState.DONE, RowState.SKIPPED, RowState.FAILED, RowState.NOT_ATTEMPTED}
)

#: States whose branch name is dimmed. BASE is deliberately absent — dimming it
#: makes it indistinguishable from EXCLUDED, and it answers "where does this
#: land?", the thing most worth noticing when it is wrong.
_DIM_NAME_STATES = frozenset({RowState.EXCLUDED, RowState.SKIPPED})

_MARKER_STYLES = {
    RowState.DONE: Style(color="green"),
    RowState.FAILED: Style(color="red"),
    RowState.ACTIVE: Style(color="cyan"),
}


@dataclass
class StackRow:
    """One branch in the tree. Commands mutate ``state``/``label`` in place."""

    branch: str
    parent: str | None = None
    state: RowState = RowState.PENDING
    label: Text | None = None
    """Right-hand status column. Owned by the command, not this module."""
    detail: str | None = None
    """Extra dim line under the row, e.g. the reason a push was rejected."""
    is_current: bool = False


@dataclass
class LayoutItem:
    """A row plus the tree furniture that precedes it.

    ``prefix`` is indent + connector (``"  "``, ``"  ├─"``, ``"    └─"``).
    ``lead`` is the ``│`` breathing line drawn above the row; dense mode drops
    those, which halves the height of a linear stack.
    """

    row: StackRow
    prefix: str
    lead: str | None


def build_layout(rows: list[StackRow]) -> list[LayoutItem]:
    """Walk parent links into render order, base first."""
    by_name = {row.branch: row for row in rows}
    children: dict[str, list[StackRow]] = {row.branch: [] for row in rows}
    roots: list[StackRow] = []
    for row in rows:
        if row.parent is not None and row.parent in by_name:
            children[row.parent].append(row)
        else:
            roots.append(row)

    items: list[LayoutItem] = []

    def walk(row: StackRow, prefix: str, connector: str, cont: str, lead: str | None):
        items.append(LayoutItem(row=row, prefix=prefix + connector, lead=lead))
        kids = children[row.branch]
        if len(kids) == 1:
            walk(kids[0], cont, "", cont, f"{cont}│")
            return
        for index, kid in enumerate(kids):
            last = index == len(kids) - 1
            # Arms get the same `│` breathing line a linear chain gets, so a
            # fork does not read denser than the rest of the tree.
            walk(
                kid,
                cont,
                "└─" if last else "├─",
                cont + ("  " if last else "│ "),
                f"{cont}│",
            )

    for root in roots:
        walk(root, "  ", "", "  ", None)
    return items


def elide(name: str, width: int) -> str:
    """Middle-elide — stacked branches share prefixes *and* have telling tails."""
    if len(name) <= width:
        return name
    if width <= 1:
        return "…"
    keep = width - 1
    head = (keep + 1) // 2
    tail = keep - head
    return name[:head] + "…" + (name[len(name) - tail :] if tail else "")


def marker_text(row: StackRow, *, planning: bool, frame: int) -> Text:
    """The leading glyph. Carries state, except while planning."""
    if row.state is RowState.ACTIVE:
        return Text(SPINNER[frame % len(SPINNER)], style=Style(color="cyan"))
    if planning and row.is_current and row.state is RowState.PENDING:
        # Nothing has a state yet, so the marker is free to mean "you are here"
        # the way the old plan tree did.
        return Text("◉", style=Style(color="cyan"))
    if row.state in (RowState.BASE, RowState.EXCLUDED):
        return Text("◯", style=DIM)
    if row.state is RowState.SKIPPED:
        return Text("◌", style=DIM)
    if row.state is RowState.FAILED:
        return Text("✗", style=Style(color="red"))
    if row.state is RowState.PENDING:
        return Text("●" if planning else "○", style=None if planning else DIM)
    if row.state is RowState.NOT_ATTEMPTED:
        return Text("○", style=DIM)
    return Text("●", style=_MARKER_STYLES[RowState.DONE])


def render_row(item: LayoutItem, label_width: int, *, planning: bool, frame: int):
    """Render one row: prefix, marker, name padded to the status column."""
    row = item.row
    # NOT Text(prefix, style=...) — that sets the base style for the whole Text,
    # so every later append inherits it and selected rows come out dimmed too.
    line = Text()
    line.append(item.prefix, style=DIM)
    line.append_text(marker_text(row, planning=planning, frame=frame))
    line.append(" ")

    budget = max(8, label_width - len(item.prefix) - 2)
    name = elide(row.branch, budget)
    style: Style | None = None
    if row.is_current:
        style = Style(bold=True)
    if row.state in _DIM_NAME_STATES or (
        row.state is RowState.PENDING and not planning
    ):
        style = DIM
    line.append(name, style=style)

    if row.label is not None and row.label.plain:
        pad = max(0, label_width - len(item.prefix) - 2 - len(name))
        line.append(" " * pad + "  ")
        line.append_text(row.label)
    line.rstrip()
    return line


class StackRenderer:
    """Owns the rows and turns them into lines that fit the terminal."""

    def __init__(
        self,
        rows: list[StackRow],
        header: str,
        console: Console,
        *,
        planning: bool = False,
    ) -> None:
        self.rows = rows
        self.header = header
        self.console = console
        self.planning = planning
        self.started_at = time.monotonic()
        self.footer: list[Text] = []

    # -- layout ---------------------------------------------------------

    def layout(self) -> list[LayoutItem]:
        return build_layout(self.rows)

    def label_width(self) -> int:
        """Depends only on names, tree shape and width — never on live state."""
        items = self.layout()
        natural = max(len(i.prefix) + 2 + len(i.row.branch) for i in items)
        available = self.console.width - 2 - STATUS_COLUMN - 1
        return max(14, min(natural, available))

    def frontier(self) -> int:
        """Index of the first item that has not finished.

        Deliberately *not* "the row currently active": between finishing one
        branch and starting the next nothing is active, and anchoring on that
        forces a fallback that teleports the viewport for a frame — a visible
        flash on every transition. The frontier only moves forward.
        """
        items = self.layout()
        for index, item in enumerate(items):
            if item.row.state in (RowState.BASE, RowState.EXCLUDED):
                continue
            if item.row.state not in TERMINAL_STATES:
                return index
        return len(items) - 1

    def anchors_of(self, items: list[LayoutItem], index: int) -> set[int]:
        """Fork points and arm heads above ``index``.

        Pinning the *full* ancestry does not work: in a stack the ancestry is
        everything above, so it pins the whole tree and windowing never engages.
        Only branches that own a ├─/└─ group, and the arm heads those groups
        indent against, have to stay visible.
        """
        position = {item.row.branch: i for i, item in enumerate(items)}
        child_count: dict[str, int] = {}
        for item in items:
            if item.row.parent is not None:
                child_count[item.row.parent] = child_count.get(item.row.parent, 0) + 1

        found: set[int] = set()
        parent = items[index].row.parent
        while parent is not None and parent in position:
            grandparent = items[position[parent]].row.parent
            is_fork = child_count.get(parent, 0) > 1
            is_arm_head = (
                grandparent is not None and child_count.get(grandparent, 0) > 1
            )
            if is_fork or is_arm_head:
                found.add(position[parent])
            parent = grandparent
        return found

    def window(
        self,
        items: list[LayoutItem],
        *,
        anchor: int | None = None,
        budget: int,
        place: str = "bottom",
        dense: bool = False,
    ) -> set[int]:
        """Choose visible rows when the tree is taller than ``budget``."""
        count = len(items)
        everything = set(range(count))
        if count < 6 or len(self.assemble(items, everything, dense=dense)) <= budget:
            return everything

        active = self.frontier() if anchor is None else anchor
        # Fixed capacity, never "grow to fill the budget": growing means that
        # once the window reaches the last branch the freed lookahead expands it
        # upward while the active row descends, sliding it down the screen.
        capacity = max(3, (budget - 1) // (1 if dense else 2))
        if place == "center":
            start = max(0, min(active - capacity // 2, count - capacity))
            end = min(count, start + capacity)
        else:
            end = min(count, active + 2)
            start = max(0, end - capacity)

        keep = set(range(start, end))
        for index in list(keep):
            keep |= self.anchors_of(items, index)
        return keep

    def assemble(
        self,
        items: list[LayoutItem],
        keep: set[int],
        *,
        frame: int = 0,
        labels: tuple[str, str] = ("done", "queued"),
        dense: bool = False,
    ) -> list[Text]:
        """Render kept items; a collapsed run replaces the `│` leading into it."""
        width = self.label_width()
        lines: list[Text] = []
        hidden = 0
        for index, item in enumerate(items):
            if index not in keep:
                hidden += 1
                continue
            if hidden:
                lines.append(Text(f"  ⋮  {hidden} {labels[0]}", style=DIM))
            elif item.lead is not None and not dense:
                lines.append(Text(item.lead, style=DIM))
            hidden = 0
            lines.append(render_row(item, width, planning=self.planning, frame=frame))
            if item.row.detail:
                lines.append(Text(f"{item.prefix}│     {item.row.detail}", style=DIM))
        if hidden:
            lines.append(Text(f"  ⋮  {hidden} {labels[1]}", style=DIM))
        return lines

    def tree_lines(
        self,
        *,
        frame: int = 0,
        window: bool = False,
        anchor: int | None = None,
        budget: int | None = None,
        place: str = "bottom",
        labels: tuple[str, str] = ("done", "queued"),
        dense: bool = False,
    ) -> list[Text]:
        items = self.layout()
        keep = set(range(len(items)))
        if window and budget is not None:
            keep = self.window(
                items, anchor=anchor, budget=budget, place=place, dense=dense
            )
        return self.assemble(items, keep, frame=frame, labels=labels, dense=dense)

    # -- progress rendering ---------------------------------------------

    def counters(self) -> tuple[int, int]:
        done = sum(1 for r in self.rows if r.state in TERMINAL_STATES)
        total = sum(
            1 for r in self.rows if r.state not in (RowState.BASE, RowState.EXCLUDED)
        )
        return done, total

    def progress_footer(self) -> Text:
        if self.planning:
            # Nothing has run yet, so "0/8 · 0s" says nothing. Report the
            # selection instead, as the standalone plan tree did.
            # ACTIVE counts too: while planning it means "being looked up",
            # and the tally should not flicker as each branch is checked.
            selected = sum(
                1 for r in self.rows if r.state in (RowState.PENDING, RowState.ACTIVE)
            )
            excluded = sum(1 for r in self.rows if r.state is RowState.EXCLUDED)
            line = Text(f"  ● {selected} selected")
            if excluded:
                noun = "branch" if excluded == 1 else "branches"
                line.append(f" · ○ {excluded} upstack {noun} not selected")
            return line

        done, total = self.counters()
        elapsed = int(time.monotonic() - self.started_at)
        return Text(f"  {done}/{total} · {elapsed}s", style=DIM)

    def active_line(self, frame: int) -> Text:
        """Everything that matters on one line, for a very short terminal."""
        items = self.layout()
        row = items[self.frontier()].row
        line = Text()
        line.append_text(marker_text(row, planning=self.planning, frame=frame))
        line.append(" ")
        line.append(self.progress_footer().plain.strip(), style=DIM)
        line.append(" · ", style=DIM)
        line.append(elide(row.branch, max(12, self.console.width - 34)))
        if row.label is not None and row.label.plain:
            line.append(" · ", style=DIM)
            line.append_text(row.label)
        return line

    def progress_parts(
        self, frame: int, tier: str, height: int
    ) -> list[RenderableType] | None:
        """Build one progress tier, or None if it does not fit ``height``."""
        if tier == "minimal":
            return [self.active_line(frame)]

        blanks: list[RenderableType] = [Text("")] if tier == "full" else []
        budget = height - (2 + 2 * len(blanks))
        if budget < (7 if tier == "full" else 3):
            return None

        parts: list[RenderableType] = [Text(self.header), *blanks]
        parts.extend(self.tree_lines(frame=frame, window=True, budget=budget))
        parts.extend(blanks)
        parts.append(self.progress_footer())
        return parts if len(parts) <= height else None

    def render(self, frame: int, *, running: bool) -> RenderableType:
        if not running:
            # The final frame may be tall: Live switches to
            # vertical_overflow="visible" on stop, so it scrolls instead of
            # cropping. Truncating a transient progress frame is fine;
            # truncating the result would lose PR numbers for good.
            parts: list[RenderableType] = [Text(self.header), Text("")]
            parts.extend(self.tree_lines(frame=frame))
            parts.append(Text(""))
            parts.extend(self.footer)
            return Group(*parts)

        for tier in ("full", "compact", "minimal"):
            parts = self.progress_parts(frame, tier, self.console.height)
            if parts is not None:
                return Group(*parts)
        # unreachable: "minimal" is a single line and always fits
        return Group(  # pragma: no cover
            *self.progress_parts(frame, "minimal", self.console.height)
        )


class Working(Live):
    """A transient spinner line while a blocking call runs.

    rich-toolkit's own progress animates by fading the title's colour rather
    than drawing a glyph, which does not read as a loader. This uses the same
    marker the stack view spins, so waiting looks the same everywhere.
    """

    def __init__(self, console: Console, message: str) -> None:
        #: Reassign to describe a later phase. One block spanning several waits
        #: leaves one blank line behind; a block per wait leaves one each.
        self.message = message
        self.started_at = time.monotonic()
        super().__init__(console=console, refresh_per_second=8, transient=True)

    def __enter__(self) -> Working:
        # Force the first frame. Live only renders on enter when it already
        # has a renderable, so a wait that finished quickly drew nothing and
        # skipped the newline it emits on stop — leaving the blank line after
        # this block dependent on how long the work happened to take.
        self.start(refresh=True)
        return self

    def get_renderable(self) -> RenderableType:
        elapsed = time.monotonic() - self.started_at
        frame = int(elapsed / (SPINNER_INTERVAL_MS / 1000))
        line = Text("  ")
        line.append(SPINNER[frame % len(SPINNER)], style=Style(color="cyan"))
        line.append(f" {self.message}", style=DIM)
        # A leading blank so the gap under a caller's header is there while
        # this runs, not only afterwards when Live's stop-newline supplies one.
        return Group(Text(""), line)


class LiveStackView(Progress):
    """TTY view: rewrites the block in place.

    Subclasses ``Progress`` purely for the ``Live`` machinery (start/stop, the
    refresh thread, JSON quieting); ``get_renderable`` is ours.
    """

    def __init__(self, renderer: StackRenderer, console: Console) -> None:
        self.renderer = renderer
        super().__init__(title=renderer.header, console=console, style=MinimalStyle())
        self.vertical_overflow = "ellipsis"

    def get_renderable(self) -> RenderableType:
        elapsed = time.monotonic() - self.renderer.started_at
        frame = int(elapsed / (SPINNER_INTERVAL_MS / 1000))
        return self.renderer.render(frame, running=self._started)

    def sync(self) -> None:
        """No-op: Live's refresh thread already repaints."""

    def finish(self, footer: list[Text]) -> None:
        self.renderer.footer = footer


class AppendStackView:
    """Non-TTY view: same grammar, printed as each row finishes.

    ``Live`` prints only a final frame when piped, so a long run would sit
    silent and then dump everything at once. This streams instead.
    """

    def __init__(self, renderer: StackRenderer, console: Console) -> None:
        self.renderer = renderer
        self.console = console
        self._printed: set[str] = set()

    def __enter__(self) -> AppendStackView:
        self.console.print(Text(self.renderer.header))
        self.console.print("")
        return self

    def __exit__(self, *exc: object) -> None:
        self.console.print("")
        for line in self.renderer.footer:
            self.console.print(line)

    def sync(self) -> None:
        width = self.renderer.label_width()
        for item in self.renderer.layout():
            row = item.row
            if row.state in TERMINAL_STATES and row.branch not in self._printed:
                self._printed.add(row.branch)
                self.console.print(
                    render_row(item, width, planning=self.renderer.planning, frame=0)
                )
                if row.detail:
                    self.console.print(Text(f"        {row.detail}", style=DIM))

    def finish(self, footer: list[Text]) -> None:
        self.renderer.footer = footer


class SilentStackView:
    """JSON mode: renders nothing. The command emits one document itself."""

    def __init__(self, renderer: StackRenderer) -> None:
        self.renderer = renderer

    def __enter__(self) -> SilentStackView:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def sync(self) -> None:
        """Nothing is drawn in JSON mode."""

    def finish(self, footer: list[Text]) -> None:
        self.renderer.footer = footer

"""The one question `sc sync` asks before deleting anything.

Replaces three separate loops that each asked `[y/n]` per branch — locally
merged, merged on GitHub, closed PR. You approved each deletion in isolation
and only learned afterwards that it had also reparented other branches and
removed worktrees.

Everything is stated up front instead, one action per section, and the choice
is about *local copies*: for a merged branch the commits are already in the
trunk, so the local branch is a redundant copy. That is not true of a closed PR
whose remote branch is also gone, which is the only case where deleting loses
work — so that case gets a warning and an option that skips it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.style import Style
from rich.text import Text
from rich_toolkit._input_handler import TextInputHandler
from rich_toolkit.container import getchar
from rich_toolkit.menu import Menu

from shortcake._stack_view import DIM

MERGED = "merged"
SQUASH_MERGED = "squash-merged"
CLOSED = "closed"


@dataclass
class StaleBranch:
    """A branch `sync` proposes to delete locally."""

    branch: str
    reason: str
    pr: int | None = None
    worktrees: list[str] = field(default_factory=list)
    pushed: bool = True
    """Whether origin still has this branch.

    Decides whether deleting the local copy loses anything. Only trustworthy
    because `fetch_remote` prunes; a stale remote-tracking ref would report a
    deleted branch as still pushed.
    """


def is_lossy(stale: StaleBranch) -> bool:
    """Whether deleting this local branch actually loses work.

    Merged is safe — the commits are in the trunk. A closed PR is also safe
    while origin still has the branch, since the work is recoverable. Only a
    closed PR with no remote copy leaves the local branch as the only one.
    """
    return stale.reason == CLOSED and not stale.pushed


def reason_label(stale: StaleBranch, trunk: str) -> Text:
    """Why this branch is a candidate, for the status column."""
    if stale.reason == MERGED:
        text = Text(f"merged into {trunk}", style=DIM)
    elif stale.reason == SQUASH_MERGED:
        text = Text("squash-merged", style=DIM)
        if stale.pr:
            text.append(f" · #{stale.pr}", style=Style(color="cyan"))
    else:
        text = Text("closed", style=Style(color="yellow"))
        if stale.pr:
            text.append(f" · #{stale.pr}", style=Style(color="cyan"))
    if stale.worktrees:
        text.append(" · worktree", style=DIM)
    return text


def review_options(stale: list[StaleBranch]) -> list[tuple[str, str]]:
    """The choices. A third appears only when something would be lost."""
    safe = [s for s in stale if not is_lossy(s)]
    lossy = [s for s in stale if is_lossy(s)]

    label = "Delete it" if len(stale) == 1 else f"Delete all {len(stale)}"
    options = [("all", label)]
    if safe and lossy:
        options.append(("safe", f"Safe only ({len(safe)})"))
    options.append(("none", "Keep everything"))
    options.append(("cancel", "Cancel"))
    return options


def breakdown(
    stale: list[StaleBranch],
    movers: list[str],
    trunk: str,
) -> list[RenderableType]:
    """Each action as its own section, with the things it applies to.

    Not a summary line: "delete 2 · reparent 1" made every number a count with
    no subject — reparent what, onto what.
    """
    safe = [s for s in stale if not is_lossy(s)]
    lossy = [s for s in stale if is_lossy(s)]
    worktrees = [(s, path) for s in stale for path in s.worktrees]

    names = [s.branch for s in stale]
    width = max((len(name) for name in names), default=20) + 2
    out: list[RenderableType] = []

    def section(title: str) -> None:
        if out:
            out.append(Text(""))
        out.append(Text(f"  {title}", style=Style(bold=True)))

    noun = "branch" if len(stale) == 1 else "branches"
    section(f"Delete {len(stale)} local {noun}")
    for item in safe + lossy:
        line = Text("    ")
        line.append(item.branch.ljust(width))
        line.append_text(reason_label(item, trunk))
        if is_lossy(item):
            line.append("  ⚠ not on origin", style=Style(color="yellow"))
        line.rstrip()
        out.append(line)

    if worktrees:
        noun = "worktree" if len(worktrees) == 1 else "worktrees"
        section(f"Remove {len(worktrees)} {noun}")
        for item, path in worktrees:
            line = Text("    ")
            line.append(path, style=DIM)
            line.append("  for ", style=DIM)
            line.append(item.branch, style=DIM)
            out.append(line)

    # Reparenting is not a decision — declining it would orphan the branches —
    # so it gets a note rather than a section competing with the actual choice.
    # Said as "rebased" because it replays commits locally and can conflict,
    # unlike GitHub moving a PR's base pointer server-side.
    if movers:
        out.append(Text(""))
        out.append(
            Text(
                f"  {len(movers)} branch above is rebased onto its new parent."
                if len(movers) == 1
                else f"  {len(movers)} branches above are rebased onto their "
                "new parents.",
                style=DIM,
            )
        )
    return out


def render_review(
    stale: list[StaleBranch],
    movers: list[str],
    cursor: int,
    *,
    trunk: str,
) -> RenderableType:
    # No header here: sync prints one before the review opens, and drawing a
    # second put two of them on screen.
    parts: list[RenderableType] = []
    parts.extend(breakdown(stale, movers, trunk))
    parts.append(Text(""))

    lossy = [s for s in stale if is_lossy(s)]
    # Agree in number with the option beside it: "Delete the local copies?"
    # sitting above "Delete it" read as a mismatch.
    question = (
        "Delete the local copy?" if len(stale) == 1 else "Delete the local copies?"
    )
    if lossy:
        noun = "branch" if len(lossy) == 1 else "branches"
        question += f" {len(lossy)} {noun} would be gone for good."
    parts.append(Text(f"  {question}"))
    parts.append(Text(""))

    line = Text("  ")
    for index, (_, label) in enumerate(review_options(stale)):
        if index:
            line.append("   ·   ", style=DIM)
        if index == cursor:
            line.append(f"❯ {label}", style=Style(color="cyan", bold=True))  # noqa: RUF001
        else:
            line.append(label, style=DIM)
    parts.append(line)
    return Group(*parts)


def pick_cleanup(
    console: Console,
    stale: list[StaleBranch],
    movers: list[str],
    *,
    trunk: str,
) -> str:
    """Ask once. Returns ``all``, ``safe``, ``none`` or ``cancel``."""
    options = review_options(stale)
    cursor = 0
    with Live(console=console, auto_refresh=False, transient=True) as live:
        while True:
            live.update(render_review(stale, movers, cursor, trunk=trunk))
            live.refresh()
            try:
                key = getchar()
            except KeyboardInterrupt:  # pragma: no cover - needs a real tty
                return "cancel"
            if key in Menu.RIGHT_KEYS or key in Menu.DOWN_KEYS:
                cursor = (cursor + 1) % len(options)
            elif key in Menu.LEFT_KEYS or key in Menu.UP_KEYS:
                cursor = (cursor - 1) % len(options)
            elif key == TextInputHandler.ENTER_KEY:
                return options[cursor][0]
            elif key in ("q", "\x03", "\x04"):
                return "cancel"


def selected_branches(stale: list[StaleBranch], scope: str) -> list[str]:
    """Which branches the chosen scope deletes."""
    if scope == "all":
        return [s.branch for s in stale]
    if scope == "safe":
        return [s.branch for s in stale if not is_lossy(s)]
    return []

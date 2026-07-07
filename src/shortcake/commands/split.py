"""Split files out of the current branch into a new stacked branch."""

from pathlib import Path
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo, _oid
from shortcake._output import get_rich_toolkit
from shortcake._recap import RecapError, build_branch_patch
from shortcake.commands.move_lines import (
    HunkSelection,
    MoveError,
    SplitHunksResult,
    _split_hunks,
)


class SplitError(ShortcakeError):
    """Error during split operation."""

    pass


def _file_sections(patch: str) -> dict[str, str]:
    """Split a multi-file patch into per-file patches keyed by new path."""
    sections: dict[str, str] = {}
    current_path: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_path is not None:
            sections[current_path] = "\n".join(current_lines) + "\n"

    for line in patch.split("\n"):
        if line.startswith("diff --git a/"):
            flush()
            # Format: diff --git a/path b/path
            current_path = line.split(" b/", 1)[1]
            current_lines = [line]
        elif current_path is not None:
            current_lines.append(line)
    flush()
    return sections


def _count_hunks(file_patch: str) -> int:
    """Count the hunks in a single-file patch."""
    return sum(1 for line in file_patch.split("\n") if line.startswith("@@"))


def _tree_id(repo: Repo, branch: str) -> object:
    """Return the tree id at a branch's head."""
    return repo.get(_oid(git.get_branch_head(repo, branch))).tree_id


def _split(
    repo: Repo,
    files: list[str],
    message: str,
    placement: str = "before",
    no_verify: bool = False,
) -> SplitHunksResult:
    """Split whole files out of the current branch into a new branch.

    Raises SplitError on failure. Verifies afterwards that no content was
    lost: the combined stack still produces the exact tree the source branch
    had before the split.
    """
    source_branch = git.get_current_branch(repo)
    if source_branch is None:
        raise SplitError("Cannot split in detached HEAD state")

    all_branches = set(git.get_all_local_branches(repo))
    parent = git.get_branch_parent(repo, source_branch, all_branches)
    if parent is None:
        raise SplitError(
            f"Branch '{source_branch}' is not tracked by Shortcake. "
            f"Run 'sc adopt {source_branch} -p <parent>' to track it"
        )

    try:
        patch = build_branch_patch(Path(repo.workdir), parent, source_branch)
    except RecapError as exc:
        raise SplitError(str(exc)) from exc

    sections = _file_sections(patch)
    missing = [f for f in files if f not in sections]
    if missing:
        changed = ", ".join(sorted(sections)) or "none"
        raise SplitError(
            f"No changes for {', '.join(missing)} on '{source_branch}'. "
            f"Changed files: {changed}"
        )

    if set(files) == set(sections):
        raise SplitError(
            f"Cannot split all changed files out of '{source_branch}' — "
            "the branch would become empty. Use 'sc move' or rename instead."
        )

    hunks = [
        HunkSelection(file_path=f, file_patch=sections[f], hunk_index=i)
        for f in files
        for i in range(_count_hunks(sections[f]))
    ]

    original_tree = _tree_id(repo, source_branch)

    try:
        result = _split_hunks(
            repo,
            source_branch=source_branch,
            commit_message=message,
            placement=placement,
            hunks=hunks,
            no_verify=no_verify,
        )
    except MoveError as exc:
        raise SplitError(str(exc)) from exc

    # Integrity check: the top of the split pair must reproduce the original
    # tree exactly — a partition that silently drops content (e.g. a missed
    # file) must never survive. _split_hunks already rolled back on failure;
    # this guards against a "successful" but lossy split.
    top_branch = source_branch if placement == "before" else result.new_branch
    if _tree_id(repo, top_branch) != original_tree:  # pragma: no cover
        raise SplitError(
            f"Split verification failed: '{top_branch}' no longer matches the "
            f"original content of '{source_branch}'. Inspect the stack with "
            "'sc ls' and 'git diff' before continuing."
        )

    return result


def split(
    files: Annotated[
        list[str],
        typer.Argument(help="Files to move into the new branch.", show_default=False),
    ],
    message: Annotated[
        str,
        typer.Option("--message", "-m", help="Commit message for the new branch."),
    ],
    after: Annotated[
        bool,
        typer.Option(
            "--after",
            help="Place the new branch after (on top of) the current branch "
            "instead of before it.",
        ),
    ] = False,
    no_verify: Annotated[
        bool, typer.Option("--no-verify", "-n", help="Skip pre-commit hooks.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Output the result as JSON.")
    ] = False,
) -> None:
    """Split files out of the current branch into a new stacked branch."""
    repo = git.open_repo()
    toolkit = get_rich_toolkit(json_output=json_output)

    placement = "after" if after else "before"
    try:
        result = _split(repo, files, message, placement=placement, no_verify=no_verify)
    except SplitError as e:
        toolkit.fail("split_failed", str(e))

    if json_output:
        toolkit.success(
            {
                "source": result.source_branch,
                "new_branch": result.new_branch,
                "placement": result.placement,
                "files": result.file_paths,
                "restacked": result.restacked_branches,
            }
        )
        return

    relation = "before" if placement == "before" else "after"
    typer.echo(
        f"Split {len(files)} file(s) from '{result.source_branch}' into "
        f"'{result.new_branch}' ({relation} it)"
    )
    for branch in result.restacked_branches:
        typer.echo(f"Restacked '{branch}'")

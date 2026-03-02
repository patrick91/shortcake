"""Unified diff patch extraction.

Extract a sub-patch from a unified diff by selecting specific line ranges.
Used by the move-lines feature to split changes between branches.
"""

from __future__ import annotations

import re
from typing import Literal

from shortcake._exceptions import ShortcakeError

Side = Literal["additions", "deletions"]


class EmptyPatchError(ShortcakeError):
    """Raised when extraction produces no actual changes."""

    pass


def _parse_hunk_header(line: str) -> tuple[int, int, int, int, str]:
    """Parse a unified diff hunk header.

    Returns (old_start, old_count, new_start, new_count, trailing_text).
    """
    m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)", line)
    if not m:
        raise ValueError(f"Invalid hunk header: {line}")
    old_start = int(m.group(1))
    old_count = int(m.group(2)) if m.group(2) is not None else 1
    new_start = int(m.group(3))
    new_count = int(m.group(4)) if m.group(4) is not None else 1
    trailing = m.group(5)
    return old_start, old_count, new_start, new_count, trailing


def _build_hunk_header(
    old_start: int,
    old_count: int,
    new_start: int,
    new_count: int,
    trailing: str = "",
) -> str:
    """Build a unified diff hunk header line."""
    return f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{trailing}"


def extract_sub_patch(
    file_patch: str,
    start_line: int,
    end_line: int,
    side: Side,
) -> str:
    """Extract a sub-patch selecting lines in [start_line, end_line].

    For side='additions': selects '+' lines by their new-file line number.
    For side='deletions': selects '-' lines by their old-file line number.

    Non-selected '+' lines are dropped (don't exist in old file).
    Non-selected '-' lines are converted to context (exist in both files).
    Context lines are always kept.

    Raises EmptyPatchError if no changes remain after extraction.
    """
    lines = file_patch.split("\n")

    # Separate file headers from hunks
    file_headers: list[str] = []
    hunk_start_indices: list[int] = []

    for i, line in enumerate(lines):
        if line.startswith("@@"):
            hunk_start_indices.append(i)
            if not file_headers:
                file_headers = lines[:i]
        elif not hunk_start_indices:
            continue

    if not hunk_start_indices:
        raise EmptyPatchError("No hunks found in patch")

    if not file_headers:
        file_headers = lines[: hunk_start_indices[0]]

    # Parse each hunk and build sub-patch hunks
    result_hunks: list[str] = []

    for hunk_idx, hunk_line_idx in enumerate(hunk_start_indices):
        # Determine hunk boundaries
        if hunk_idx + 1 < len(hunk_start_indices):
            next_hunk_idx = hunk_start_indices[hunk_idx + 1]
        else:
            next_hunk_idx = len(lines)

        hunk_header = lines[hunk_line_idx]
        old_start, _old_count, new_start, _new_count, trailing = _parse_hunk_header(
            hunk_header
        )

        hunk_lines = lines[hunk_line_idx + 1 : next_hunk_idx]

        # Remove trailing empty line that's just an artifact of splitting
        while hunk_lines and hunk_lines[-1] == "":
            hunk_lines.pop()

        # Track line counters and build output lines
        old_line = old_start
        new_line = new_start
        out_lines: list[str] = []

        for hline in hunk_lines:
            if not hline and hline != " " and hline == "":
                # Completely empty trailing line in diff, skip
                continue
            prefix = hline[0] if hline else " "
            if prefix == " ":
                # Context line — always keep
                out_lines.append(hline)
                old_line += 1
                new_line += 1
            elif prefix == "+":
                if side == "additions":
                    if start_line <= new_line <= end_line:
                        out_lines.append(hline)  # keep as addition
                    else:
                        pass  # drop: doesn't exist in old file
                else:
                    # side == 'deletions': non-selected addition → drop
                    pass
                new_line += 1
            elif prefix == "-":
                if side == "deletions":
                    if start_line <= old_line <= end_line:
                        out_lines.append(hline)  # keep as deletion
                    else:
                        # Convert to context (line exists in both files)
                        out_lines.append(" " + hline[1:])
                else:
                    # side == 'additions': non-selected deletion → context
                    out_lines.append(" " + hline[1:])
                old_line += 1
            elif prefix == "\\":
                # "\ No newline at end of file" — keep with preceding line
                out_lines.append(hline)

        # Count actual changes in output
        has_changes = any(ln.startswith("+") or ln.startswith("-") for ln in out_lines)
        if not has_changes:
            continue

        # Recompute hunk header counts
        sub_old_count = sum(
            1 for ln in out_lines if ln.startswith(" ") or ln.startswith("-")
        )
        sub_new_count = sum(
            1 for ln in out_lines if ln.startswith(" ") or ln.startswith("+")
        )

        # Compute correct start lines for the sub-hunk
        sub_header = _build_hunk_header(
            old_start, sub_old_count, new_start, sub_new_count, trailing
        )
        result_hunks.append(sub_header + "\n" + "\n".join(out_lines))

    if not result_hunks:
        raise EmptyPatchError("No changes remain after extraction")

    result = "\n".join(file_headers) + "\n" + "\n".join(result_hunks) + "\n"
    return result

"""Branch suggestion algorithm for hunk-to-branch matching.

Given a source patch (working diff or branch diff) and patches for each
tracked branch, suggest which branch is the best target for each hunk
based on file creation, line overlap, and adjacency heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BranchFileTouch:
    """A record of how a branch touches a specific file."""

    branch: str
    file_path: str
    is_new_file: bool
    line_ranges: list[tuple[int, int]]


@dataclass(frozen=True)
class HunkSuggestion:
    """Suggestion result for a single source hunk."""

    file: str
    hunk_index: int
    suggested_branch: str | None
    reason: str  # "file_created" | "line_overlap" | "adjacent" | "file_only" | ""


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_HEADER_RE = re.compile(r"^diff --git a/.+ b/(.+)$")


def _parse_new_side_range(line: str) -> tuple[int, int]:
    """Extract (start, start+count-1) for the new side of a hunk header."""
    m = _HUNK_RE.match(line)
    if not m:
        raise ValueError(f"Invalid hunk header: {line}")
    start = int(m.group(3))
    count = int(m.group(4)) if m.group(4) is not None else 1
    end = start + count - 1 if count > 0 else start
    return start, end


def _parse_patch_file_touches(branch_name: str, patch: str) -> list[BranchFileTouch]:
    """Parse a unified diff to extract per-file touch info for a branch."""
    if not patch.strip():
        return []

    touches: list[BranchFileTouch] = []
    lines = patch.split("\n")

    current_file: str | None = None
    is_new_file = False
    line_ranges: list[tuple[int, int]] = []

    def _flush() -> None:
        nonlocal current_file, is_new_file, line_ranges
        if current_file is not None:
            touches.append(
                BranchFileTouch(
                    branch=branch_name,
                    file_path=current_file,
                    is_new_file=is_new_file,
                    line_ranges=list(line_ranges),
                )
            )
        current_file = None
        is_new_file = False
        line_ranges = []

    for line in lines:
        diff_m = _DIFF_HEADER_RE.match(line)
        if diff_m:
            _flush()
            current_file = diff_m.group(1)
            continue

        if current_file is not None and line.startswith("--- /dev/null"):
            is_new_file = True
            continue

        if current_file is not None and _HUNK_RE.match(line):
            start, end = _parse_new_side_range(line)
            line_ranges.append((start, end))

    _flush()
    return touches


def _ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Return the size of overlap between two ranges, or 0 if none."""
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    return max(0, end - start + 1)


def _ranges_adjacent(a: tuple[int, int], b: tuple[int, int], margin: int = 5) -> bool:
    """Return True if two ranges are within `margin` lines of each other."""
    if _ranges_overlap(a, b) > 0:
        return False
    gap = min(abs(a[0] - b[1]), abs(b[0] - a[1]))
    return gap <= margin


def _compute_suggestions(
    source_patch: str,
    branch_patches: dict[str, str],
    exclude_branch: str | None = None,
) -> list[HunkSuggestion]:
    """Compute branch suggestions for each hunk in the source patch.

    Args:
        source_patch: Unified diff of the hunks to suggest targets for.
        branch_patches: Mapping of branch name → unified diff patch.
        exclude_branch: Branch name to exclude from candidates.

    Returns:
        List of HunkSuggestion, one per hunk in source_patch.
    """
    if not source_patch.strip():
        return []

    # Parse branch touches
    all_touches: list[BranchFileTouch] = []
    for branch_name, patch in branch_patches.items():
        if branch_name == exclude_branch:
            continue
        all_touches.extend(_parse_patch_file_touches(branch_name, patch))

    # Build per-file index: file_path → list of BranchFileTouch
    file_index: dict[str, list[BranchFileTouch]] = {}
    for touch in all_touches:
        file_index.setdefault(touch.file_path, []).append(touch)

    # Parse source patch into per-file, per-hunk structures
    source_lines = source_patch.split("\n")
    suggestions: list[HunkSuggestion] = []

    current_file: str | None = None
    hunk_index_in_file = -1

    for line in source_lines:
        diff_m = _DIFF_HEADER_RE.match(line)
        if diff_m:
            current_file = diff_m.group(1)
            hunk_index_in_file = -1
            continue

        if current_file is not None and _HUNK_RE.match(line):
            hunk_index_in_file += 1
            hunk_start, hunk_end = _parse_new_side_range(line)
            hunk_range = (hunk_start, hunk_end)

            # Score each candidate branch
            branch_scores: dict[str, tuple[int, str]] = {}
            candidates = file_index.get(current_file, [])

            for touch in candidates:
                branch = touch.branch

                best_score = branch_scores.get(branch, (0, ""))[0]
                best_reason = branch_scores.get(branch, (0, ""))[1]

                # file_created: score 1000
                if touch.is_new_file and best_score < 1000:
                    best_score = 1000
                    best_reason = "file_created"

                # line_overlap: score 100 + overlap_size
                for br_range in touch.line_ranges:
                    overlap = _ranges_overlap(hunk_range, br_range)
                    if overlap > 0:
                        score = 100 + overlap
                        if score > best_score:
                            best_score = score
                            best_reason = "line_overlap"

                # adjacent: score 11-15 (proximity, beats file_only)
                for br_range in touch.line_ranges:
                    if _ranges_adjacent(hunk_range, br_range):
                        gap = min(
                            abs(hunk_range[0] - br_range[1]),
                            abs(br_range[0] - hunk_range[1]),
                        )
                        score = max(11, 16 - gap)
                        if score > best_score:
                            best_score = score
                            best_reason = "adjacent"

                # file_only: score 10
                if best_score == 0 and candidates:
                    best_score = 10
                    best_reason = "file_only"

                branch_scores[branch] = (best_score, best_reason)

            # Pick the winner
            if not branch_scores:
                suggestions.append(
                    HunkSuggestion(
                        file=current_file,
                        hunk_index=hunk_index_in_file,
                        suggested_branch=None,
                        reason="",
                    )
                )
            else:
                sorted_branches = sorted(
                    branch_scores.items(), key=lambda x: x[1][0], reverse=True
                )
                top_score = sorted_branches[0][1][0]
                top_branches = [b for b, (s, _) in sorted_branches if s == top_score]

                if len(top_branches) == 1 and top_score > 0:
                    winner = top_branches[0]
                    reason = branch_scores[winner][1]
                    suggestions.append(
                        HunkSuggestion(
                            file=current_file,
                            hunk_index=hunk_index_in_file,
                            suggested_branch=winner,
                            reason=reason,
                        )
                    )
                else:
                    # Tie at the top or zero score → no suggestion
                    suggestions.append(
                        HunkSuggestion(
                            file=current_file,
                            hunk_index=hunk_index_in_file,
                            suggested_branch=None,
                            reason="",
                        )
                    )

    return suggestions

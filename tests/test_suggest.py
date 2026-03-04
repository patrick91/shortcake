import pytest

from shortcake.commands._suggest import (
    _compute_suggestions,
    _parse_new_side_range,
    _parse_patch_file_touches,
)

# --- _parse_new_side_range ---


def test_parse_new_side_range_invalid_line() -> None:
    """Non-hunk-header line raises ValueError."""
    with pytest.raises(ValueError, match="Invalid hunk header"):
        _parse_new_side_range("not a hunk header")


# --- _parse_patch_file_touches ---


def test_parse_patch_new_file() -> None:
    """Detects new file creation from --- /dev/null."""
    patch = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
    )
    touches = _parse_patch_file_touches("feat-branch", patch)
    assert len(touches) == 1
    assert touches[0].branch == "feat-branch"
    assert touches[0].file_path == "new.txt"
    assert touches[0].is_new_file is True
    assert touches[0].line_ranges == [(1, 3)]


def test_parse_patch_modified_file() -> None:
    """Extracts line ranges from modified file hunks."""
    patch = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -10,5 +10,7 @@\n"
        " context\n"
        "+added1\n"
        "+added2\n"
        " context\n"
        "@@ -30,3 +32,4 @@\n"
        " context\n"
        "+another\n"
    )
    touches = _parse_patch_file_touches("fix-branch", patch)
    assert len(touches) == 1
    assert touches[0].file_path == "src/app.py"
    assert touches[0].is_new_file is False
    assert touches[0].line_ranges == [(10, 16), (32, 35)]


def test_parse_patch_multiple_files() -> None:
    """Parses patches with multiple files."""
    patch = (
        "diff --git a/a.txt b/a.txt\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        "+new\n"
        "diff --git a/b.txt b/b.txt\n"
        "--- a/b.txt\n"
        "+++ b/b.txt\n"
        "@@ -5,2 +5,3 @@\n"
        " line5\n"
        "+added\n"
    )
    touches = _parse_patch_file_touches("branch", patch)
    assert len(touches) == 2
    assert touches[0].file_path == "a.txt"
    assert touches[1].file_path == "b.txt"


def test_parse_patch_empty() -> None:
    """Empty patch returns no touches."""
    assert _parse_patch_file_touches("branch", "") == []
    assert _parse_patch_file_touches("branch", "  ") == []


# --- _compute_suggestions ---


def _make_new_file_patch(file_path: str, lines: int = 3) -> str:
    """Helper to create a new-file patch."""
    line_content = "".join(f"+line{i}\n" for i in range(1, lines + 1))
    return (
        f"diff --git a/{file_path} b/{file_path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{file_path}\n"
        f"@@ -0,0 +1,{lines} @@\n"
        f"{line_content}"
    )


def _make_modify_patch(
    file_path: str, start: int = 10, count: int = 5, added: int = 2
) -> str:
    """Helper to create a modify-file patch."""
    context = "".join(f" ctx{i}\n" for i in range(count - added))
    additions = "".join(f"+add{i}\n" for i in range(added))
    new_count = count + added
    return (
        f"diff --git a/{file_path} b/{file_path}\n"
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        f"@@ -{start},{count} +{start},{new_count} @@\n"
        f"{context}{additions}"
    )


def test_new_file_suggests_creating_branch() -> None:
    """Branch that created a file gets file_created suggestion."""
    source = _make_modify_patch("new.txt", start=1, count=3, added=1)
    branch_patches = {
        "feat-a": _make_new_file_patch("new.txt"),
        "feat-b": _make_modify_patch("other.txt"),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 1
    assert result[0].suggested_branch == "feat-a"
    assert result[0].reason == "file_created"


def test_line_overlap_suggests_branch() -> None:
    """Branch with overlapping line ranges gets line_overlap suggestion."""
    source = _make_modify_patch("app.py", start=10, count=5, added=2)
    branch_patches = {
        "feat-a": _make_modify_patch("app.py", start=8, count=5, added=3),
        "feat-b": _make_modify_patch("app.py", start=50, count=3, added=1),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 1
    assert result[0].suggested_branch == "feat-a"
    assert result[0].reason == "line_overlap"


def test_adjacent_lines_suggest_branch() -> None:
    """Branch modifying adjacent lines (within 5) gets adjacent suggestion."""
    # Source hunk at lines 20-24
    source = _make_modify_patch("app.py", start=20, count=3, added=2)
    # Branch hunk at lines 10-14 (6 lines away from 20 → not adjacent)
    # Branch hunk at lines 26-28 (just 1 line away from 24 → adjacent)
    branch_patches = {
        "far-branch": _make_modify_patch("app.py", start=50, count=3, added=1),
        "near-branch": _make_modify_patch("app.py", start=26, count=3, added=1),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 1
    assert result[0].suggested_branch == "near-branch"
    assert result[0].reason == "adjacent"


def test_file_only_match() -> None:
    """Branch touching same file but no line proximity gets file_only."""
    source = _make_modify_patch("app.py", start=100, count=3, added=1)
    branch_patches = {
        "feat-a": _make_modify_patch("app.py", start=10, count=3, added=1),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 1
    assert result[0].suggested_branch == "feat-a"
    assert result[0].reason == "file_only"


def test_no_match_returns_no_suggestion() -> None:
    """No branch touches the file → no suggestion."""
    source = _make_modify_patch("unique.py", start=1, count=3, added=1)
    branch_patches = {
        "feat-a": _make_modify_patch("other.py", start=1, count=3, added=1),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 1
    assert result[0].suggested_branch is None
    assert result[0].reason == ""


def test_clear_winner_among_multiple_branches() -> None:
    """When one branch has a higher score, it wins."""
    source = _make_modify_patch("app.py", start=10, count=5, added=2)
    branch_patches = {
        # file_created beats line_overlap
        "creator": _make_new_file_patch("app.py"),
        "modifier": _make_modify_patch("app.py", start=10, count=5, added=3),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 1
    assert result[0].suggested_branch == "creator"
    assert result[0].reason == "file_created"


def test_ambiguous_tie_returns_no_suggestion() -> None:
    """When two branches tie on score, no suggestion is made."""
    source = _make_modify_patch("app.py", start=10, count=5, added=2)
    branch_patches = {
        "feat-a": _make_modify_patch("app.py", start=10, count=5, added=2),
        "feat-b": _make_modify_patch("app.py", start=10, count=5, added=2),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 1
    assert result[0].suggested_branch is None


def test_exclude_branch_is_excluded() -> None:
    """Excluded branch is not considered as a candidate."""
    source = _make_modify_patch("app.py", start=10, count=5, added=2)
    branch_patches = {
        "self-branch": _make_new_file_patch("app.py"),
        "other-branch": _make_modify_patch("app.py", start=10, count=3, added=1),
    }
    result = _compute_suggestions(source, branch_patches, exclude_branch="self-branch")
    assert len(result) == 1
    assert result[0].suggested_branch == "other-branch"


def test_empty_source_patch() -> None:
    """Empty source patch returns no suggestions."""
    result = _compute_suggestions("", {"a": _make_modify_patch("x.py")})
    assert result == []


def test_multiple_hunks_in_source() -> None:
    """Each hunk in source gets its own suggestion."""
    source = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -10,3 +10,4 @@\n"
        " ctx\n"
        "+add1\n"
        " ctx\n"
        "@@ -50,3 +51,4 @@\n"
        " ctx\n"
        "+add2\n"
        " ctx\n"
    )
    branch_patches = {
        "feat-a": _make_modify_patch("app.py", start=10, count=3, added=1),
        "feat-b": _make_modify_patch("app.py", start=50, count=3, added=1),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 2
    assert result[0].hunk_index == 0
    assert result[0].suggested_branch == "feat-a"
    assert result[1].hunk_index == 1
    assert result[1].suggested_branch == "feat-b"


def test_multiple_files_in_source() -> None:
    """Hunks across multiple files each get suggestions."""
    source = _make_modify_patch(
        "a.py", start=10, count=3, added=1
    ) + _make_modify_patch("b.py", start=20, count=3, added=1)
    branch_patches = {
        "feat-a": _make_modify_patch("a.py", start=10, count=3, added=1),
        "feat-b": _make_modify_patch("b.py", start=20, count=3, added=1),
    }
    result = _compute_suggestions(source, branch_patches)
    assert len(result) == 2
    assert result[0].file == "a.py"
    assert result[0].suggested_branch == "feat-a"
    assert result[1].file == "b.py"
    assert result[1].suggested_branch == "feat-b"

import pytest

from shortcake._git._patch import EmptyPatchError, extract_sub_patch

# --- Fixtures: sample patches ---

SINGLE_HUNK_ADD = """\
diff --git a/src/example.py b/src/example.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/example.py
@@ -0,0 +1,5 @@
+line 1
+line 2
+line 3
+line 4
+line 5"""

MODIFY_PATCH = """\
diff --git a/src/example.py b/src/example.py
index a1b2c3d..d4e5f6a 100644
--- a/src/example.py
+++ b/src/example.py
@@ -1,7 +1,8 @@
 context before
-old line 1
-old line 2
+new line 1
+new line 2
+new line 3
 context middle
-old line 3
+new line 4
 context after"""

MULTI_HUNK_PATCH = """\
diff --git a/src/example.py b/src/example.py
index a1b2c3d..d4e5f6a 100644
--- a/src/example.py
+++ b/src/example.py
@@ -1,5 +1,5 @@
 header
-old first
+new first
 middle
 footer
 end
@@ -10,5 +10,6 @@
 section two start
-removed line
+added line A
+added line B
 section two middle
 section two end"""

DELETION_PATCH = """\
diff --git a/src/example.py b/src/example.py
index a1b2c3d..d4e5f6a 100644
--- a/src/example.py
+++ b/src/example.py
@@ -1,7 +1,4 @@
 context
-deleted 1
-deleted 2
-deleted 3
 more context
 end context
 trailing"""

NEW_FILE_PATCH = """\
diff --git a/src/new_file.py b/src/new_file.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/new_file.py
@@ -0,0 +1,3 @@
+alpha
+beta
+gamma"""

DELETED_FILE_PATCH = """\
diff --git a/src/old_file.py b/src/old_file.py
deleted file mode 100644
index a1b2c3d..0000000
--- a/src/old_file.py
+++ /dev/null
@@ -1,3 +0,0 @@
-alpha
-beta
-gamma"""


def test_full_hunk_selection_additions() -> None:
    """Selecting all additions keeps the full patch."""
    result = extract_sub_patch(SINGLE_HUNK_ADD, 1, 5, "additions")
    assert "+line 1" in result
    assert "+line 2" in result
    assert "+line 3" in result
    assert "+line 4" in result
    assert "+line 5" in result
    assert "@@" in result


def test_partial_hunk_additions() -> None:
    """Selecting some additions keeps only those; others are dropped."""
    # Select only lines 2-3 (new-file line numbers)
    result = extract_sub_patch(SINGLE_HUNK_ADD, 2, 3, "additions")
    assert "+line 2" in result
    assert "+line 3" in result
    assert "+line 1" not in result
    assert "+line 4" not in result
    assert "+line 5" not in result


def test_modify_patch_select_some_additions() -> None:
    """Select some additions, drop others, keep deletions as context."""
    # New-file line numbers:
    #   1 = "context before"
    #   2 = "new line 1"
    #   3 = "new line 2"
    #   4 = "new line 3"
    #   5 = "context middle"
    #   6 = "new line 4"
    #   7 = "context after"
    # Selecting new-file lines 2-3 picks "new line 1" and "new line 2"
    result = extract_sub_patch(MODIFY_PATCH, 2, 3, "additions")
    assert "+new line 1" in result
    assert "+new line 2" in result
    # "new line 3" is at new-file line 4, outside [2,3] → dropped
    assert "+new line 3" not in result
    # "new line 4" is at new-file line 6, outside [2,3] → dropped
    assert "+new line 4" not in result
    # Deletions become context since we're selecting additions side
    assert " old line 1" in result
    assert " old line 2" in result


def test_deletion_side_selection() -> None:
    """Selecting deletions keeps those as '-', converts non-selected to context."""
    # old-file lines: 1=context, 2=deleted 1, 3=deleted 2, 4=deleted 3
    # Select old-file lines 2-3
    result = extract_sub_patch(DELETION_PATCH, 2, 3, "deletions")
    assert "-deleted 1" in result
    assert "-deleted 2" in result
    # deleted 3 at old line 4 is outside range → becomes context
    assert " deleted 3" in result
    assert "-deleted 3" not in result


def test_multi_hunk_selection_in_one() -> None:
    """Selecting lines in one hunk; other hunk has no changes → dropped."""
    # Second hunk: new-file lines 11=added line A, 12=added line B
    result = extract_sub_patch(MULTI_HUNK_PATCH, 11, 12, "additions")
    assert "+added line A" in result
    assert "+added line B" in result
    # First hunk should be dropped (new first at line 2 is outside 11-12)
    # The first hunk's +new first should not appear
    assert "+new first" not in result


def test_selection_spanning_hunks() -> None:
    """Selecting a range that spans both hunks keeps changes from both."""
    # First hunk: +new first at new line 2
    # Second hunk: +added line A at new line 11, +added line B at new line 12
    result = extract_sub_patch(MULTI_HUNK_PATCH, 1, 12, "additions")
    assert "+new first" in result
    assert "+added line A" in result
    assert "+added line B" in result


def test_empty_result_raises() -> None:
    """Selecting range with no changes raises EmptyPatchError."""
    # Select lines 100-200 which don't exist
    with pytest.raises(EmptyPatchError):
        extract_sub_patch(MODIFY_PATCH, 100, 200, "additions")


def test_new_file_patch() -> None:
    """New file patches work correctly."""
    result = extract_sub_patch(NEW_FILE_PATCH, 1, 2, "additions")
    assert "+alpha" in result
    assert "+beta" in result
    assert "+gamma" not in result
    assert "new file mode 100644" in result


def test_deleted_file_patch() -> None:
    """Deleted file patches work with deletion side."""
    result = extract_sub_patch(DELETED_FILE_PATCH, 1, 2, "deletions")
    assert "-alpha" in result
    assert "-beta" in result
    # gamma at old line 3 is outside range → context
    assert " gamma" in result
    assert "-gamma" not in result


def test_file_headers_preserved() -> None:
    """File headers (diff --git, index, ---, +++) are preserved."""
    result = extract_sub_patch(MODIFY_PATCH, 1, 10, "additions")
    assert "diff --git a/src/example.py b/src/example.py" in result
    assert "index a1b2c3d..d4e5f6a 100644" in result
    assert "--- a/src/example.py" in result
    assert "+++ b/src/example.py" in result


def test_hunk_header_recomputed() -> None:
    """Hunk header counts are recomputed for the sub-patch."""
    # Select only 1 of 5 additions → fewer new lines
    result = extract_sub_patch(SINGLE_HUNK_ADD, 3, 3, "additions")
    # Should have 1 new line
    assert "+line 3" in result
    assert "@@ -0,0 +1,1 @@" in result


def test_no_hunks_raises() -> None:
    """Patch with no hunks raises EmptyPatchError."""
    headeronly = "diff --git a/f b/f\nindex 000..111\n--- a/f\n+++ b/f\n"
    with pytest.raises(EmptyPatchError):
        extract_sub_patch(headeronly, 1, 5, "additions")

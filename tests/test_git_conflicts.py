import pytest

from shortcake import _git as git
from shortcake._git._core import ConflictedIndexEntry
from tests._git_helpers import Repo


def test_get_conflict_files_no_conflicts(temp_repo: Repo) -> None:
    """Test get_conflict_files with no conflicts."""
    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_index_exception(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles index open exception."""

    def mock_open_index():
        raise OSError("Index failed")

    monkeypatch.setattr(temp_repo, "open_index", mock_open_index)

    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_with_conflicts(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files returns conflicted files."""

    class MockIndex:
        def items(self):
            return [
                (b"normal.txt", object()),
                (b"conflict1.txt", ConflictedIndexEntry()),
                (b"conflict2.txt", ConflictedIndexEntry()),
            ]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict1.txt", "conflict2.txt"]

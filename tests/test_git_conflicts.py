import pytest
from dulwich.repo import Repo

from shortcake import _git as git


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
    from dulwich.index import ConflictedIndexEntry, IndexEntry

    class MockIndex:
        def items(self):
            return [
                (b"normal.txt", IndexEntry(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, b"")),
                (b"conflict1.txt", ConflictedIndexEntry()),
                (b"conflict2.txt", ConflictedIndexEntry()),
            ]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict1.txt", "conflict2.txt"]

import pytest

from shortcake import _git as git
from tests._git_helpers import Repo


def test_get_conflict_files_no_conflicts(temp_repo: Repo) -> None:
    """Test get_conflict_files with no conflicts."""
    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_index_exception(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles index read exception."""

    class BrokenIndex:
        def read(self):
            raise OSError("Index failed")

    monkeypatch.setattr(type(temp_repo), "index", property(lambda self: BrokenIndex()))

    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_with_conflicts(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files returns conflicted files."""

    class FakeEntry:
        """Fake pygit2 IndexEntry with a path attribute."""

        def __init__(self, path: str):
            self.path = path

    class FakeConflicts:
        """Fake conflicts iterator yielding (ancestor, ours, theirs) tuples."""

        def __bool__(self):
            return True

        def __iter__(self):
            # pygit2 index.conflicts yields (ancestor, ours, theirs) tuples
            yield (None, FakeEntry("conflict1.txt"), FakeEntry("conflict1.txt"))
            yield (None, FakeEntry("conflict2.txt"), FakeEntry("conflict2.txt"))

    class FakeIndex:
        conflicts = FakeConflicts()

        def read(self):
            pass

    monkeypatch.setattr(type(temp_repo), "index", property(lambda self: FakeIndex()))

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict1.txt", "conflict2.txt"]

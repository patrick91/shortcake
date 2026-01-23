from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git


def test_open_repo_current_dir(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test opening repo from current directory."""
    monkeypatch.chdir(tmp_path)
    repo = git.open_repo()
    # Just verify it opens without error
    assert repo is not None


def test_open_repo_with_path(temp_repo: Repo, tmp_path: Path) -> None:
    """Test opening repo with explicit path."""
    repo = git.open_repo(tmp_path)
    assert repo is not None


def test_get_current_branch(repo_with_feature: Repo) -> None:
    """Test getting current branch name."""
    branch = git.get_current_branch(repo_with_feature)
    assert branch == "feature"


def test_get_current_branch_detached_head(temp_repo: Repo) -> None:
    """Test error when in detached HEAD state."""
    # Get the commit SHA and write it directly to HEAD file
    head_sha = temp_repo.refs[b"refs/heads/main"]
    # Write raw SHA to HEAD (not a symbolic ref)
    head_path = Path(temp_repo.controldir()) / "HEAD"
    head_path.write_bytes(head_sha.hex().encode() + b"\n")

    assert git.get_current_branch(temp_repo) is None


def test_get_default_branch_from_origin_head(temp_repo: Repo) -> None:
    """Test getting default branch from origin/HEAD."""
    # Set up origin/HEAD pointing to main
    temp_repo.refs[b"refs/remotes/origin/main"] = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs.set_symbolic_ref(
        b"refs/remotes/origin/HEAD", b"refs/remotes/origin/main"
    )

    default = git.get_default_branch(temp_repo)
    assert default == "main"


def test_get_default_branch_fallback_main(temp_repo: Repo) -> None:
    """Test fallback to main when origin/HEAD not set."""
    default = git.get_default_branch(temp_repo)
    assert default == "main"


def test_get_default_branch_fallback_master(tmp_path: Path) -> None:
    """Test fallback to master when main doesn't exist."""
    repo = Repo.init(tmp_path, default_branch=b"master")
    # Need to create a commit for the branch to exist
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    default = git.get_default_branch(repo)
    assert default == "master"


def test_get_default_branch_none(tmp_path: Path) -> None:
    """Test None when no default branch can be determined."""
    repo = Repo.init(tmp_path, default_branch=b"develop")
    # Create commit so develop branch exists
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    default = git.get_default_branch(repo)
    assert default is None


# ============================================================================
# Tests for get_rebase_commits edge cases
# ============================================================================


def test_get_rebase_commits_same_commit(temp_repo: Repo) -> None:
    """Test get_rebase_commits returns empty when head equals merge_base."""
    head_sha = temp_repo.refs[b"refs/heads/main"]
    commits = git.get_rebase_commits(temp_repo, head_sha, head_sha)
    assert commits == []


def test_get_rebase_commits_no_parents(temp_repo: Repo) -> None:
    """Test get_rebase_commits handles root commit (no parents)."""
    # The initial commit has no parents - walk should stop there
    head_sha = temp_repo.refs[b"refs/heads/main"]
    # Use a non-existent merge_base that won't be found
    # The function should stop when it runs out of parents
    fake_base = b"0" * 40
    # This will walk to root and hit the "no parents" break
    commits = git.get_rebase_commits(temp_repo, head_sha, fake_base)
    # Should return the initial commit since it never found the base
    assert len(commits) >= 1


# ============================================================================
# Tests for helper functions
# ============================================================================


def test_decode_path_bytes() -> None:
    """Test _decode_path with bytes input."""
    from shortcake._git import _decode_path

    assert _decode_path(b"path/to/file.txt") == "path/to/file.txt"


def test_decode_path_string() -> None:
    """Test _decode_path with string input."""
    from shortcake._git import _decode_path

    assert _decode_path("path/to/file.txt") == "path/to/file.txt"


def test_decode_path_other() -> None:
    """Test _decode_path with other types."""
    from shortcake._git import _decode_path

    assert _decode_path(123) == "123"


def test_normalize_paths_none() -> None:
    """Test _normalize_paths with None."""
    from shortcake._git import _normalize_paths

    assert _normalize_paths(None) == []


def test_normalize_paths_dict() -> None:
    """Test _normalize_paths with dict."""
    from shortcake._git import _normalize_paths

    result = _normalize_paths({"a": b"file1.txt", "b": b"file2.txt"})
    assert sorted(result) == ["file1.txt", "file2.txt"]


def test_normalize_paths_list() -> None:
    """Test _normalize_paths with list."""
    from shortcake._git import _normalize_paths

    result = _normalize_paths([b"file1.txt", b"file2.txt"])
    assert sorted(result) == ["file1.txt", "file2.txt"]


def test_normalize_paths_nested() -> None:
    """Test _normalize_paths with nested structures."""
    from shortcake._git import _normalize_paths

    result = _normalize_paths({"key": [b"file1.txt", {b"file2.txt"}]})
    assert sorted(result) == ["file1.txt", "file2.txt"]


def test_normalize_paths_removes_empty() -> None:
    """Test _normalize_paths filters out empty strings."""
    from shortcake._git import _normalize_paths

    result = _normalize_paths([b"file.txt", b"", ""])
    assert result == ["file.txt"]


# ============================================================================
# Tests for cherry_pick
# ============================================================================


def test_cherry_pick_success(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test cherry_pick copies a commit."""
    # Get the feature commit SHA
    feature_sha = repo_with_feature.refs[b"refs/heads/feature"]

    # Switch to main
    porcelain.switch(repo_with_feature, "main")
    original_main_sha = repo_with_feature.refs[b"refs/heads/main"]

    # Cherry-pick the feature commit
    git.cherry_pick(repo_with_feature, feature_sha)

    # Main should have moved
    new_main_sha = repo_with_feature.refs[b"refs/heads/main"]
    assert new_main_sha != original_main_sha


# ============================================================================
# Tests for get_conflict_files edge cases
# ============================================================================


def test_get_conflict_files_no_conflicts(temp_repo: Repo) -> None:
    """Test get_conflict_files with no conflicts."""
    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_status_exception(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles status exception."""

    def mock_status(repo):
        raise RuntimeError("Status failed")

    monkeypatch.setattr(porcelain, "status", mock_status)

    # Should return empty list when status fails
    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_with_unstaged(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files returns unstaged files as fallback."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = [b"modified.txt"]

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    files = git.get_conflict_files(temp_repo)
    assert files == ["modified.txt"]


def test_get_conflict_files_index_iterconflicts(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files uses index.iterconflicts if available."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockIndex:
        def iterconflicts(self):
            # Return tuples where first element is path
            return [(b"conflict1.txt", None, None), (b"conflict2.txt", None, None)]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert sorted(files) == ["conflict1.txt", "conflict2.txt"]


def test_get_conflict_files_index_exception(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles index open exception."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    def mock_open_index():
        raise RuntimeError("Index failed")

    monkeypatch.setattr(temp_repo, "open_index", mock_open_index)

    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_index_items(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files uses index.items() for staged entries."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockEntry:
        # Stage bits are in flags bits 12-13, stage 1 = conflict
        flags = 0x1000  # stage 1

    class MockIndex:
        # No iterconflicts/conflicts methods so it falls through to items()
        def items(self):
            # Key is (path, stage) tuple
            return [((b"conflict.txt", 1), MockEntry())]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict.txt"]


def test_get_conflict_files_index_items_from_flags(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files extracts stage from entry flags."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockEntry:
        # Stage 2 in bits 12-13 = 0x2000
        flags = 0x2000

    class MockIndex:
        # No iterconflicts/conflicts methods
        def items(self):
            # Path without stage tuple - stage extracted from flags
            return [(b"conflict.txt", MockEntry())]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict.txt"]


def test_get_conflict_files_index_iteritems(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files uses iteritems fallback."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockEntry:
        flags = 0x1000  # stage 1

    class MockIndex:
        # No iterconflicts/conflicts/items methods, only iteritems
        def iteritems(self):
            return [(b"conflict.txt", MockEntry())]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict.txt"]


def test_get_conflict_files_index_items_none(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles missing items method."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockIndex:
        pass  # No iterconflicts/conflicts/items/iteritems methods

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_index_items_bad_entry(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles non-tuple entries."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockIndex:
        # No iterconflicts/conflicts methods
        def items(self):
            return ["not a tuple"]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    # Should handle gracefully
    files = git.get_conflict_files(temp_repo)
    assert files == []


# ============================================================================
# Tests for rebase functions
# ============================================================================


def test_rebase_failure_class() -> None:
    """Test RebaseFailure exception."""
    from shortcake._git import RebaseFailure

    err = RebaseFailure("test error")
    assert str(err) == "test error"


def test_get_cherry_pick_head_none(temp_repo: Repo) -> None:
    """Test get_cherry_pick_head returns None when not in cherry-pick."""
    result = git.get_cherry_pick_head(temp_repo)
    assert result is None


def test_get_cherry_pick_head_empty_file(temp_repo: Repo, tmp_path: Path) -> None:
    """Test get_cherry_pick_head returns None for empty file."""
    head_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    head_path.write_bytes(b"")
    result = git.get_cherry_pick_head(temp_repo)
    assert result is None


def test_get_conflict_files_status_unmerged(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files returns status.unmerged if available."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []
        unmerged = [b"conflict.txt"]

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict.txt"]


def test_get_conflict_files_status_conflicted(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files returns status.conflicted if available."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []
        conflicted = [b"conflict.txt"]

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict.txt"]


def test_get_conflict_files_status_conflicts(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files returns status.conflicts if available."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []
        conflicts = [b"conflict.txt"]

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict.txt"]


def test_get_conflict_files_staged_unmerged(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files returns staged.unmerged if available."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": [], "unmerged": [b"conflict.txt"]}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    files = git.get_conflict_files(temp_repo)
    assert files == ["conflict.txt"]


def test_get_conflict_files_index_conflicts_type_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles TypeError from conflicts iterator."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockIndex:
        def iterconflicts(self):
            raise TypeError("Mocked error")

        def items(self):
            return []

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert files == []


def test_get_conflict_files_index_conflicts_non_tuple(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test get_conflict_files handles non-tuple items in conflicts."""

    class MockStatus:
        staged = {"add": [], "modify": [], "delete": []}
        unstaged = []

    def mock_status(repo):
        return MockStatus()

    monkeypatch.setattr(porcelain, "status", mock_status)

    class MockIndex:
        def iterconflicts(self):
            # Return non-tuple items (just paths)
            return [b"conflict1.txt", b"conflict2.txt"]

    monkeypatch.setattr(temp_repo, "open_index", lambda: MockIndex())

    files = git.get_conflict_files(temp_repo)
    assert sorted(files) == ["conflict1.txt", "conflict2.txt"]


# ============================================================================
# Tests for rebase_continue and rebase_abort exception handling
# ============================================================================


def test_rebase_continue_generic_exception(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test rebase_continue wraps generic exceptions as RebaseFailure."""
    from shortcake._git import RebaseFailure

    # Create CHERRY_PICK_HEAD so it thinks a cherry-pick is in progress
    head_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    head_sha = temp_repo.refs[b"refs/heads/main"]
    head_path.write_bytes(head_sha)

    def mock_cherry_pick(repo, commit, continue_=False, abort=False):
        if continue_:
            raise RuntimeError("Something went wrong")

    monkeypatch.setattr(porcelain, "cherry_pick", mock_cherry_pick)

    with pytest.raises(RebaseFailure, match="Something went wrong"):
        git.rebase_continue(temp_repo)


def test_rebase_abort_generic_exception(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test rebase_abort wraps generic exceptions as RebaseFailure."""
    from shortcake._git import RebaseFailure

    # Create CHERRY_PICK_HEAD so it thinks a cherry-pick is in progress
    head_path = Path(temp_repo.controldir()) / "CHERRY_PICK_HEAD"
    head_sha = temp_repo.refs[b"refs/heads/main"]
    head_path.write_bytes(head_sha)

    def mock_cherry_pick(repo, commit, continue_=False, abort=False):
        if abort:
            raise RuntimeError("Abort failed badly")

    monkeypatch.setattr(porcelain, "cherry_pick", mock_cherry_pick)

    with pytest.raises(RebaseFailure, match="Abort failed badly"):
        git.rebase_abort(temp_repo)

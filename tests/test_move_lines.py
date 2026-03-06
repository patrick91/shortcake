import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.move_lines import (
    HunkSelection,
    LineSelection,
    MoveError,
    SplitChunk,
    _add_lines_to_file,
    _get_patch_files,
    _git_apply,
    _move_hunks,
    _move_lines,
    _remove_lines_from_file,
    _split_hunks,
    _split_lines_batch,
    _stage_patch_files,
)


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


def _git_diff_patch(repo_path: Path, parent: str, branch: str) -> str:
    """Get the diff patch between parent and branch."""
    result = subprocess.run(
        [
            "git",
            "diff",
            "--no-color",
            "--find-renames",
            "--full-index",
            f"{parent}...{branch}",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _get_file_patch(full_patch: str, file_name: str) -> str:
    """Extract the patch for a specific file from a multi-file patch."""
    sections = full_patch.split("diff --git ")
    for section in sections[1:]:
        if file_name in section.split("\n")[0]:
            return "diff --git " + section.rstrip()
    raise ValueError(f"File '{file_name}' not found in patch")


@pytest.fixture
def repo_for_move(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a stack: main → child_a → child_b.

    child_a adds file 'app.py' with multiple functions.
    child_b adds file 'utils.py'.
    """
    # Create child_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/child_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_a")

    # Write app.py with multiple functions
    app_py = tmp_path / "app.py"
    app_py.write_text(
        "def hello():\n    return 'hello'\n\ndef goodbye():\n    return 'goodbye'\n"
    )
    porcelain.add(temp_repo, paths=[str(app_py)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: add app functions")
    porcelain.commit(temp_repo, message=message_a.encode())
    child_a_sha = temp_repo.refs[b"refs/heads/child_a"]

    # Create child_b from child_a
    temp_repo.refs[b"refs/heads/child_b"] = child_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_b")

    utils_py = tmp_path / "utils.py"
    utils_py.write_text("def util():\n    return 'util'\n")
    porcelain.add(temp_repo, paths=[str(utils_py)])
    trailers_b = Trailers(parent_branch="child_a")
    message_b = trailers_b.apply_to("feat: add utils")
    porcelain.commit(temp_repo, message=message_b.encode())

    # Switch back to child_a
    switch_branch(temp_repo, "child_a")

    return temp_repo


@pytest.fixture
def repo_for_move_parent_to_child(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo for testing move from parent → child.

    main → parent_branch (adds shared.py) → child_branch (adds child.py)
    """
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/parent_branch"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/parent_branch")

    shared_py = tmp_path / "shared.py"
    shared_py.write_text(
        "def func_a():\n    return 'a'\n\ndef func_b():\n    return 'b'\n"
    )
    porcelain.add(temp_repo, paths=[str(shared_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add shared functions")
    porcelain.commit(temp_repo, message=message.encode())
    parent_sha = temp_repo.refs[b"refs/heads/parent_branch"]

    temp_repo.refs[b"refs/heads/child_branch"] = parent_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_branch")

    child_py = tmp_path / "child.py"
    child_py.write_text("def child_func():\n    return 'child'\n")
    porcelain.add(temp_repo, paths=[str(child_py)])
    trailers_c = Trailers(parent_branch="parent_branch")
    message_c = trailers_c.apply_to("feat: add child function")
    porcelain.commit(temp_repo, message=message_c.encode())

    switch_branch(temp_repo, "parent_branch")
    return temp_repo


def test_move_additions_child_to_parent(repo_for_move: Repo, tmp_path: Path) -> None:
    """Move additions from child_a to main (well, main is untracked, so
    let's restructure). Actually, let's just verify moving between two
    tracked branches works."""
    repo = repo_for_move
    repo_path = Path(repo.path)

    # Get the diff for child_a (child_a vs main)
    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    # The file has:
    # +def hello():         (new line 1)
    # +    return 'hello'   (new line 2)
    # +                     (new line 3)
    # +def goodbye():       (new line 4)
    # +    return 'goodbye' (new line 5)
    # We want to move lines 4-5 (goodbye function) to child_b

    # First we need to make sure we're on child_a
    git.switch_branch(repo, "child_a")

    result = _move_lines(
        repo,
        source_branch="child_a",
        target_branch="child_b",
        file_patch=file_patch,
        file_path="app.py",
        start_line=4,
        end_line=5,
        side="additions",
    )

    assert result.source_branch == "child_a"
    assert result.target_branch == "child_b"
    assert result.file_path == "app.py"

    # Verify source (child_a) no longer has the goodbye function
    git.switch_branch(repo, "child_a")
    app_content = (tmp_path / "app.py").read_text()
    assert "def hello()" in app_content
    assert "def goodbye()" not in app_content

    # Verify target (child_b) has the goodbye function
    git.switch_branch(repo, "child_b")
    app_content_b = (tmp_path / "app.py").read_text()
    assert "def goodbye()" in app_content_b


def test_move_additions_parent_to_child(
    repo_for_move_parent_to_child: Repo, tmp_path: Path
) -> None:
    """Move additions from parent → child."""
    repo = repo_for_move_parent_to_child
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "parent_branch")
    file_patch = _get_file_patch(full_patch, "shared.py")

    # shared.py lines (new file, all additions):
    # +def func_a():      (new line 1)
    # +    return 'a'      (new line 2)
    # +                    (new line 3)
    # +def func_b():      (new line 4)
    # +    return 'b'      (new line 5)
    # Move func_b (lines 4-5) to child_branch

    result = _move_lines(
        repo,
        source_branch="parent_branch",
        target_branch="child_branch",
        file_patch=file_patch,
        file_path="shared.py",
        start_line=4,
        end_line=5,
        side="additions",
    )

    assert result.source_branch == "parent_branch"
    assert result.target_branch == "child_branch"

    # Verify parent no longer has func_b
    git.switch_branch(repo, "parent_branch")
    content = (tmp_path / "shared.py").read_text()
    assert "def func_a()" in content
    assert "def func_b()" not in content


def test_error_dirty_working_tree(repo_for_move: Repo, tmp_path: Path) -> None:
    """Error when working tree has uncommitted changes."""
    repo = repo_for_move
    # Create a dirty file
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("dirty")
    porcelain.add(repo, paths=[str(dirty)])

    with pytest.raises(MoveError, match="uncommitted changes"):
        _move_lines(
            repo,
            source_branch="child_a",
            target_branch="child_b",
            file_patch="fake",
            file_path="app.py",
            start_line=1,
            end_line=5,
            side="additions",
        )


def test_error_source_equals_target(repo_for_move: Repo) -> None:
    """Error when source and target are the same branch."""
    with pytest.raises(MoveError, match="must be different"):
        _move_lines(
            repo_for_move,
            source_branch="child_a",
            target_branch="child_a",
            file_patch="fake",
            file_path="app.py",
            start_line=1,
            end_line=5,
            side="additions",
        )


def test_error_branch_not_tracked(temp_repo: Repo, tmp_path: Path) -> None:
    """Error when branch is not tracked by Shortcake."""
    # Create an untracked branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/untracked"] = main_sha

    with pytest.raises(MoveError, match="not tracked"):
        _move_lines(
            temp_repo,
            source_branch="untracked",
            target_branch="main",
            file_patch="fake",
            file_path="f.py",
            start_line=1,
            end_line=5,
            side="additions",
        )


def test_error_branch_not_exist(temp_repo: Repo) -> None:
    """Error when branch doesn't exist."""
    with pytest.raises(MoveError, match="does not exist"):
        _move_lines(
            temp_repo,
            source_branch="nonexistent",
            target_branch="main",
            file_patch="fake",
            file_path="f.py",
            start_line=1,
            end_line=5,
            side="additions",
        )


def test_rollback_on_restack_failure(repo_for_move: Repo, tmp_path: Path) -> None:
    """If restacking fails after source modification, all refs are rolled back."""
    repo = repo_for_move
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    # Tamper with child_b so the restack (after source modification) will conflict.
    # child_b modifies app.py in a way that conflicts with the removal.
    git.switch_branch(repo, "child_b")
    app_py = tmp_path / "app.py"
    app_py.write_text("CONFLICT CONTENT\nTHIS WILL PREVENT REBASE\n")
    porcelain.add(repo, paths=[str(app_py)])
    head = git.get_branch_head(repo, "child_b")
    msg = git.get_commit_message(repo, head)
    git.amend_commit(repo, msg)
    git.switch_branch(repo, "child_a")

    # Save SHAs after tampering (these are the refs _move_lines will save)
    source_sha_before = git.get_branch_head(repo, "child_a").decode()
    target_sha_before = git.get_branch_head(repo, "child_b").decode()

    with pytest.raises(MoveError, match="Restack failed"):
        _move_lines(
            repo,
            source_branch="child_a",
            target_branch="child_b",
            file_patch=file_patch,
            file_path="app.py",
            start_line=4,
            end_line=5,
            side="additions",
        )

    # Verify all refs were rolled back
    source_sha_after = git.get_branch_head(repo, "child_a").decode()
    target_sha_after = git.get_branch_head(repo, "child_b").decode()
    assert source_sha_after == source_sha_before
    assert target_sha_after == target_sha_before


# --- Helper function unit tests ---


def test_get_patch_files_extracts_paths() -> None:
    """_get_patch_files extracts file paths from a diff."""
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/bar/baz.py b/bar/baz.py\n"
        "--- a/bar/baz.py\n"
        "+++ b/bar/baz.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    assert _get_patch_files(patch) == ["foo.py", "bar/baz.py"]


def test_get_patch_files_empty_patch() -> None:
    """_get_patch_files returns empty list for empty patch."""
    assert _get_patch_files("") == []


def test_stage_patch_files_empty_patch(temp_repo: Repo) -> None:
    """_stage_patch_files is a no-op for empty patch."""
    repo_path = Path(temp_repo.path)
    _stage_patch_files(repo_path, "")


def test_git_apply_invalid_patch(temp_repo: Repo) -> None:
    """_git_apply raises MoveError on invalid patch."""
    repo_path = Path(temp_repo.path)
    with pytest.raises(MoveError, match="Failed to"):
        _git_apply(repo_path, "this is not a valid patch", reverse=False)


def test_remove_lines_file_not_found(tmp_path: Path) -> None:
    """_remove_lines_from_file raises MoveError if file doesn't exist."""
    with pytest.raises(MoveError, match="not found"):
        _remove_lines_from_file(tmp_path, "nonexistent.py", 1, 5)


def test_remove_lines_all_lines_deletes_file(tmp_path: Path) -> None:
    """_remove_lines_from_file deletes file when all lines are removed."""
    f = tmp_path / "to_delete.py"
    f.write_text("line 1\nline 2\n")
    removed = _remove_lines_from_file(tmp_path, "to_delete.py", 1, 2)
    assert len(removed) == 2
    assert not f.exists()


def test_add_lines_to_new_file(tmp_path: Path) -> None:
    """_add_lines_to_file creates new file when it doesn't exist."""
    _add_lines_to_file(tmp_path, "new_file.py", ["line 1\n", "line 2\n"])
    assert (tmp_path / "new_file.py").read_text() == "line 1\nline 2\n"


def test_add_lines_to_existing_without_trailing_newline(tmp_path: Path) -> None:
    """_add_lines_to_file adds newline before appending."""
    f = tmp_path / "existing.py"
    f.write_text("existing content")  # no trailing newline
    _add_lines_to_file(tmp_path, "existing.py", ["new line\n"])
    assert f.read_text() == "existing content\nnew line\n"


def test_error_target_branch_not_exist(repo_for_move: Repo) -> None:
    """Error when target branch doesn't exist."""
    with pytest.raises(MoveError, match="does not exist"):
        _move_lines(
            repo_for_move,
            source_branch="child_a",
            target_branch="nonexistent",
            file_patch="fake",
            file_path="app.py",
            start_line=1,
            end_line=5,
            side="additions",
        )


def test_error_target_branch_not_tracked(repo_for_move: Repo, tmp_path: Path) -> None:
    """Error when target branch is not tracked by Shortcake."""
    repo = repo_for_move
    # main is untracked (no Shortcake-Parent trailer)
    # child_a is tracked, so source check passes
    with pytest.raises(MoveError, match="not tracked"):
        _move_lines(
            repo,
            source_branch="child_a",
            target_branch="main",
            file_patch="fake",
            file_path="app.py",
            start_line=1,
            end_line=5,
            side="additions",
        )


def test_error_empty_patch(repo_for_move: Repo) -> None:
    """Error when extracted sub-patch has no changes."""
    # Use a valid patch format but select lines that don't exist
    patch = (
        "diff --git a/app.py b/app.py\n"
        "index 000..111 100644\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,3 +1,3 @@\n"
        " context\n"
        "-old\n"
        "+new\n"
    )
    with pytest.raises(MoveError):
        _move_lines(
            repo_for_move,
            source_branch="child_a",
            target_branch="child_b",
            file_patch=patch,
            file_path="app.py",
            start_line=100,
            end_line=200,
            side="additions",
        )


def test_error_rebase_in_progress(repo_for_move: Repo) -> None:
    """Error when rebase is in progress."""
    repo = repo_for_move
    rebase_dir = Path(repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir(exist_ok=True)

    try:
        with pytest.raises(MoveError, match="rebase in progress"):
            _move_lines(
                repo,
                source_branch="child_a",
                target_branch="child_b",
                file_patch="fake",
                file_path="app.py",
                start_line=1,
                end_line=5,
                side="additions",
            )
    finally:
        rebase_dir.rmdir()


def test_move_restacks_target_descendants(temp_repo: Repo, tmp_path: Path) -> None:
    """Phase 4 restacks target's descendants after target is amended."""
    repo = temp_repo
    repo_path = Path(repo.path)

    # Build: main → branch_a → branch_b → branch_c
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    switch_branch(repo, "branch_a")

    a_py = tmp_path / "a.py"
    a_py.write_text("def a():\n    return 'a1'\n\ndef a2():\n    return 'a2'\n")
    porcelain.add(repo, paths=[str(a_py)])
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(repo, message=msg_a.encode())
    a_sha = repo.refs[b"refs/heads/branch_a"]

    repo.refs[b"refs/heads/branch_b"] = a_sha
    switch_branch(repo, "branch_b")

    b_py = tmp_path / "b.py"
    b_py.write_text("def b():\n    return 'b'\n")
    porcelain.add(repo, paths=[str(b_py)])
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(repo, message=msg_b.encode())
    b_sha = repo.refs[b"refs/heads/branch_b"]

    repo.refs[b"refs/heads/branch_c"] = b_sha
    switch_branch(repo, "branch_c")

    c_py = tmp_path / "c.py"
    c_py.write_text("def c():\n    return 'c'\n")
    porcelain.add(repo, paths=[str(c_py)])
    trailers_c = Trailers(parent_branch="branch_b")
    msg_c = trailers_c.apply_to("feat: branch c")
    porcelain.commit(repo, message=msg_c.encode())

    switch_branch(repo, "branch_a")

    # Get patch for branch_a vs main
    full_patch = _git_diff_patch(repo_path, "main", "branch_a")
    file_patch = _get_file_patch(full_patch, "a.py")

    # Move a2 function (lines 4-5) from branch_a to branch_b
    # This triggers Phase 4: after branch_b is amended, branch_c is restacked
    result = _move_lines(
        repo,
        source_branch="branch_a",
        target_branch="branch_b",
        file_patch=file_patch,
        file_path="a.py",
        start_line=4,
        end_line=5,
        side="additions",
    )

    assert result.source_branch == "branch_a"
    assert result.target_branch == "branch_b"
    # branch_c should appear in restacked branches (Phase 4)
    assert "branch_c" in result.restacked_branches


def test_move_deletions(temp_repo: Repo, tmp_path: Path) -> None:
    """Move deletions from parent → child using side='deletions'."""
    repo = temp_repo
    repo_path = Path(repo.path)

    # Create a file on main that we'll delete lines from
    shared_py = tmp_path / "shared.py"
    shared_py.write_text(
        "def func_a():\n    return 'a'\n\ndef func_b():\n    return 'b'\n"
        "\ndef func_c():\n    return 'c'\n"
    )
    porcelain.add(repo, paths=[str(shared_py)])
    git.amend_commit(repo, "init with shared.py")

    # Create parent_branch from main, delete func_c
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/parent_branch"] = main_sha
    switch_branch(repo, "parent_branch")

    shared_py.write_text(
        "def func_a():\n    return 'a'\n\ndef func_b():\n    return 'b'\n"
    )
    porcelain.add(repo, paths=[str(shared_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: remove func_c")
    porcelain.commit(repo, message=message.encode())
    parent_sha = repo.refs[b"refs/heads/parent_branch"]

    # Create child_branch from parent_branch
    repo.refs[b"refs/heads/child_branch"] = parent_sha
    switch_branch(repo, "child_branch")

    child_py = tmp_path / "child.py"
    child_py.write_text("def child_func():\n    return 'child'\n")
    porcelain.add(repo, paths=[str(child_py)])
    trailers_c = Trailers(parent_branch="parent_branch")
    message_c = trailers_c.apply_to("feat: add child function")
    porcelain.commit(repo, message=message_c.encode())

    switch_branch(repo, "parent_branch")

    # Get the diff with deletions
    full_patch = _git_diff_patch(repo_path, "main", "parent_branch")
    file_patch = _get_file_patch(full_patch, "shared.py")

    # Verify patch has deletions
    assert "-def func_c():" in file_patch

    result = _move_lines(
        repo,
        source_branch="parent_branch",
        target_branch="child_branch",
        file_patch=file_patch,
        file_path="shared.py",
        start_line=5,  # old-file line where deletions start
        end_line=8,
        side="deletions",
    )

    assert result.source_branch == "parent_branch"
    assert result.target_branch == "child_branch"


# --- _move_hunks tests ---


def test_move_hunks_basic(repo_for_move: Repo, tmp_path: Path) -> None:
    """Move a hunk from child_a to child_b using _move_hunks."""
    repo = repo_for_move
    repo_path = Path(repo.path)

    # Get the diff for child_a (child_a vs main)
    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    git.switch_branch(repo, "child_a")

    hunks = [
        HunkSelection(
            file_path="app.py",
            file_patch=file_patch,
            hunk_index=0,  # The only hunk (entire file is new)
        )
    ]

    result = _move_hunks(repo, "child_a", "child_b", hunks)

    assert result.source_branch == "child_a"
    assert result.target_branch == "child_b"
    assert "app.py" in result.file_paths

    # Verify source (child_a) no longer has app.py content
    git.switch_branch(repo, "child_a")
    app_path = tmp_path / "app.py"
    # The file should either be deleted or have no content from the hunk
    if app_path.exists():
        content = app_path.read_text()
        assert "def hello()" not in content
        assert "def goodbye()" not in content

    # Verify target (child_b) has the content
    git.switch_branch(repo, "child_b")
    app_content_b = (tmp_path / "app.py").read_text()
    assert "def hello()" in app_content_b
    assert "def goodbye()" in app_content_b


def test_move_hunks_error_no_hunks(repo_for_move: Repo) -> None:
    """Error when no hunks are provided."""
    with pytest.raises(MoveError, match="No hunks selected"):
        _move_hunks(repo_for_move, "child_a", "child_b", [])


def test_move_hunks_error_same_branch(repo_for_move: Repo) -> None:
    """Error when source and target are the same."""
    hunks = [HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)]
    with pytest.raises(MoveError, match="must be different"):
        _move_hunks(repo_for_move, "child_a", "child_a", hunks)


def test_move_hunks_error_dirty_tree(repo_for_move: Repo, tmp_path: Path) -> None:
    """Error when working tree has uncommitted changes."""
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("dirty")
    porcelain.add(repo_for_move, paths=[str(dirty)])

    hunks = [HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)]
    with pytest.raises(MoveError, match="uncommitted changes"):
        _move_hunks(repo_for_move, "child_a", "child_b", hunks)


def test_move_hunks_error_branch_not_exist(repo_for_move: Repo) -> None:
    """Error when branch doesn't exist."""
    hunks = [HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)]
    with pytest.raises(MoveError, match="does not exist"):
        _move_hunks(repo_for_move, "nonexistent", "child_b", hunks)


def test_move_hunks_error_branch_not_tracked(repo_for_move: Repo) -> None:
    """Error when branch is not tracked by Shortcake."""
    hunks = [HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)]
    with pytest.raises(MoveError, match="not tracked"):
        _move_hunks(repo_for_move, "child_a", "main", hunks)


def test_move_hunks_error_rebase_in_progress(repo_for_move: Repo) -> None:
    """Error when rebase is in progress."""
    repo = repo_for_move
    rebase_dir = Path(repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir(exist_ok=True)

    try:
        hunks = [HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)]
        with pytest.raises(MoveError, match="rebase in progress"):
            _move_hunks(repo, "child_a", "child_b", hunks)
    finally:
        rebase_dir.rmdir()


def test_move_hunks_rollback_on_restack_failure(
    repo_for_move: Repo, tmp_path: Path
) -> None:
    """If restacking fails after source modification, all refs are rolled back."""
    repo = repo_for_move
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    # Tamper with child_b so the restack will conflict
    git.switch_branch(repo, "child_b")
    app_py = tmp_path / "app.py"
    app_py.write_text("CONFLICT CONTENT\nTHIS WILL PREVENT REBASE\n")
    porcelain.add(repo, paths=[str(app_py)])
    head = git.get_branch_head(repo, "child_b")
    msg = git.get_commit_message(repo, head)
    git.amend_commit(repo, msg)
    git.switch_branch(repo, "child_a")

    source_sha_before = git.get_branch_head(repo, "child_a").decode()
    target_sha_before = git.get_branch_head(repo, "child_b").decode()

    hunks = [
        HunkSelection(
            file_path="app.py",
            file_patch=file_patch,
            hunk_index=0,
        )
    ]

    with pytest.raises(MoveError, match="Restack failed"):
        _move_hunks(repo, "child_a", "child_b", hunks)

    # Verify all refs were rolled back
    source_sha_after = git.get_branch_head(repo, "child_a").decode()
    target_sha_after = git.get_branch_head(repo, "child_b").decode()
    assert source_sha_after == source_sha_before
    assert target_sha_after == target_sha_before


def test_move_hunks_restacks_target_children(
    repo_for_move: Repo, tmp_path: Path
) -> None:
    """Moving hunks to a branch restacks that branch's children (Phase 4)."""
    repo = repo_for_move
    repo_path = Path(repo.path)

    # Extend stack: main → child_a → child_b → child_c
    child_b_sha = repo.refs[b"refs/heads/child_b"]
    repo.refs[b"refs/heads/child_c"] = child_b_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_c")

    extra = tmp_path / "extra.py"
    extra.write_text("def extra():\n    return 'extra'\n")
    porcelain.add(repo, paths=[str(extra)])
    trailers = Trailers(parent_branch="child_b")
    message = trailers.apply_to("feat: add extra")
    porcelain.commit(repo, message=message.encode())

    git.switch_branch(repo, "child_a")

    # Move hunks from child_a to child_b; child_b has child_c, so Phase 4 restacks
    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    hunks = [HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)]
    result = _move_hunks(repo, "child_a", "child_b", hunks)

    assert result.source_branch == "child_a"
    assert result.target_branch == "child_b"
    # child_c should have been restacked in Phase 4
    assert "child_c" in result.restacked_branches


def test_move_hunks_target_not_exist(repo_for_move: Repo) -> None:
    """Error when target branch doesn't exist."""
    hunks = [HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)]
    with pytest.raises(MoveError, match="does not exist"):
        _move_hunks(repo_for_move, "child_a", "nonexistent", hunks)


def test_move_hunks_source_not_tracked(repo_for_move: Repo) -> None:
    """Error when source branch is not tracked."""
    hunks = [HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)]
    with pytest.raises(MoveError, match="not tracked"):
        _move_hunks(repo_for_move, "main", "child_a", hunks)


# === Split hunks tests ===


@pytest.fixture
def repo_for_split(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a stack: main → child_a → child_b.

    child_a adds file 'app.py' with two functions (hello + goodbye).
    child_b adds file 'utils.py'.
    """
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/child_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_a")

    app_py = tmp_path / "app.py"
    app_py.write_text(
        "def hello():\n    return 'hello'\n\ndef goodbye():\n    return 'goodbye'\n"
    )
    porcelain.add(temp_repo, paths=[str(app_py)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: add app functions")
    porcelain.commit(temp_repo, message=message_a.encode())
    child_a_sha = temp_repo.refs[b"refs/heads/child_a"]

    temp_repo.refs[b"refs/heads/child_b"] = child_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_b")

    utils_py = tmp_path / "utils.py"
    utils_py.write_text("def util():\n    return 'util'\n")
    porcelain.add(temp_repo, paths=[str(utils_py)])
    trailers_b = Trailers(parent_branch="child_a")
    message_b = trailers_b.apply_to("feat: add utils")
    porcelain.commit(temp_repo, message=message_b.encode())

    switch_branch(temp_repo, "child_a")
    return temp_repo


def test_split_hunks_before_basic(repo_for_split: Repo, tmp_path: Path) -> None:
    """Split a hunk 'before' creates new branch as parent of source."""
    repo = repo_for_split
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    hunks = [HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)]
    result = _split_hunks(
        repo,
        source_branch="child_a",
        commit_message="feat: extract hello",
        placement="before",
        hunks=hunks,
        no_verify=True,
    )

    assert result.source_branch == "child_a"
    assert result.new_branch == "feat-extract-hello"
    assert result.placement == "before"
    assert "app.py" in result.file_paths

    # Verify new branch exists
    assert git.branch_exists(repo, "feat-extract-hello")

    # Verify new branch has the hunk content
    switch_branch(repo, "feat-extract-hello")
    app_content = (tmp_path / "app.py").read_text()
    assert "def hello()" in app_content

    # Verify new branch's parent trailer points to main (source's original parent)
    all_branches = set(git.get_all_local_branches(repo))
    new_parent = git.get_branch_parent(repo, "feat-extract-hello", all_branches)
    assert new_parent == "main"

    # Verify source's parent trailer now points to new branch
    source_parent = git.get_branch_parent(repo, "child_a", all_branches)
    assert source_parent == "feat-extract-hello"


def test_split_hunks_after_basic(repo_for_split: Repo, tmp_path: Path) -> None:
    """Split a hunk 'after' creates new branch as child of source."""
    repo = repo_for_split
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    hunks = [HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)]
    result = _split_hunks(
        repo,
        source_branch="child_a",
        commit_message="feat: extract hello after",
        placement="after",
        hunks=hunks,
        no_verify=True,
    )

    assert result.source_branch == "child_a"
    assert result.new_branch == "feat-extract-hello-after"
    assert result.placement == "after"

    # Verify new branch exists
    assert git.branch_exists(repo, "feat-extract-hello-after")

    # Verify new branch's parent trailer points to source
    all_branches = set(git.get_all_local_branches(repo))
    new_parent = git.get_branch_parent(repo, "feat-extract-hello-after", all_branches)
    assert new_parent == "child_a"

    # Verify child_b's parent trailer now points to new branch
    child_b_parent = git.get_branch_parent(repo, "child_b", all_branches)
    assert child_b_parent == "feat-extract-hello-after"


def test_split_hunks_after_no_child(temp_repo: Repo, tmp_path: Path) -> None:
    """Split 'after' when source has no children works fine (no reparenting)."""
    repo = temp_repo
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/leaf"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/leaf")

    f = tmp_path / "leaf.py"
    f.write_text("def a():\n    return 'a'\n\ndef b():\n    return 'b'\n")
    porcelain.add(repo, paths=[str(f)])
    trailers = Trailers(parent_branch="main")
    msg = trailers.apply_to("feat: leaf functions")
    porcelain.commit(repo, message=msg.encode())

    switch_branch(repo, "leaf")
    repo_path = Path(repo.path)
    full_patch = _git_diff_patch(repo_path, "main", "leaf")
    file_patch = _get_file_patch(full_patch, "leaf.py")

    hunks = [HunkSelection(file_path="leaf.py", file_patch=file_patch, hunk_index=0)]
    result = _split_hunks(
        repo,
        source_branch="leaf",
        commit_message="feat: extract leaf part",
        placement="after",
        hunks=hunks,
        no_verify=True,
    )

    assert result.new_branch == "feat-extract-leaf-part"
    assert git.branch_exists(repo, "feat-extract-leaf-part")

    all_branches = set(git.get_all_local_branches(repo))
    new_parent = git.get_branch_parent(repo, "feat-extract-leaf-part", all_branches)
    assert new_parent == "leaf"


def test_split_hunks_after_multiple_children_error(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Split 'after' fails when source has multiple children."""
    repo = temp_repo
    main_sha = repo.refs[b"refs/heads/main"]

    # Create parent branch
    repo.refs[b"refs/heads/parent_br"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/parent_br")
    f = tmp_path / "p.py"
    f.write_text("def p():\n    return 'p'\n")
    porcelain.add(repo, paths=[str(f)])
    trailers = Trailers(parent_branch="main")
    porcelain.commit(repo, message=trailers.apply_to("feat: parent").encode())
    parent_sha = repo.refs[b"refs/heads/parent_br"]

    # Create two children
    for name in ["child_x", "child_y"]:
        repo.refs[f"refs/heads/{name}".encode()] = parent_sha
        repo.refs.set_symbolic_ref(b"HEAD", f"refs/heads/{name}".encode())
        cf = tmp_path / f"{name}.py"
        cf.write_text(f"def {name}():\n    return '{name}'\n")
        porcelain.add(repo, paths=[str(cf)])
        t = Trailers(parent_branch="parent_br")
        porcelain.commit(repo, message=t.apply_to(f"feat: {name}").encode())

    switch_branch(repo, "parent_br")
    repo_path = Path(repo.path)
    full_patch = _git_diff_patch(repo_path, "main", "parent_br")
    file_patch = _get_file_patch(full_patch, "p.py")

    hunks = [HunkSelection(file_path="p.py", file_patch=file_patch, hunk_index=0)]
    with pytest.raises(MoveError, match="multiple children"):
        _split_hunks(
            repo,
            source_branch="parent_br",
            commit_message="feat: split attempt",
            placement="after",
            hunks=hunks,
            no_verify=True,
        )


def test_split_hunks_error_dirty_tree(repo_for_split: Repo, tmp_path: Path) -> None:
    """Error when working tree has uncommitted changes."""
    repo = repo_for_split
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("dirty")
    porcelain.add(repo, paths=[str(dirty)])

    hunks = [HunkSelection(file_path="app.py", file_patch="fake", hunk_index=0)]
    with pytest.raises(MoveError, match="uncommitted changes"):
        _split_hunks(
            repo,
            source_branch="child_a",
            commit_message="feat: split",
            placement="before",
            hunks=hunks,
        )


def test_split_hunks_error_no_hunks(repo_for_split: Repo) -> None:
    """Error when no hunks are selected."""
    with pytest.raises(MoveError, match="No hunks selected"):
        _split_hunks(
            repo_for_split,
            source_branch="child_a",
            commit_message="feat: split",
            placement="before",
            hunks=[],
        )


def test_split_hunks_error_branch_exists(repo_for_split: Repo, tmp_path: Path) -> None:
    """Error when generated branch name already exists."""
    repo = repo_for_split
    # Create a branch that conflicts with the generated name
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/feat-split-attempt"] = main_sha

    repo_path = Path(repo.path)
    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")

    hunks = [HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)]
    with pytest.raises(MoveError, match="already exists"):
        _split_hunks(
            repo,
            source_branch="child_a",
            commit_message="feat: split attempt",
            placement="before",
            hunks=hunks,
            no_verify=True,
        )


def test_split_hunks_multiple_hunks(temp_repo: Repo, tmp_path: Path) -> None:
    """Split multiple hunks across files into a new branch."""
    repo = temp_repo
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/multi"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/multi")

    # Create two files
    f1 = tmp_path / "alpha.py"
    f1.write_text("def alpha():\n    return 'alpha'\n")
    f2 = tmp_path / "beta.py"
    f2.write_text("def beta():\n    return 'beta'\n")
    porcelain.add(repo, paths=[str(f1), str(f2)])
    trailers = Trailers(parent_branch="main")
    msg = trailers.apply_to("feat: add alpha and beta")
    porcelain.commit(repo, message=msg.encode())

    switch_branch(repo, "multi")
    repo_path = Path(repo.path)
    full_patch = _git_diff_patch(repo_path, "main", "multi")
    alpha_patch = _get_file_patch(full_patch, "alpha.py")
    beta_patch = _get_file_patch(full_patch, "beta.py")

    hunks = [
        HunkSelection(file_path="alpha.py", file_patch=alpha_patch, hunk_index=0),
        HunkSelection(file_path="beta.py", file_patch=beta_patch, hunk_index=0),
    ]
    result = _split_hunks(
        repo,
        source_branch="multi",
        commit_message="feat: extract both files",
        placement="before",
        hunks=hunks,
        no_verify=True,
    )

    assert result.new_branch == "feat-extract-both-files"
    assert set(result.file_paths) == {"alpha.py", "beta.py"}

    # New branch should have both files
    switch_branch(repo, "feat-extract-both-files")
    assert (tmp_path / "alpha.py").exists()
    assert (tmp_path / "beta.py").exists()

    # Source still has files (inherited from new parent branch)
    # but its own diff against its parent should be empty
    switch_branch(repo, "multi")
    source_patch = _git_diff_patch(repo_path, "feat-extract-both-files", "multi")
    assert source_patch.strip() == ""


def test_split_hunks_rebase_in_progress_error(
    repo_for_split: Repo,
) -> None:
    """Error when rebase is in progress."""
    repo = repo_for_split
    rebase_dir = Path(repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir(exist_ok=True)
    try:
        with pytest.raises(MoveError, match="rebase in progress"):
            _split_hunks(
                repo,
                source_branch="child_a",
                commit_message="feat: extract",
                placement="before",
                hunks=[
                    HunkSelection(
                        file_path="app.py", file_patch="x", hunk_index=0
                    )
                ],
            )
    finally:
        rebase_dir.rmdir()


def test_split_hunks_nonexistent_branch_error(temp_repo: Repo) -> None:
    """Error when source branch does not exist."""
    with pytest.raises(MoveError, match="does not exist"):
        _split_hunks(
            temp_repo,
            source_branch="nonexistent-branch",
            commit_message="feat: extract",
            placement="before",
            hunks=[
                HunkSelection(file_path="app.py", file_patch="x", hunk_index=0)
            ],
        )


def test_split_hunks_before_restack_failure_triggers_rollback(
    repo_for_split: Repo, tmp_path: Path
) -> None:
    """Phase 2a restack failure triggers rollback for 'before' placement."""
    repo = repo_for_split
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")
    child_a_sha_before = git.get_branch_head(repo, "child_a").decode()

    hunks = [HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)]

    failing_result = MagicMock()
    failing_result.success = False
    failing_result.error_output = "simulated conflict"

    with (
        patch(
            "shortcake.commands.move_lines._rebase_branch",
            return_value=failing_result,
        ),
        pytest.raises(MoveError, match="Restack failed"),
    ):
        _split_hunks(
            repo,
            source_branch="child_a",
            commit_message="feat: extract hello",
            placement="before",
            hunks=hunks,
            no_verify=True,
        )

    # Rollback must restore child_a's ref
    child_a_sha_after = git.get_branch_head(repo, "child_a").decode()
    assert child_a_sha_after == child_a_sha_before
    # New branch must NOT exist (failure occurred before Phase 3a)
    assert not git.branch_exists(repo, "feat-extract-hello")


def test_split_hunks_after_restack_failure_triggers_rollback(
    repo_for_split: Repo, tmp_path: Path
) -> None:
    """Phase 2b restack failure triggers rollback for 'after' placement."""
    repo = repo_for_split
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")
    child_a_sha_before = git.get_branch_head(repo, "child_a").decode()

    hunks = [HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)]

    failing_result = MagicMock()
    failing_result.success = False
    failing_result.error_output = "simulated conflict"

    with (
        patch(
            "shortcake.commands.move_lines._rebase_branch",
            return_value=failing_result,
        ),
        pytest.raises(MoveError, match="Restack failed"),
    ):
        _split_hunks(
            repo,
            source_branch="child_a",
            commit_message="feat: extract hello after",
            placement="after",
            hunks=hunks,
            no_verify=True,
        )

    # Rollback must restore child_a's ref
    child_a_sha_after = git.get_branch_head(repo, "child_a").decode()
    assert child_a_sha_after == child_a_sha_before


def test_split_hunks_after_phase5b_failure_triggers_rollback(
    repo_for_split: Repo, tmp_path: Path
) -> None:
    """Phase 5b restack failure triggers rollback for 'after' placement."""
    repo = repo_for_split
    repo_path = Path(repo.path)

    # child_a has child_b: Phase 2b restacks child_b (1st call), then
    # Phase 5b restacks child_b onto new branch (2nd call → failure).
    full_patch = _git_diff_patch(repo_path, "main", "child_a")
    file_patch = _get_file_patch(full_patch, "app.py")
    child_a_sha_before = git.get_branch_head(repo, "child_a").decode()

    hunks = [HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)]

    success_result = MagicMock()
    success_result.success = True
    failing_result = MagicMock()
    failing_result.success = False
    failing_result.error_output = "simulated conflict"

    with (
        patch(
            "shortcake.commands.move_lines._rebase_branch",
            side_effect=[success_result, failing_result],
        ),
        pytest.raises(MoveError, match="Restack failed"),
    ):
        _split_hunks(
            repo,
            source_branch="child_a",
            commit_message="feat: extract hello phase5b",
            placement="after",
            hunks=hunks,
            no_verify=True,
        )

    # Rollback must restore child_a's ref
    child_a_sha_after = git.get_branch_head(repo, "child_a").decode()
    assert child_a_sha_after == child_a_sha_before
    # New branch must be deleted (created in Phase 3b before failure)
    assert not git.branch_exists(repo, "feat-extract-hello-phase5b")


def test_split_hunks_before_phase6a_failure_deletes_new_branch(
    repo_for_split: Repo, tmp_path: Path
) -> None:
    """Phase 6a failure deletes the created new branch during rollback."""
    repo = repo_for_split
    repo_path = Path(repo.path)

    # Use child_b (no children) so Phase 2a has empty plan → _rebase_branch
    # is only called in Phase 6a, after the new branch has been created.
    switch_branch(repo, "child_b")
    full_patch = _git_diff_patch(repo_path, "child_a", "child_b")
    file_patch = _get_file_patch(full_patch, "utils.py")
    child_b_sha_before = git.get_branch_head(repo, "child_b").decode()

    hunks = [HunkSelection(file_path="utils.py", file_patch=file_patch, hunk_index=0)]

    failing_result = MagicMock()
    failing_result.success = False
    failing_result.error_output = "simulated conflict"

    with (
        patch(
            "shortcake.commands.move_lines._rebase_branch",
            return_value=failing_result,
        ),
        pytest.raises(MoveError, match="Restack failed"),
    ):
        _split_hunks(
            repo,
            source_branch="child_b",
            commit_message="feat: extract util",
            placement="before",
            hunks=hunks,
            no_verify=True,
        )

    # Rollback must have deleted the created new branch
    assert not git.branch_exists(repo, "feat-extract-util")
    # Rollback must restore child_b's ref
    child_b_sha_after = git.get_branch_head(repo, "child_b").decode()
    assert child_b_sha_after == child_b_sha_before


# === Split lines batch tests ===


@pytest.fixture
def repo_for_split_lines(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a stack: main → work.

    work adds 'app.py' with 3 functions (6 added lines, one big hunk).
    """
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/work"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/work")

    app_py = tmp_path / "app.py"
    app_py.write_text(
        "def foo():\n"
        "    return 'foo'\n"
        "def bar():\n"
        "    return 'bar'\n"
        "def baz():\n"
        "    return 'baz'\n"
    )
    porcelain.add(temp_repo, paths=[str(app_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add three functions")
    porcelain.commit(temp_repo, message=message.encode())

    switch_branch(temp_repo, "work")
    return temp_repo


def test_split_lines_single_chunk(
    repo_for_split_lines: Repo, tmp_path: Path
) -> None:
    """Single-chunk split: lines 1-2 into new branch."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    chunks = [
        SplitChunk(
            commit_message="feat: extract foo",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]

    result = _split_lines_batch(repo, "work", chunks, no_verify=True)

    assert result.source_branch == "work"
    assert result.new_branches == ["feat-extract-foo"]

    # Verify new branch has foo function
    switch_branch(repo, "feat-extract-foo")
    content = (tmp_path / "app.py").read_text()
    assert "def foo()" in content

    # Verify chain: main → feat-extract-foo → work
    all_branches = set(git.get_all_local_branches(repo))
    assert git.get_branch_parent(repo, "feat-extract-foo", all_branches) == "main"
    assert git.get_branch_parent(repo, "work", all_branches) == "feat-extract-foo"


def test_split_lines_multi_chunk(
    repo_for_split_lines: Repo, tmp_path: Path
) -> None:
    """Multi-chunk split: 2 chunks → 2 new branches, verify chain."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    chunks = [
        SplitChunk(
            commit_message="feat: extract foo",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        ),
        SplitChunk(
            commit_message="feat: extract bar",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=3,
                    end_line=4,
                    side="additions",
                )
            ],
        ),
    ]

    result = _split_lines_batch(repo, "work", chunks, no_verify=True)

    assert result.source_branch == "work"
    assert result.new_branches == ["feat-extract-foo", "feat-extract-bar"]

    # Verify chain: main → feat-extract-foo → feat-extract-bar → work
    all_branches = set(git.get_all_local_branches(repo))
    assert git.get_branch_parent(repo, "feat-extract-foo", all_branches) == "main"
    assert (
        git.get_branch_parent(repo, "feat-extract-bar", all_branches)
        == "feat-extract-foo"
    )
    assert (
        git.get_branch_parent(repo, "work", all_branches) == "feat-extract-bar"
    )

    # Verify each branch has the right content
    switch_branch(repo, "feat-extract-foo")
    assert "def foo()" in (tmp_path / "app.py").read_text()

    switch_branch(repo, "feat-extract-bar")
    content = (tmp_path / "app.py").read_text()
    assert "def foo()" in content  # inherited from parent
    assert "def bar()" in content


def test_split_lines_overlap_error(repo_for_split_lines: Repo) -> None:
    """Overlapping line ranges across chunks raises MoveError."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    chunks = [
        SplitChunk(
            commit_message="feat: chunk one",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=3,
                    side="additions",
                )
            ],
        ),
        SplitChunk(
            commit_message="feat: chunk two",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=2,
                    end_line=4,
                    side="additions",
                )
            ],
        ),
    ]

    with pytest.raises(MoveError, match="Overlapping"):
        _split_lines_batch(repo, "work", chunks, no_verify=True)


def test_split_lines_empty_chunks_error(repo_for_split_lines: Repo) -> None:
    """Empty chunks list raises MoveError."""
    with pytest.raises(MoveError, match="No chunks provided"):
        _split_lines_batch(repo_for_split_lines, "work", [], no_verify=True)


def test_split_lines_empty_selections_error(repo_for_split_lines: Repo) -> None:
    """Chunk with no selections raises MoveError."""
    chunks = [SplitChunk(commit_message="feat: empty", selections=[])]
    with pytest.raises(MoveError, match="no selections"):
        _split_lines_batch(repo_for_split_lines, "work", chunks, no_verify=True)


def test_split_lines_dirty_tree_error(
    repo_for_split_lines: Repo, tmp_path: Path
) -> None:
    """Error when working tree has uncommitted changes."""
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("dirty")
    porcelain.add(repo_for_split_lines, paths=[str(dirty)])

    chunks = [
        SplitChunk(
            commit_message="feat: chunk",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch="fake",
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]
    with pytest.raises(MoveError, match="uncommitted changes"):
        _split_lines_batch(repo_for_split_lines, "work", chunks, no_verify=True)


def test_split_lines_branch_not_tracked_error(temp_repo: Repo, tmp_path: Path) -> None:
    """Error when branch is not tracked by Shortcake."""
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/untracked"] = main_sha

    chunks = [
        SplitChunk(
            commit_message="feat: chunk",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch="fake",
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]
    with pytest.raises(MoveError, match="not tracked"):
        _split_lines_batch(temp_repo, "untracked", chunks, no_verify=True)


def test_split_lines_rollback_on_failure(
    repo_for_split_lines: Repo, tmp_path: Path
) -> None:
    """Refs are restored and created branches deleted if chunk creation fails."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    source_sha_before = git.get_branch_head(repo, "work").decode()

    # Create a branch that will collide with the second chunk's name
    # (validation catches this before phase 1, so state should be unchanged)
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/feat-extract-bar"] = main_sha

    chunks = [
        SplitChunk(
            commit_message="feat: extract foo",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        ),
        SplitChunk(
            commit_message="feat: extract bar",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=3,
                    end_line=4,
                    side="additions",
                )
            ],
        ),
    ]

    with pytest.raises(MoveError, match="already exists"):
        _split_lines_batch(repo, "work", chunks, no_verify=True)

    # Verify source ref unchanged
    source_sha_after = git.get_branch_head(repo, "work").decode()
    assert source_sha_after == source_sha_before

    # Verify no chunk branches were created
    assert not git.branch_exists(repo, "feat-extract-foo")


def test_split_lines_restacks_source_descendants(
    repo_for_split_lines: Repo, tmp_path: Path
) -> None:
    """Source descendants are properly restacked."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    # Add a non-conflicting child to work
    work_sha = repo.refs[b"refs/heads/work"]
    repo.refs[b"refs/heads/work-child"] = work_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/work-child")

    extra = tmp_path / "extra.py"
    extra.write_text("def extra():\n    return 'extra'\n")
    porcelain.add(repo, paths=[str(extra)])
    trailers = Trailers(parent_branch="work")
    msg = trailers.apply_to("feat: extra file")
    porcelain.commit(repo, message=msg.encode())

    switch_branch(repo, "work")

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    chunks = [
        SplitChunk(
            commit_message="feat: extract foo",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]

    result = _split_lines_batch(repo, "work", chunks, no_verify=True)

    # work-child should be restacked
    assert "work-child" in result.restacked_branches


def test_split_lines_duplicate_branch_name_error(
    repo_for_split_lines: Repo,
) -> None:
    """Same commit message → same slug → duplicate branch name error."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    chunks = [
        SplitChunk(
            commit_message="feat: same name",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        ),
        SplitChunk(
            commit_message="feat: same name",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=3,
                    end_line=4,
                    side="additions",
                )
            ],
        ),
    ]

    with pytest.raises(MoveError, match="Duplicate branch name"):
        _split_lines_batch(repo, "work", chunks, no_verify=True)


def test_split_lines_invalid_patch_error(repo_for_split_lines: Repo) -> None:
    """EmptyPatchError from extract_sub_patch is converted to MoveError."""
    repo = repo_for_split_lines
    chunks = [
        SplitChunk(
            commit_message="feat: chunk",
            selections=[
                LineSelection(
                    file_path="app.py",
                    # Patch with no hunks triggers EmptyPatchError
                    file_patch="--- a/app.py\n+++ b/app.py\n",
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]
    with pytest.raises(MoveError, match="No hunks found"):
        _split_lines_batch(repo, "work", chunks, no_verify=True)


def test_split_lines_rebase_in_progress_error(repo_for_split_lines: Repo) -> None:
    """Error when rebase is in progress."""
    repo = repo_for_split_lines
    rebase_dir = Path(repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir(exist_ok=True)

    chunks = [
        SplitChunk(
            commit_message="feat: chunk",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch="fake",
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]
    try:
        with pytest.raises(MoveError, match="rebase in progress"):
            _split_lines_batch(repo, "work", chunks, no_verify=True)
    finally:
        rebase_dir.rmdir()


def test_split_lines_nonexistent_branch_error(temp_repo: Repo) -> None:
    """Error when source branch does not exist."""
    chunks = [
        SplitChunk(
            commit_message="feat: chunk",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch="fake",
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]
    with pytest.raises(MoveError, match="does not exist"):
        _split_lines_batch(temp_repo, "nonexistent-branch", chunks, no_verify=True)


def test_split_lines_all_lines_selected_empty_commit(
    repo_for_split_lines: Repo, tmp_path: Path
) -> None:
    """When all added lines are selected, source gets an empty commit."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    # Select all 6 lines — nothing left for source branch
    chunks = [
        SplitChunk(
            commit_message="feat: extract all",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=6,
                    side="additions",
                )
            ],
        )
    ]

    result = _split_lines_batch(repo, "work", chunks, no_verify=True)

    assert result.new_branches == ["feat-extract-all"]
    assert git.branch_exists(repo, "work")

    # New branch should have all content
    switch_branch(repo, "feat-extract-all")
    content = (tmp_path / "app.py").read_text()
    assert "def foo()" in content
    assert "def baz()" in content


def test_split_lines_restack_failure_triggers_rollback(
    repo_for_split_lines: Repo, tmp_path: Path
) -> None:
    """If Phase 3 restack fails, rollback deletes created branches."""
    repo = repo_for_split_lines
    repo_path = Path(repo.path)

    # Add a child of work so Phase 3 has a branch to restack
    work_sha = repo.refs[b"refs/heads/work"]
    repo.refs[b"refs/heads/work-child"] = work_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/work-child")
    extra = tmp_path / "extra.py"
    extra.write_text("x = 1\n")
    porcelain.add(repo, paths=[str(extra)])
    trailers_child = Trailers(parent_branch="work")
    porcelain.commit(
        repo, message=trailers_child.apply_to("feat: extra").encode()
    )
    switch_branch(repo, "work")

    full_patch = _git_diff_patch(repo_path, "main", "work")
    file_patch = _get_file_patch(full_patch, "app.py")

    work_sha_before = git.get_branch_head(repo, "work").decode()

    chunks = [
        SplitChunk(
            commit_message="feat: extract foo",
            selections=[
                LineSelection(
                    file_path="app.py",
                    file_patch=file_patch,
                    start_line=1,
                    end_line=2,
                    side="additions",
                )
            ],
        )
    ]

    failing_result = MagicMock()
    failing_result.success = False
    failing_result.error_output = "simulated conflict"

    with (
        patch(
            "shortcake.commands.move_lines._rebase_branch",
            return_value=failing_result,
        ),
        pytest.raises(MoveError, match="Restack failed"),
    ):
        _split_lines_batch(repo, "work", chunks, no_verify=True)

    # Rollback must delete the created chunk branch
    assert not git.branch_exists(repo, "feat-extract-foo")
    # Rollback must restore work's ref
    work_sha_after = git.get_branch_head(repo, "work").decode()
    assert work_sha_after == work_sha_before

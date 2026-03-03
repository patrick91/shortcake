import subprocess
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.move_lines import (
    HunkSelection,
    MoveError,
    _add_lines_to_file,
    _get_patch_files,
    _git_apply,
    _move_hunks,
    _move_lines,
    _remove_lines_from_file,
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

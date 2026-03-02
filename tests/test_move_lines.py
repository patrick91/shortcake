import subprocess
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.move_lines import MoveError, _move_lines


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


def _git_diff_patch(repo_path: Path, parent: str, branch: str) -> str:
    """Get the diff patch between parent and branch."""
    result = subprocess.run(
        ["git", "diff", "--no-color", "--find-renames", "--full-index",
         f"{parent}...{branch}"],
        cwd=repo_path,
        capture_output=True, text=True, check=True,
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
    app_py.write_text("def hello():\n    return 'hello'\n\ndef goodbye():\n    return 'goodbye'\n")
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


def test_rollback_on_restack_failure(
    repo_for_move: Repo, tmp_path: Path
) -> None:
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

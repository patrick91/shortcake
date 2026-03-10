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
    _accept_working_hunks,
)
from tests._git_helpers import switch_branch


def _git_diff_working(repo_path: Path) -> str:
    """Get working tree diff vs HEAD."""
    result = subprocess.run(
        ["git", "diff", "--no-color", "--find-renames", "--full-index", "HEAD"],
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
def repo_with_tracked_and_working(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with a tracked branch and working changes with multiple hunks.

    main → tracked_branch (adds app.py with many lines)
    Current branch is tracked_branch with uncommitted changes to app.py
    that modify lines far apart, producing two separate hunks.
    """
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/tracked_branch"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/tracked_branch")

    # Need enough context lines (>6) between modified regions for separate hunks
    app_py = tmp_path / "app.py"
    app_py.write_text(
        "def hello():\n"
        "    return 'hello'\n"
        "\n"
        "\n"
        "def spacer1():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer2():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer3():\n"
        "    pass\n"
        "\n"
        "\n"
        "def world():\n"
        "    return 'world'\n"
    )
    porcelain.add(temp_repo, paths=[str(app_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add app")
    porcelain.commit(temp_repo, message=message.encode())

    # Modify lines far apart to create two separate hunks
    app_py.write_text(
        "def hello():\n"
        "    return 'hello modified'\n"
        "\n"
        "\n"
        "def spacer1():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer2():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer3():\n"
        "    pass\n"
        "\n"
        "\n"
        "def world():\n"
        "    return 'world modified'\n"
    )

    return temp_repo


def test_accept_single_hunk(
    repo_with_tracked_and_working: Repo, tmp_path: Path
) -> None:
    """Accept a single hunk from working changes into a tracked branch."""
    repo = repo_with_tracked_and_working
    repo_path = Path(repo.path)

    full_patch = _git_diff_working(repo_path)
    file_patch = _get_file_patch(full_patch, "app.py")

    result = _accept_working_hunks(
        repo,
        target_branch="tracked_branch",
        hunks=[
            HunkSelection(
                file_path="app.py",
                file_patch=file_patch,
                hunk_index=0,
            )
        ],
    )

    assert result.target_branch == "tracked_branch"
    assert result.file_paths == ["app.py"]

    # Verify target branch has the first hunk's change
    git.switch_branch(repo, "tracked_branch")
    content = (tmp_path / "app.py").read_text()
    assert "hello modified" in content


def test_accept_multiple_hunks_same_file(
    repo_with_tracked_and_working: Repo, tmp_path: Path
) -> None:
    """Accept multiple hunks from the same file."""
    repo = repo_with_tracked_and_working
    repo_path = Path(repo.path)

    full_patch = _git_diff_working(repo_path)
    file_patch = _get_file_patch(full_patch, "app.py")

    result = _accept_working_hunks(
        repo,
        target_branch="tracked_branch",
        hunks=[
            HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0),
            HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=1),
        ],
    )

    assert result.target_branch == "tracked_branch"
    assert result.file_paths == ["app.py"]

    # Verify target branch has both hunks' changes
    git.switch_branch(repo, "tracked_branch")
    content = (tmp_path / "app.py").read_text()
    assert "hello modified" in content
    assert "world modified" in content


def test_accept_hunks_across_multiple_files(temp_repo: Repo, tmp_path: Path) -> None:
    """Accept hunks from different files."""
    repo = temp_repo
    repo_path = Path(repo.path)

    # Create tracked branch with two files
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/tracked_branch"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/tracked_branch")

    app_py = tmp_path / "app.py"
    app_py.write_text("def hello():\n    return 'hello'\n")
    utils_py = tmp_path / "utils.py"
    utils_py.write_text("def util():\n    return 'util'\n")
    porcelain.add(repo, paths=[str(app_py), str(utils_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add files")
    porcelain.commit(repo, message=message.encode())

    # Add working changes to both files
    app_py.write_text("def hello():\n    return 'hello modified'\n")
    utils_py.write_text("def util():\n    return 'util modified'\n")

    full_patch = _git_diff_working(repo_path)
    app_patch = _get_file_patch(full_patch, "app.py")
    utils_patch = _get_file_patch(full_patch, "utils.py")

    result = _accept_working_hunks(
        repo,
        target_branch="tracked_branch",
        hunks=[
            HunkSelection(file_path="app.py", file_patch=app_patch, hunk_index=0),
            HunkSelection(file_path="utils.py", file_patch=utils_patch, hunk_index=0),
        ],
    )

    assert result.target_branch == "tracked_branch"
    assert sorted(result.file_paths) == ["app.py", "utils.py"]

    # Verify target branch has changes from both files
    git.switch_branch(repo, "tracked_branch")
    assert "hello modified" in (tmp_path / "app.py").read_text()
    assert "util modified" in (tmp_path / "utils.py").read_text()


def test_remaining_working_changes_preserved(temp_repo: Repo, tmp_path: Path) -> None:
    """Remaining working changes are preserved via stash/pop after accept."""
    repo = temp_repo
    repo_path = Path(repo.path)

    # Create tracked branch
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/tracked_branch"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/tracked_branch")

    app_py = tmp_path / "app.py"
    app_py.write_text(
        "def hello():\n"
        "    return 'hello'\n"
        "\n"
        "\n"
        "def spacer1():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer2():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer3():\n"
        "    pass\n"
        "\n"
        "\n"
        "def world():\n"
        "    return 'world'\n"
    )
    porcelain.add(repo, paths=[str(app_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add app")
    porcelain.commit(repo, message=message.encode())

    # Add working changes with two hunks + an untracked file
    app_py.write_text(
        "def hello():\n"
        "    return 'hello modified'\n"
        "\n"
        "\n"
        "def spacer1():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer2():\n"
        "    pass\n"
        "\n"
        "\n"
        "def spacer3():\n"
        "    pass\n"
        "\n"
        "\n"
        "def world():\n"
        "    return 'world modified'\n"
    )
    other_file = tmp_path / "other.py"
    other_file.write_text("# other changes\n")

    full_patch = _git_diff_working(repo_path)
    file_patch = _get_file_patch(full_patch, "app.py")

    # Accept only the first hunk
    _accept_working_hunks(
        repo,
        target_branch="tracked_branch",
        hunks=[HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)],
    )

    # Verify the other file is still present in working tree
    assert other_file.exists()
    assert other_file.read_text() == "# other changes\n"

    # Verify the second hunk's change is still in the working tree
    remaining_diff = _git_diff_working(repo_path)
    assert "world modified" in remaining_diff


def test_error_rebase_in_progress(
    repo_with_tracked_and_working: Repo, tmp_path: Path
) -> None:
    """Error when rebase is in progress."""
    repo = repo_with_tracked_and_working

    # Simulate rebase in progress
    rebase_dir = Path(repo.controldir()) / "rebase-merge"
    rebase_dir.mkdir(exist_ok=True)

    try:
        with pytest.raises(MoveError, match="rebase in progress"):
            _accept_working_hunks(
                repo,
                target_branch="tracked_branch",
                hunks=[
                    HunkSelection(file_path="app.py", file_patch="fake", hunk_index=0)
                ],
            )
    finally:
        rebase_dir.rmdir()


def test_error_target_branch_not_tracked(temp_repo: Repo, tmp_path: Path) -> None:
    """Error when target branch is not tracked by Shortcake."""
    # Create an untracked branch (no Shortcake-Parent trailer)
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/untracked"] = main_sha

    # Create working changes
    work_file = tmp_path / "work.py"
    work_file.write_text("changes\n")

    with pytest.raises(MoveError, match="not tracked"):
        _accept_working_hunks(
            temp_repo,
            target_branch="untracked",
            hunks=[HunkSelection(file_path="work.py", file_patch="fake", hunk_index=0)],
        )


def test_error_target_branch_not_exist(temp_repo: Repo) -> None:
    """Error when target branch doesn't exist."""
    with pytest.raises(MoveError, match="does not exist"):
        _accept_working_hunks(
            temp_repo,
            target_branch="nonexistent",
            hunks=[HunkSelection(file_path="f.py", file_patch="fake", hunk_index=0)],
        )


def test_error_invalid_hunk_index(
    repo_with_tracked_and_working: Repo, tmp_path: Path
) -> None:
    """Error when hunk index is out of range."""
    repo = repo_with_tracked_and_working
    repo_path = Path(repo.path)

    full_patch = _git_diff_working(repo_path)
    file_patch = _get_file_patch(full_patch, "app.py")

    with pytest.raises(MoveError, match="Invalid hunk index"):
        _accept_working_hunks(
            repo,
            target_branch="tracked_branch",
            hunks=[
                HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=99)
            ],
        )


def test_error_empty_hunks_list(temp_repo: Repo) -> None:
    """Error when hunks list is empty."""
    with pytest.raises(MoveError, match="No hunks selected"):
        _accept_working_hunks(
            temp_repo,
            target_branch="main",
            hunks=[],
        )


def test_accept_restacks_downstream_branches(temp_repo: Repo, tmp_path: Path) -> None:
    """When accepting hunks, downstream branches are restacked successfully."""
    repo = temp_repo
    repo_path = Path(repo.path)

    # Build: main → parent_branch → child_branch
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/parent_branch"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/parent_branch")

    app_py = tmp_path / "app.py"
    app_py.write_text("def hello():\n    return 'hello'\n")
    porcelain.add(repo, paths=[str(app_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add app")
    porcelain.commit(repo, message=message.encode())
    parent_sha = repo.refs[b"refs/heads/parent_branch"]

    # child_branch adds a separate file (non-conflicting)
    repo.refs[b"refs/heads/child_branch"] = parent_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_branch")

    child_py = tmp_path / "child.py"
    child_py.write_text("def child():\n    return 'child'\n")
    porcelain.add(repo, paths=[str(child_py)])
    trailers_c = Trailers(parent_branch="parent_branch")
    message_c = trailers_c.apply_to("feat: add child")
    porcelain.commit(repo, message=message_c.encode())

    # Switch to parent_branch and add working changes
    switch_branch(repo, "parent_branch")
    app_py.write_text("def hello():\n    return 'hello modified'\n")

    full_patch = _git_diff_working(repo_path)
    file_patch = _get_file_patch(full_patch, "app.py")

    result = _accept_working_hunks(
        repo,
        target_branch="parent_branch",
        hunks=[HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)],
    )

    assert result.target_branch == "parent_branch"
    # child_branch should have been restacked
    assert "child_branch" in result.restacked_branches


def test_rollback_on_restack_failure(temp_repo: Repo, tmp_path: Path) -> None:
    """If restacking fails after target modification, all refs are rolled back."""
    repo = temp_repo
    repo_path = Path(repo.path)

    # Build stack: main → parent_branch → child_branch
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/parent_branch"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/parent_branch")

    app_py = tmp_path / "app.py"
    app_py.write_text("def hello():\n    return 'hello'\n")
    porcelain.add(repo, paths=[str(app_py)])
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add app")
    porcelain.commit(repo, message=message.encode())
    parent_sha = repo.refs[b"refs/heads/parent_branch"]

    repo.refs[b"refs/heads/child_branch"] = parent_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/child_branch")

    # child modifies app.py in a conflicting way
    app_py.write_text("CONFLICT CONTENT\nTHIS WILL PREVENT REBASE\n")
    porcelain.add(repo, paths=[str(app_py)])
    trailers_c = Trailers(parent_branch="parent_branch")
    message_c = trailers_c.apply_to("feat: conflict")
    porcelain.commit(repo, message=message_c.encode())

    # Switch to parent_branch and add working changes
    switch_branch(repo, "parent_branch")
    app_py.write_text("def hello():\n    return 'hello modified'\n")

    # Save original SHAs
    parent_sha_before = git.get_branch_head(repo, "parent_branch").decode()
    child_sha_before = git.get_branch_head(repo, "child_branch").decode()

    full_patch = _git_diff_working(repo_path)
    file_patch = _get_file_patch(full_patch, "app.py")

    with pytest.raises(MoveError, match="Restack failed"):
        _accept_working_hunks(
            repo,
            target_branch="parent_branch",
            hunks=[
                HunkSelection(file_path="app.py", file_patch=file_patch, hunk_index=0)
            ],
        )

    # Verify refs were rolled back
    parent_sha_after = git.get_branch_head(repo, "parent_branch").decode()
    child_sha_after = git.get_branch_head(repo, "child_branch").decode()
    assert parent_sha_after == parent_sha_before
    assert child_sha_after == child_sha_before

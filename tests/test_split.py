import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.cli import app
from shortcake.commands.split import (
    SplitError,
    _count_hunks,
    _file_sections,
    _split,
)
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    get_ref,
    set_ref,
    switch_branch,
)

runner = CliRunner()


def _make_two_file_branch(repo: Repo, tmp_path: Path) -> None:
    """Create tracked branch 'feature' off main adding a.py and b.py."""
    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/feature", main_sha)
    repo.set_head("refs/heads/feature")

    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 2\n")
    add_paths(repo, tmp_path / "a.py")
    add_paths(repo, tmp_path / "b.py")
    commit(repo, Trailers(parent_branch="main").apply_to("Add a and b"))


def test_file_sections_and_hunk_count() -> None:
    """Test splitting a multi-file patch into per-file sections."""
    patch = (
        "diff --git a/a.py b/a.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/a.py\n"
        "@@ -0,0 +1 @@\n"
        "+a = 1\n"
        "diff --git a/b.py b/b.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/b.py\n"
        "@@ -0,0 +1 @@\n"
        "+b = 2\n"
    )

    sections = _file_sections(patch)

    assert set(sections) == {"a.py", "b.py"}
    assert sections["a.py"].startswith("diff --git a/a.py b/a.py\n")
    assert _count_hunks(sections["a.py"]) == 1


def test_split_before_moves_file_below(temp_repo: Repo, tmp_path: Path) -> None:
    """Test splitting a file into a new parent branch."""
    _make_two_file_branch(temp_repo, tmp_path)

    result = _split(temp_repo, ["b.py"], "Extract b", placement="before")

    assert result.new_branch == "extract-b"
    assert result.source_branch == "feature"

    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "extract-b", all_branches) == "main"
    assert git.get_branch_parent(temp_repo, "feature", all_branches) == "extract-b"

    # The new branch carries only b.py; feature still has both files
    switch_branch(temp_repo, "extract-b")
    assert (tmp_path / "b.py").read_text() == "b = 2\n"
    assert not (tmp_path / "a.py").exists()

    switch_branch(temp_repo, "feature")
    assert (tmp_path / "a.py").read_text() == "a = 1\n"
    assert (tmp_path / "b.py").read_text() == "b = 2\n"


def test_split_after_moves_file_above(temp_repo: Repo, tmp_path: Path) -> None:
    """Test splitting a file into a new child branch."""
    _make_two_file_branch(temp_repo, tmp_path)

    result = _split(temp_repo, ["b.py"], "Extract b", placement="after")

    assert result.new_branch == "extract-b"

    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "feature", all_branches) == "main"
    assert git.get_branch_parent(temp_repo, "extract-b", all_branches) == "feature"

    switch_branch(temp_repo, "feature")
    assert not (tmp_path / "b.py").exists()
    switch_branch(temp_repo, "extract-b")
    assert (tmp_path / "b.py").read_text() == "b = 2\n"


def test_split_restacks_children(temp_repo: Repo, tmp_path: Path) -> None:
    """Test descendants are restacked over the split."""
    _make_two_file_branch(temp_repo, tmp_path)

    feature_sha = get_ref(temp_repo, "refs/heads/feature")
    set_ref(temp_repo, "refs/heads/child", feature_sha)
    temp_repo.set_head("refs/heads/child")
    (tmp_path / "c.py").write_text("c = 3\n")
    add_paths(temp_repo, tmp_path / "c.py")
    commit(temp_repo, Trailers(parent_branch="feature").apply_to("Add c"))

    switch_branch(temp_repo, "feature")
    result = _split(temp_repo, ["b.py"], "Extract b", placement="before")

    assert "child" in result.restacked_branches
    # child must contain the whole stack's content
    switch_branch(temp_repo, "child")
    for name in ("a.py", "b.py", "c.py"):
        assert (tmp_path / name).exists()


def test_split_detached_head(temp_repo: Repo) -> None:
    """Test SplitError in detached HEAD state."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    with pytest.raises(SplitError, match="detached HEAD"):
        _split(temp_repo, ["a.py"], "Extract")


def test_split_untracked_branch_suggests_adopt(temp_repo: Repo, tmp_path: Path) -> None:
    """Test the not-tracked error teaches sc adopt."""
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/loose", main_sha)
    temp_repo.set_head("refs/heads/loose")
    (tmp_path / "x.py").write_text("x\n")
    add_paths(temp_repo, tmp_path / "x.py")
    commit(temp_repo, b"untracked commit")

    with pytest.raises(SplitError, match="sc adopt loose -p <parent>"):
        _split(temp_repo, ["x.py"], "Extract")


def test_split_unknown_file_lists_changed_files(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test asking for an unchanged file reports what actually changed."""
    _make_two_file_branch(temp_repo, tmp_path)

    with pytest.raises(SplitError, match=r"Changed files: a\.py, b\.py"):
        _split(temp_repo, ["missing.py"], "Extract")


def test_split_all_files_rejected(temp_repo: Repo, tmp_path: Path) -> None:
    """Test splitting every changed file out is refused."""
    _make_two_file_branch(temp_repo, tmp_path)

    with pytest.raises(SplitError, match="would become empty"):
        _split(temp_repo, ["a.py", "b.py"], "Extract everything")


def test_split_wraps_move_errors(temp_repo: Repo, tmp_path: Path) -> None:
    """Test underlying MoveError failures surface as SplitError."""
    _make_two_file_branch(temp_repo, tmp_path)

    # Uncommitted changes make _split_hunks refuse to run
    (tmp_path / "a.py").write_text("dirty\n")

    with pytest.raises(SplitError, match="uncommitted changes"):
        _split(temp_repo, ["b.py"], "Extract b")


def test_cli_split(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test the split CLI happy path."""
    monkeypatch.chdir(tmp_path)
    _make_two_file_branch(temp_repo, tmp_path)

    result = runner.invoke(app, ["split", "b.py", "-m", "Extract b"])

    assert result.exit_code == 0
    assert "Split 1 file(s) from 'feature' into 'extract-b' (before it)" in (
        result.output
    )
    assert "Restacked 'feature'" in result.output


def test_cli_split_json(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test split --json emits the result envelope."""
    monkeypatch.chdir(tmp_path)
    _make_two_file_branch(temp_repo, tmp_path)

    result = runner.invoke(app, ["split", "b.py", "-m", "Extract b", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["new_branch"] == "extract-b"
    assert document["data"]["source"] == "feature"
    assert document["data"]["placement"] == "before"


def test_cli_split_error_json(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test split --json failures use the error envelope."""
    monkeypatch.chdir(tmp_path)
    _make_two_file_branch(temp_repo, tmp_path)

    result = runner.invoke(app, ["split", "nope.py", "-m", "Extract", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "split_failed"
    assert "Changed files" in document["error"]["message"]


def test_cli_split_after(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test split --after places the new branch on top."""
    monkeypatch.chdir(tmp_path)
    _make_two_file_branch(temp_repo, tmp_path)

    result = runner.invoke(app, ["split", "b.py", "-m", "Extract b", "--after"])

    assert result.exit_code == 0
    assert "(after it)" in result.output


def test_split_patch_build_failure(temp_repo: Repo, tmp_path: Path) -> None:
    """Test diff failures surface as SplitError."""
    from unittest.mock import patch as mock_patch

    from shortcake._recap import RecapError

    _make_two_file_branch(temp_repo, tmp_path)

    with (
        mock_patch(
            "shortcake.commands.split.build_branch_patch",
            side_effect=RecapError("diff failed"),
        ),
        pytest.raises(SplitError, match="diff failed"),
    ):
        _split(temp_repo, ["b.py"], "Extract b")

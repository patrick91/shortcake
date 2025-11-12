import subprocess
from pathlib import Path

from typer.testing import CliRunner

from shortcake.cli import app

from .conftest import GitEditorScript

runner = CliRunner()


def stage_all(repo_path: Path) -> None:
    """Helper to stage all changes in the repository."""
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)


def test_adopt_help():
    result = runner.invoke(app, ["adopt", "--help"])

    assert result.exit_code == 0
    assert "Adopt an existing branch" in result.stdout
    assert "managed by shortcake" in result.stdout


def test_adopt_current_branch_no_argument(isolated_git_repo: Path, isolated_config: Path):
    """Test adopting the current branch without providing a branch name argument."""
    # Create a manual branch
    subprocess.run(
        ["git", "checkout", "-b", "my-feature"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Add a commit to it
    test_file = isolated_git_repo / "feature.txt"
    test_file.write_text("feature content")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add feature"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt current branch (no argument)
    result = runner.invoke(app, ["adopt"])

    assert result.exit_code == 0
    assert "Successfully adopted" in result.stdout
    assert "my-feature" in result.stdout
    assert "Use 'shortcake ls' to see all managed branches" in result.stdout

    # Verify git note was added
    commit_sha_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commit_sha = commit_sha_result.stdout.strip()

    note_result = subprocess.run(
        ["git", "notes", "--ref=shortcake", "show", commit_sha],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )

    assert note_result.returncode == 0
    assert "shortcake-managed" in note_result.stdout


def test_adopt_specific_branch_with_argument(isolated_git_repo: Path, isolated_config: Path):
    """Test adopting a specific branch by providing its name."""
    # Create a manual branch
    subprocess.run(
        ["git", "checkout", "-b", "feature-branch"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Add a commit to it
    test_file = isolated_git_repo / "feature.txt"
    test_file.write_text("feature content")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add feature"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Go back to main
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt the feature branch by name
    result = runner.invoke(app, ["adopt", "feature-branch"])

    assert result.exit_code == 0
    assert "Successfully adopted" in result.stdout
    assert "feature-branch" in result.stdout

    # Verify git note was added
    commit_sha_result = subprocess.run(
        ["git", "rev-parse", "feature-branch"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commit_sha = commit_sha_result.stdout.strip()

    note_result = subprocess.run(
        ["git", "notes", "--ref=shortcake", "show", commit_sha],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )

    assert note_result.returncode == 0
    assert "shortcake-managed" in note_result.stdout


def test_adopt_error_main_branch(isolated_git_repo: Path, isolated_config: Path):
    """Test that adopting main branch fails."""
    result = runner.invoke(app, ["adopt", "main"])

    assert result.exit_code == 1
    assert "Error: Cannot adopt the main branch" in result.output


def test_adopt_error_master_branch(isolated_git_repo: Path, isolated_config: Path):
    """Test that adopting master branch fails."""
    # Create a master branch
    subprocess.run(
        ["git", "branch", "master"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    result = runner.invoke(app, ["adopt", "master"])

    assert result.exit_code == 1
    assert "Error: Cannot adopt the master branch" in result.output


def test_adopt_error_branch_does_not_exist(isolated_git_repo: Path, isolated_config: Path):
    """Test that adopting a non-existent branch fails."""
    result = runner.invoke(app, ["adopt", "nonexistent-branch"])

    assert result.exit_code == 1
    assert "Error: Branch 'nonexistent-branch' does not exist" in result.output


def test_adopt_error_already_tracked(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test that adopting an already tracked branch reports it's already tracked."""
    # Create a branch with shortcake
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    git_editor_script("Add feature")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Try to adopt it again
    result = runner.invoke(app, ["adopt", "add-feature"])

    assert result.exit_code == 0
    assert "was already tracked" in result.output or "were already tracked" in result.output


def test_adopt_error_already_adopted_with_current_branch(
    isolated_git_repo: Path, isolated_config: Path
):
    """Test that adopting the current branch when it's already tracked reports it's already tracked."""
    # Create a manual branch
    subprocess.run(
        ["git", "checkout", "-b", "my-branch"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Add a commit to it
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("content")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add test"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt it once (should succeed)
    result1 = runner.invoke(app, ["adopt"])
    assert result1.exit_code == 0

    # Try to adopt again (should succeed but report already tracked)
    result2 = runner.invoke(app, ["adopt"])
    assert result2.exit_code == 0
    assert "was already tracked" in result2.output or "were already tracked" in result2.output


def test_adopt_branch_appears_in_ls(isolated_git_repo: Path, isolated_config: Path):
    """Test that an adopted branch appears in ls output."""
    # Create a manual branch
    subprocess.run(
        ["git", "checkout", "-b", "manual-feature"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Add a commit to it
    test_file = isolated_git_repo / "feature.txt"
    test_file.write_text("feature content")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add manual feature"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Verify it doesn't appear in ls before adoption
    ls_before = runner.invoke(app, ["ls"])
    assert "No shortcake-managed branches found" in ls_before.stdout

    # Adopt the branch
    runner.invoke(app, ["adopt"])

    # Verify it appears in ls after adoption
    ls_after = runner.invoke(app, ["ls"])
    assert ls_after.exit_code == 0
    assert "manual-feature" in ls_after.stdout
    assert "Add manual feature" in ls_after.stdout


def test_adopt_multiple_branches(isolated_git_repo: Path, isolated_config: Path):
    """Test adopting multiple branches."""
    # Create first branch
    subprocess.run(
        ["git", "checkout", "-b", "feature-a"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file1 = isolated_git_repo / "a.txt"
    test_file1.write_text("feature a")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add feature A"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Create second branch
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature-b"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file2 = isolated_git_repo / "b.txt"
    test_file2.write_text("feature b")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add feature B"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt both branches
    result1 = runner.invoke(app, ["adopt", "feature-a"])
    result2 = runner.invoke(app, ["adopt", "feature-b"])

    assert result1.exit_code == 0
    assert result2.exit_code == 0

    # Verify both appear in ls
    ls_result = runner.invoke(app, ["ls"])
    assert "feature-a" in ls_result.stdout
    assert "feature-b" in ls_result.stdout
    assert "Add feature A" in ls_result.stdout
    assert "Add feature B" in ls_result.stdout


def test_adopt_branch_with_stacked_structure(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test adopting a branch that has a parent relationship with a shortcake branch."""
    # Create a shortcake branch
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("test content 1")

    git_editor_script("Add first feature")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Create a manual branch on top
    subprocess.run(
        ["git", "checkout", "-b", "manual-child"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test content 2")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add child feature"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt the child branch
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0

    # Verify tree structure shows parent-child relationship
    ls_result = runner.invoke(app, ["ls"])
    assert "add-first-feature" in ls_result.stdout
    assert "manual-child" in ls_result.stdout

    # Check that manual-child appears as child of add-first-feature in tree
    lines = ls_result.stdout.strip().split("\n")
    child_line = [line for line in lines if "manual-child" in line][0]

    # Child should have tree characters
    assert "└──" in child_line or "├──" in child_line


def test_adopt_recursive_ancestors(isolated_git_repo: Path, isolated_config: Path):
    """Test that adopting a branch also adopts its ancestor branches."""
    # Create a stack of manual branches: feature-a -> feature-b -> feature-c
    subprocess.run(
        ["git", "checkout", "-b", "feature-a"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file1 = isolated_git_repo / "a.txt"
    test_file1.write_text("a")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add A"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", "feature-b"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file2 = isolated_git_repo / "b.txt"
    test_file2.write_text("b")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add B"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", "feature-c"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file3 = isolated_git_repo / "c.txt"
    test_file3.write_text("c")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add C"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt feature-c (should also adopt feature-a and feature-b)
    result = runner.invoke(app, ["adopt", "feature-c"])

    assert result.exit_code == 0
    assert "Successfully adopted 3 branches in the stack" in result.stdout
    assert "feature-a" in result.stdout
    assert "feature-b" in result.stdout
    assert "feature-c" in result.stdout

    # Verify all three branches have notes
    for branch in ["feature-a", "feature-b", "feature-c"]:
        commit_sha_result = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=isolated_git_repo,
            capture_output=True,
            text=True,
        )
        commit_sha = commit_sha_result.stdout.strip()

        note_result = subprocess.run(
            ["git", "notes", "--ref=shortcake", "show", commit_sha],
            cwd=isolated_git_repo,
            capture_output=True,
            text=True,
        )

        assert note_result.returncode == 0
        assert "shortcake-managed" in note_result.stdout


def test_adopt_recursive_descendants(isolated_git_repo: Path, isolated_config: Path):
    """Test that adopting a branch also adopts its descendant branches."""
    # Create a stack: feature-a -> feature-b -> feature-c
    subprocess.run(
        ["git", "checkout", "-b", "feature-a"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file1 = isolated_git_repo / "a.txt"
    test_file1.write_text("a")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add A"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", "feature-b"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file2 = isolated_git_repo / "b.txt"
    test_file2.write_text("b")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add B"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", "feature-c"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file3 = isolated_git_repo / "c.txt"
    test_file3.write_text("c")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add C"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt feature-a (should also adopt feature-b and feature-c)
    result = runner.invoke(app, ["adopt", "feature-a"])

    assert result.exit_code == 0
    assert "Successfully adopted 3 branches in the stack" in result.stdout
    assert "feature-a" in result.stdout
    assert "feature-b" in result.stdout
    assert "feature-c" in result.stdout


def test_adopt_recursive_middle_of_stack(isolated_git_repo: Path, isolated_config: Path):
    """Test that adopting a branch in the middle of a stack adopts the entire stack."""
    # Create a stack: feature-a -> feature-b -> feature-c
    subprocess.run(
        ["git", "checkout", "-b", "feature-a"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file1 = isolated_git_repo / "a.txt"
    test_file1.write_text("a")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add A"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", "feature-b"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file2 = isolated_git_repo / "b.txt"
    test_file2.write_text("b")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add B"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", "feature-c"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file3 = isolated_git_repo / "c.txt"
    test_file3.write_text("c")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add C"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt feature-b (should also adopt feature-a and feature-c)
    result = runner.invoke(app, ["adopt", "feature-b"])

    assert result.exit_code == 0
    assert "Successfully adopted 3 branches in the stack" in result.stdout
    assert "feature-a" in result.stdout
    assert "feature-b" in result.stdout
    assert "feature-c" in result.stdout


def test_adopt_recursive_partial_already_tracked(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test adopting a stack where some branches are already tracked."""
    # Create feature-a with shortcake
    test_file1 = isolated_git_repo / "a.txt"
    test_file1.write_text("a")

    git_editor_script("Add A")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Create feature-b and feature-c manually
    subprocess.run(
        ["git", "checkout", "-b", "feature-b"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file2 = isolated_git_repo / "b.txt"
    test_file2.write_text("b")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add B"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", "feature-c"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    test_file3 = isolated_git_repo / "c.txt"
    test_file3.write_text("c")
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add C"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Adopt feature-c (add-a is already tracked, feature-b and feature-c are new)
    result = runner.invoke(app, ["adopt", "feature-c"])

    assert result.exit_code == 0
    assert "Successfully adopted 2 branches in the stack" in result.stdout
    assert "feature-b" in result.stdout
    assert "feature-c" in result.stdout
    assert "was already tracked" in result.stdout or "were already tracked" in result.stdout

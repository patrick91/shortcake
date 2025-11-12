import subprocess
from pathlib import Path

from typer.testing import CliRunner

from shortcake.cli import app

from .conftest import GitEditorScript

runner = CliRunner()


def stage_all(repo_path: Path) -> None:
    """Helper to stage all changes in the repository."""
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)


def test_ls_help():
    result = runner.invoke(app, ["ls", "--help"])

    assert result.exit_code == 0
    assert "List all shortcake-managed branches" in result.stdout


def test_ls_with_no_branches(isolated_git_repo: Path, isolated_config: Path):
    """Test ls with no shortcake-managed branches."""
    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0
    assert "No shortcake-managed branches found" in result.stdout
    assert "Create a new stack with: shortcake create" in result.stdout


def test_ls_with_single_branch(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test ls with a single shortcake branch."""
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    commit_message = "Add first feature"
    git_editor_script(commit_message)

    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0
    assert "add-first-feature" in result.stdout
    assert commit_message in result.stdout
    assert "* " in result.stdout  # Current branch marker


def test_ls_with_stacked_branches(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test ls with stacked branches showing tree structure."""
    # Create first stack
    test_file = isolated_git_repo / "test1.txt"
    test_file.write_text("test content 1")

    commit_message1 = "Add first feature"
    git_editor_script(commit_message1)

    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Create second stack on top of first
    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test content 2")

    commit_message2 = "Add second feature"
    git_editor_script(commit_message2)

    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Create third stack on top of second
    test_file3 = isolated_git_repo / "test3.txt"
    test_file3.write_text("test content 3")

    commit_message3 = "Add third feature"
    git_editor_script(commit_message3)

    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0

    # Check all branches are listed
    assert "add-first-feature" in result.stdout
    assert "add-second-feature" in result.stdout
    assert "add-third-feature" in result.stdout

    # Check all commit messages are shown
    assert commit_message1 in result.stdout
    assert commit_message2 in result.stdout
    assert commit_message3 in result.stdout

    # Check current branch is marked
    assert "* add-third-feature" in result.stdout


def test_ls_shows_tree_structure(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test that ls shows proper tree structure with indentation."""
    # Create first stack
    test_file = isolated_git_repo / "test1.txt"
    test_file.write_text("test content 1")

    git_editor_script("Add first feature")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Create second stack on top of first
    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("test content 2")

    git_editor_script("Add second feature")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0

    # The output should show tree structure
    lines = result.stdout.strip().split("\n")

    # First branch should be at root level (no indentation with tree chars)
    first_branch_line = [line for line in lines if "add-first-feature" in line][0]
    assert not first_branch_line.startswith("├──") and not first_branch_line.startswith("└──")

    # Second branch should be indented as child
    second_branch_line = [line for line in lines if "add-second-feature" in line][0]
    assert "└──" in second_branch_line or "├──" in second_branch_line


def test_create_adds_git_note(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test that create command adds a git note to track the branch."""
    test_file = isolated_git_repo / "test.txt"
    test_file.write_text("test content")

    git_editor_script("Add new feature")

    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Get the HEAD commit SHA
    commit_sha_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )
    commit_sha = commit_sha_result.stdout.strip()

    # Check if the git note exists
    note_result = subprocess.run(
        ["git", "notes", "--ref=shortcake", "show", commit_sha],
        cwd=isolated_git_repo,
        capture_output=True,
        text=True,
    )

    assert note_result.returncode == 0
    assert "shortcake-managed" in note_result.stdout


def test_ls_only_shows_shortcake_branches(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test that ls only shows branches created by shortcake, not manual branches."""
    # Create a manual branch (not using shortcake)
    test_file = isolated_git_repo / "manual.txt"
    test_file.write_text("manual content")

    subprocess.run(
        ["git", "checkout", "-b", "manual-branch"],
        cwd=isolated_git_repo,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=isolated_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Manual commit"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Create a shortcake branch
    test_file2 = isolated_git_repo / "shortcake.txt"
    test_file2.write_text("shortcake content")

    git_editor_script("Add shortcake feature")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0

    # Should show shortcake branch
    assert "add-shortcake-feature" in result.stdout

    # Should NOT show manual branch
    assert "manual-branch" not in result.stdout


def test_ls_with_multiple_root_branches(
    isolated_git_repo: Path, isolated_config: Path, git_editor_script: GitEditorScript
):
    """Test ls with multiple independent root branches (not stacked)."""
    # Create first branch from main
    test_file1 = isolated_git_repo / "test1.txt"
    test_file1.write_text("content 1")

    git_editor_script("Add feature A")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    # Go back to main
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=isolated_git_repo,
        capture_output=True,
    )

    # Create second branch from main (not stacked on first)
    test_file2 = isolated_git_repo / "test2.txt"
    test_file2.write_text("content 2")

    git_editor_script("Add feature B")
    stage_all(isolated_git_repo)
    runner.invoke(app, ["create"])

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0

    # Both branches should be shown
    assert "add-feature-a" in result.stdout
    assert "add-feature-b" in result.stdout

    # Both should be at root level (no parent-child relationship)
    lines = result.stdout.strip().split("\n")
    feature_a_line = [line for line in lines if "add-feature-a" in line][0]
    feature_b_line = [line for line in lines if "add-feature-b" in line][0]

    # Neither should be indented
    assert not feature_a_line.startswith("├──") and not feature_a_line.startswith("└──")
    assert not feature_b_line.startswith("├──") and not feature_b_line.startswith("└──")

import stat
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    get_ref,
    init_repo,
    reset_hard,
    set_ref,
    set_remote,
    switch_branch,
)

runner = CliRunner()


def _dated(slug: str) -> str:
    return f"{date.today().isoformat()}-{slug}"


def test_cli_adopt_success(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI adopt command success."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["adopt"])

    assert result.exit_code == 0
    assert "Adopted 'feature' with parent 'main'" in result.output


def test_cli_adopt_with_branch(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI adopt with explicit branch argument."""
    monkeypatch.chdir(tmp_path)
    # Switch to main first
    repo_with_feature.set_head("refs/heads/main")

    result = runner.invoke(app, ["adopt", "feature"])

    assert result.exit_code == 0
    assert "Adopted 'feature'" in result.output


def test_cli_adopt_with_parent_option(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI adopt with --parent option."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["adopt", "--parent", "main"])

    assert result.exit_code == 0


def test_cli_adopt_error(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI adopt command error handling."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["adopt", "main"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Cannot adopt default branch" in result.output


def test_cli_help(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI shows help."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Shortcake" in result.output
    assert "adopt" in result.output
    assert "ls" in result.output
    assert "ui" in result.output


def test_cli_ls_no_tracked(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI ls with no tracked branches."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0
    assert "No tracked branches found." in result.output


def test_cli_ls_with_tracked(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI ls with tracked branches."""
    monkeypatch.chdir(tmp_path)

    # First adopt the branch
    runner.invoke(app, ["adopt"])

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0
    assert "feature" in result.output
    assert "main" in result.output
    assert "◉" in result.output  # Current branch marker


def test_cli_create_success(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create command success with --allow-empty."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["create", "-m", "feat: add new feature", "--allow-empty"]
    )

    assert result.exit_code == 0
    assert (
        f"Created branch '{_dated('feat-add-new-feature')}' from 'main'"
        in result.output
    )


def test_cli_create_no_staged_changes_error(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create fails without staged changes."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["create", "-m", "feat: something"])

    assert result.exit_code == 1
    assert "No staged changes" in result.output
    assert "--allow-empty" in result.output


def test_cli_create_suffixes_when_branch_exists(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI suffixes the generated name when the branch exists."""
    monkeypatch.chdir(tmp_path)

    # Create a branch first
    set_ref(
        temp_repo,
        f"refs/heads/{_dated('feat-existing')}",
        get_ref(temp_repo, "refs/heads/main"),
    )

    result = runner.invoke(
        app,
        ["create", "-m", "feat: existing", "--allow-empty"],
    )

    assert result.exit_code == 0
    assert f"Created branch '{_dated('feat-existing')}-2'" in result.output


def test_cli_create_suffixes_when_branch_has_merged_pr(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI suffixes the generated name when GitHub has a merged PR."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    set_remote(temp_repo, "origin", "git@github.com:owner/repo.git")

    base_name = _dated("feat-merged-before")
    mock_client = MagicMock()
    mock_client.has_merged_pr.side_effect = lambda branch: branch == base_name
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    with patch(
        "shortcake.commands.create.GitHubClient",
        return_value=mock_client,
        create=True,
    ):
        result = runner.invoke(
            app, ["create", "-m", "feat: merged before", "--allow-empty"]
        )

    assert result.exit_code == 0
    assert f"Created branch '{base_name}-2'" in result.output


def test_cli_create_error_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["create", "-m", "feat: something"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "detached HEAD" in result.output


def test_cli_help_includes_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI help includes create command."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--help"])

    assert "create" in result.output


def test_cli_create_no_verify(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create with --no-verify skips hooks."""
    monkeypatch.chdir(tmp_path)

    # Create a failing hook
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    add_paths(temp_repo, new_file)

    # With --no-verify, should succeed despite failing hook
    result = runner.invoke(app, ["create", "-m", "feat: test", "-n"])

    assert result.exit_code == 0
    assert "Created branch" in result.output


def test_cli_create_hook_failure(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create fails when pre-commit hook fails."""
    monkeypatch.chdir(tmp_path)

    # Create a failing hook
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'Hook failed!'\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    add_paths(temp_repo, new_file)

    result = runner.invoke(app, ["create", "-m", "feat: test"])

    assert result.exit_code == 1
    assert "Pre-commit hook failed" in result.output


def test_cli_create_prompts_for_branch_name(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI prompts for branch name when slug is empty."""
    monkeypatch.chdir(tmp_path)

    # Message with only special chars - will generate empty slug
    result = runner.invoke(
        app, ["create", "-m", "...", "--allow-empty"], input="my-custom-branch\n"
    )

    assert result.exit_code == 0
    assert "Could not generate branch name" in result.output
    assert f"Created branch '{_dated('my-custom-branch')}'" in result.output


def test_cli_create_invalid_branch_name_after_empty_prompt(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when user enters invalid name after empty slug prompt."""
    monkeypatch.chdir(tmp_path)

    # Message generates empty slug, user enters invalid name (only special chars)
    result = runner.invoke(app, ["create", "-m", "...", "--allow-empty"], input="...\n")

    assert result.exit_code == 1
    assert "Could not generate branch name" in result.output
    assert "Invalid branch name" in result.output


def test_cli_create_suffixes_past_multiple_existing_branches(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI keeps incrementing suffixes until a name is available."""
    monkeypatch.chdir(tmp_path)

    set_ref(
        temp_repo,
        f"refs/heads/{_dated('feat-existing')}",
        get_ref(temp_repo, "refs/heads/main"),
    )
    set_ref(
        temp_repo,
        f"refs/heads/{_dated('feat-existing')}-2",
        get_ref(temp_repo, "refs/heads/main"),
    )

    result = runner.invoke(app, ["create", "-m", "feat: existing", "--allow-empty"])

    assert result.exit_code == 0
    assert f"Created branch '{_dated('feat-existing')}-3'" in result.output


def test_cli_create_interactive_mode(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create in interactive mode (opens editor)."""
    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.create.open_editor") as mock_editor:
        mock_editor.return_value = "feat: interactive feature"
        result = runner.invoke(app, ["create", "--allow-empty"])

    assert result.exit_code == 0
    assert f"Created branch '{_dated('feat-interactive-feature')}'" in result.output


def test_cli_create_interactive_cancelled(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create when editor is cancelled."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.create.open_editor") as mock_editor:
        mock_editor.return_value = None  # Editor cancelled
        result = runner.invoke(app, ["create", "--allow-empty"])

    assert result.exit_code == 1
    assert "Aborted: empty message" in result.output


def test_cli_create_gitmoji_mode(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create with --gitmoji flag."""
    from unittest.mock import patch

    from shortcake._gitmoji import GITMOJIS

    monkeypatch.chdir(tmp_path)

    with (
        patch("shortcake.commands.create.pick_gitmoji") as mock_gitmoji,
        patch("shortcake.commands.create.open_editor") as mock_editor,
    ):
        mock_gitmoji.return_value = GITMOJIS[0]  # First gitmoji (🎨)
        mock_editor.return_value = "🎨 improve code style"
        result = runner.invoke(app, ["create", "--gitmoji", "--allow-empty"])

    assert result.exit_code == 0
    assert "Created branch" in result.output


def test_cli_create_gitmoji_cancelled(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create when gitmoji picker is cancelled."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.create.pick_gitmoji") as mock_gitmoji:
        mock_gitmoji.return_value = None  # Picker cancelled
        result = runner.invoke(app, ["create", "--gitmoji", "--allow-empty"])

    assert result.exit_code == 1
    assert "Cancelled" in result.output


# ============================================================================
# Create --before / --after CLI tests
# ============================================================================


def test_cli_create_before(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --before inserts branch before current."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["create", "-m", "fix: before-b", "--before", "--allow-empty"]
    )

    assert result.exit_code == 0
    assert f"Created branch '{_dated('fix-before-b')}' from 'branch_a'" in result.output
    assert f"Rebased 'branch_b' onto '{_dated('fix-before-b')}'" in result.output


def test_cli_create_after(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --after inserts branch after current."""
    monkeypatch.chdir(tmp_path)

    # Switch to branch_a first (which has branch_b as child)
    repo_with_stack.set_head("refs/heads/branch_a")
    reset_hard(repo_with_stack)

    result = runner.invoke(
        app, ["create", "-m", "fix: after-a", "--after", "--allow-empty"]
    )

    assert result.exit_code == 0
    assert f"Created branch '{_dated('fix-after-a')}' from 'branch_a'" in result.output
    assert f"Rebased 'branch_b' onto '{_dated('fix-after-a')}'" in result.output


def test_cli_create_before_and_after_error(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create with both --before and --after gives error."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["create", "-m", "fix: both", "--before", "--after", "--allow-empty"],
    )

    assert result.exit_code == 1
    assert "Cannot use both --before and --after" in result.output


def test_cli_create_before_untracked_error(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --before on untracked branch gives error."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["create", "-m", "fix: something", "--before", "--allow-empty"]
    )

    assert result.exit_code == 1
    assert "not tracked" in result.output


def test_cli_create_after_no_children(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --after on leaf branch (no rebase needed)."""
    monkeypatch.chdir(tmp_path)

    # branch_b is the leaf, already checked out
    result = runner.invoke(
        app, ["create", "-m", "fix: leaf", "--after", "--allow-empty"]
    )

    assert result.exit_code == 0
    assert f"Created branch '{_dated('fix-leaf')}' from 'branch_b'" in result.output
    # No rebase message since there are no children
    assert "Rebased" not in result.output


def test_cli_create_before_with_staged_changes(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --before works with staged changes."""
    monkeypatch.chdir(tmp_path)

    # Stage a new file
    new_file = tmp_path / "new_feature.py"
    new_file.write_text("print('hello')")
    add_paths(repo_with_stack, new_file)

    result = runner.invoke(app, ["create", "-m", "fix: staged", "--before"])

    assert result.exit_code == 0
    assert f"Created branch '{_dated('fix-staged')}' from 'branch_a'" in result.output
    assert f"Rebased 'branch_b' onto '{_dated('fix-staged')}'" in result.output


def test_cli_create_after_multiple_children_error(
    repo_with_fork: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --after on branch with multiple children gives error."""
    monkeypatch.chdir(tmp_path)

    # Switch to branch_a which has multiple children (branch_b, branch_c)
    repo_with_fork.set_head("refs/heads/branch_a")
    reset_hard(repo_with_fork)

    result = runner.invoke(
        app, ["create", "-m", "fix: after", "--after", "--allow-empty"]
    )

    assert result.exit_code == 1
    assert "multiple children" in result.output


def test_cli_create_before_conflict_exit(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --before exits with code 1 on conflict."""
    from unittest.mock import patch

    from shortcake.commands.create import CreateResult

    monkeypatch.chdir(tmp_path)

    conflict_result = CreateResult(
        branch="fix-conflict",
        parent="branch_a",
        message="fix: conflict",
        inserted_before="branch_b",
        conflict_branch="branch_b",
    )

    with patch(
        "shortcake.commands.create._create_insert_before", return_value=conflict_result
    ):
        result = runner.invoke(
            app, ["create", "-m", "fix: conflict", "--before", "--allow-empty"]
        )

    assert result.exit_code == 1
    assert "Created branch 'fix-conflict'" in result.output


def test_cli_create_before_rebase_in_progress(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --before with rebase in progress gives error."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch(
        "shortcake.commands.create.git.is_rebase_in_progress", return_value=True
    ):
        result = runner.invoke(
            app, ["create", "-m", "fix: test", "--before", "--allow-empty"]
        )

    assert result.exit_code == 1
    assert "rebase in progress" in result.output


def test_cli_create_before_restack_in_progress(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create --before with restack state gives error."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.create.RestackState.exists", return_value=True):
        result = runner.invoke(
            app, ["create", "-m", "fix: test", "--before", "--allow-empty"]
        )

    assert result.exit_code == 1
    assert "Restack already in progress" in result.output


# ============================================================================
# Navigation CLI tests
# ============================================================================


def test_cli_up_success(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI up command success."""
    monkeypatch.chdir(tmp_path)

    # First adopt the feature branch
    runner.invoke(app, ["adopt"])

    # Switch to main
    switch_branch(repo_with_feature, "main")

    result = runner.invoke(app, ["up"])

    assert result.exit_code == 0
    assert "Switched to 'feature'" in result.output


def test_cli_up_at_top(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI up when already at top."""
    monkeypatch.chdir(tmp_path)

    # Adopt and stay on feature (which has no children)
    runner.invoke(app, ["adopt"])

    result = runner.invoke(app, ["up"])

    assert result.exit_code == 0
    assert "Already at top of stack" in result.output


def test_cli_down_success(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI down command success."""
    monkeypatch.chdir(tmp_path)

    # First adopt the feature branch
    runner.invoke(app, ["adopt"])

    result = runner.invoke(app, ["down"])

    assert result.exit_code == 0
    assert "Switched to 'main'" in result.output
    assert "bottom of stack" in result.output


def test_cli_down_not_tracked(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI down when branch is not tracked."""
    monkeypatch.chdir(tmp_path)

    # Don't adopt, try to go down
    result = runner.invoke(app, ["down"])

    assert result.exit_code == 1
    assert "not tracked" in result.output


def test_cli_top_success(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top command success."""
    monkeypatch.chdir(tmp_path)

    # Adopt and switch to main
    runner.invoke(app, ["adopt"])
    switch_branch(repo_with_feature, "main")

    result = runner.invoke(app, ["top"])

    assert result.exit_code == 0
    assert "Switched to 'feature'" in result.output


def test_cli_top_already_at_top(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top when already at top."""
    monkeypatch.chdir(tmp_path)

    # Adopt and stay on feature
    runner.invoke(app, ["adopt"])

    result = runner.invoke(app, ["top"])

    assert result.exit_code == 0
    assert "Already at top of stack" in result.output


def test_cli_bottom_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI bottom command success with a deeper stack."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create a repo with main → branch_a → branch_b
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    # Create branch_a
    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    # Create branch_b from branch_a
    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    # Now run bottom from branch_b
    result = runner.invoke(app, ["bottom"])

    assert result.exit_code == 0
    assert "Switched to 'branch_a'" in result.output


def test_cli_bottom_already_at_bottom(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI bottom when already at bottom."""
    monkeypatch.chdir(tmp_path)

    # Adopt the feature branch (its parent is main, so it's at bottom)
    runner.invoke(app, ["adopt"])

    result = runner.invoke(app, ["bottom"])

    assert result.exit_code == 0
    assert "Already at bottom of stack" in result.output


def test_cli_help_includes_navigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI help includes navigation commands."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--help"])

    assert "up" in result.output
    assert "down" in result.output
    assert "top" in result.output
    assert "bottom" in result.output


def test_cli_up_multiple_children_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI up prompts when multiple children exist."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create a repo with main → branch_a → (branch_b, branch_c)
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    # Create branch_a
    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    # Create branch_b from branch_a
    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    # Create branch_c from branch_a (fork!)
    set_ref(repo, "refs/heads/branch_c", branch_a_sha)
    repo.set_head("refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    add_paths(repo, file_c)
    commit(repo, msg_c)

    # Switch to branch_a
    switch_branch(repo, "branch_a")

    # Run up with input to select branch_b
    result = runner.invoke(app, ["up"], input="branch_b\n")

    assert result.exit_code == 0
    assert "Multiple children" in result.output
    assert "Switched to 'branch_b'" in result.output


def test_cli_up_multiple_children_invalid_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI up error when invalid child selected."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create a repo with main → branch_a → (branch_b, branch_c)
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    # Create branch_a
    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    # Create branch_b from branch_a
    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    # Create branch_c from branch_a (fork!)
    set_ref(repo, "refs/heads/branch_c", branch_a_sha)
    repo.set_head("refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    add_paths(repo, file_c)
    commit(repo, msg_c)

    # Switch to branch_a
    repo.set_head("refs/heads/branch_a")

    # Run up with invalid input
    result = runner.invoke(app, ["up"], input="invalid_branch\n")

    assert result.exit_code == 1
    assert "not a valid child" in result.output


def test_cli_up_with_child_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI up with explicit child argument."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create repo with main → branch_a → (branch_b, branch_c)
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    set_ref(repo, "refs/heads/branch_c", branch_a_sha)
    repo.set_head("refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    add_paths(repo, file_c)
    commit(repo, msg_c)

    # Switch to branch_a
    switch_branch(repo, "branch_a")

    # Run up with explicit child argument
    result = runner.invoke(app, ["up", "branch_c"])

    assert result.exit_code == 0
    assert "Switched to 'branch_c'" in result.output


def test_cli_up_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI up error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["up"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_down_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI down error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["down"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_top_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["top"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_bottom_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI bottom error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["bottom"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_bottom_not_tracked(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI bottom when branch is not tracked."""
    monkeypatch.chdir(tmp_path)

    # Don't adopt, try to go to bottom
    result = runner.invoke(app, ["bottom"])

    assert result.exit_code == 1
    assert "not tracked" in result.output


def test_cli_top_multiple_children_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top prompts when multiple children exist."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create repo with main → branch_a → (branch_b, branch_c)
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    set_ref(repo, "refs/heads/branch_c", branch_a_sha)
    repo.set_head("refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    add_paths(repo, file_c)
    commit(repo, msg_c)

    # Switch to main
    switch_branch(repo, "main")

    # Run top with input to select branch_b (which is a leaf)
    result = runner.invoke(app, ["top"], input="branch_b\n")

    assert result.exit_code == 0
    assert "Multiple children" in result.output
    assert "Switched to 'branch_b'" in result.output


def test_cli_top_multiple_children_invalid_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top error when invalid child selected."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create repo with main → branch_a → (branch_b, branch_c)
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    set_ref(repo, "refs/heads/branch_c", branch_a_sha)
    repo.set_head("refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    add_paths(repo, file_c)
    commit(repo, msg_c)

    # Switch to main
    repo.set_head("refs/heads/main")

    # Run top with invalid input
    result = runner.invoke(app, ["top"], input="invalid_branch\n")

    assert result.exit_code == 1
    assert "not a valid child" in result.output


def test_cli_up_invalid_child_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI up with invalid child argument."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create repo with main → branch_a → branch_b
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    # Switch to main (which has branch_a as child)
    repo.set_head("refs/heads/main")

    # Run up with invalid child argument (branch_b is not a direct child of main)
    result = runner.invoke(app, ["up", "nonexistent"])

    assert result.exit_code == 1
    assert "not a child" in result.output


def test_cli_down_to_non_trunk_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI down from branch_c to branch_b (not trunk)."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create repo with main → branch_a → branch_b
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    branch_a_sha = get_ref(repo, "refs/heads/branch_a")
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    # Now on branch_b, go down to branch_a (not trunk)
    result = runner.invoke(app, ["down"])

    assert result.exit_code == 0
    assert "Switched to 'branch_a'" in result.output
    assert "bottom of stack" not in result.output


def test_cli_top_fork_then_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top with fork, select branch that has more children."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create: main → branch_a → (branch_b → branch_d, branch_c)
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    branch_a_sha = get_ref(repo, "refs/heads/branch_a")

    # branch_b from branch_a
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    branch_b_sha = get_ref(repo, "refs/heads/branch_b")

    # branch_d from branch_b (so branch_b has a child)
    set_ref(repo, "refs/heads/branch_d", branch_b_sha)
    repo.set_head("refs/heads/branch_d")
    trailers_d = Trailers(parent_branch="branch_b")
    msg_d = trailers_d.apply_to("feat: d")
    file_d = tmp_path / "d.txt"
    file_d.write_text("d")
    add_paths(repo, file_d)
    commit(repo, msg_d)

    # branch_c from branch_a (fork sibling of branch_b)
    set_ref(repo, "refs/heads/branch_c", branch_a_sha)
    repo.set_head("refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    add_paths(repo, file_c)
    commit(repo, msg_c)

    # Switch to main
    switch_branch(repo, "main")

    # Run top, select branch_b which has branch_d as child
    result = runner.invoke(app, ["top"], input="branch_b\n")

    assert result.exit_code == 0
    assert "Multiple children" in result.output
    assert "Switched to 'branch_b'" in result.output
    # Should continue to branch_d
    assert "Switched to 'branch_d'" in result.output


def test_cli_top_fork_then_another_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top with fork, select branch that has another fork."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # Create: main → branch_a → (branch_b → (branch_d, branch_e), branch_c)
    repo = init_repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/branch_a", main_sha)
    repo.set_head("refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(repo, file_a)
    commit(repo, msg_a)

    branch_a_sha = get_ref(repo, "refs/heads/branch_a")

    # branch_b from branch_a
    set_ref(repo, "refs/heads/branch_b", branch_a_sha)
    repo.set_head("refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(repo, file_b)
    commit(repo, msg_b)

    branch_b_sha = get_ref(repo, "refs/heads/branch_b")

    # branch_d from branch_b
    set_ref(repo, "refs/heads/branch_d", branch_b_sha)
    repo.set_head("refs/heads/branch_d")
    trailers_d = Trailers(parent_branch="branch_b")
    msg_d = trailers_d.apply_to("feat: d")
    file_d = tmp_path / "d.txt"
    file_d.write_text("d")
    add_paths(repo, file_d)
    commit(repo, msg_d)

    # branch_e from branch_b (another fork!)
    set_ref(repo, "refs/heads/branch_e", branch_b_sha)
    repo.set_head("refs/heads/branch_e")
    trailers_e = Trailers(parent_branch="branch_b")
    msg_e = trailers_e.apply_to("feat: e")
    file_e = tmp_path / "e.txt"
    file_e.write_text("e")
    add_paths(repo, file_e)
    commit(repo, msg_e)

    # branch_c from branch_a
    set_ref(repo, "refs/heads/branch_c", branch_a_sha)
    repo.set_head("refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    add_paths(repo, file_c)
    commit(repo, msg_c)

    # Switch to main
    switch_branch(repo, "main")

    # Run top, select branch_b which has another fork (branch_d, branch_e)
    result = runner.invoke(app, ["top"], input="branch_b\n")

    assert result.exit_code == 0
    assert "Multiple children" in result.output
    assert "Switched to 'branch_b'" in result.output
    # Should hit another fork and tell user to run again
    assert "Run 'sc top' again" in result.output


# ============================================================================
# Modify CLI tests
# ============================================================================


def test_cli_modify_with_message_creates_new_commit(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify command with -m option creates new commit."""
    monkeypatch.chdir(tmp_path)

    # First adopt to add trailer
    runner.invoke(app, ["adopt"])

    old_oid = repo_with_feature.head.target

    # Stage a new file (required for -m)
    new_file = tmp_path / "new.txt"
    new_file.write_text("content")
    add_paths(repo_with_feature, new_file)

    result = runner.invoke(app, ["modify", "-m", "feat: new commit"])

    assert result.exit_code == 0
    assert "Created commit on 'feature'" in result.output

    # Verify old commit is parent of new commit
    new_commit = repo_with_feature.get(str(repo_with_feature.head.target))
    assert old_oid in new_commit.parent_ids


def test_cli_modify_preserves_trailer(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify preserves Shortcake-Parent trailer."""
    from shortcake._trailers import Trailers

    monkeypatch.chdir(tmp_path)

    # First adopt to add trailer
    runner.invoke(app, ["adopt"])

    # Stage a new file (required for -m)
    new_file = tmp_path / "new.txt"
    new_file.write_text("content")
    add_paths(repo_with_feature, new_file)

    result = runner.invoke(app, ["modify", "-m", "feat: completely new message"])

    assert result.exit_code == 0

    # Verify trailer is still there
    from shortcake import _git as git

    head_sha = str(repo_with_feature.head.target).encode()
    message = git.get_commit_message(repo_with_feature, head_sha)
    trailers = Trailers.from_message(message)
    assert trailers.parent_branch == "main"


def test_cli_modify_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["modify", "-e"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_modify_interactive(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify with -e opens editor to amend."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.modify.open_editor") as mock_editor:
        mock_editor.return_value = "feat: edited message"
        result = runner.invoke(app, ["modify", "-e"])

    assert result.exit_code == 0
    assert "Amended commit on 'feature'" in result.output


def test_cli_modify_editor_aborted(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify with -e when editor is cancelled/empty."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.modify.open_editor") as mock_editor:
        mock_editor.return_value = None  # Editor cancelled/empty
        result = runner.invoke(app, ["modify", "-e"])

    assert result.exit_code == 1
    assert "Aborted: empty message" in result.output


def test_cli_modify_with_staged_changes(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify includes staged changes."""
    monkeypatch.chdir(tmp_path)

    # Stage a new file
    new_file = tmp_path / "staged.txt"
    new_file.write_text("staged content")
    add_paths(repo_with_feature, new_file)

    result = runner.invoke(app, ["modify", "-m", "feat: with staged changes"])

    assert result.exit_code == 0

    # Verify file is in the commit
    head_sha = str(repo_with_feature.head.target).encode()
    commit = repo_with_feature.get(
        head_sha.decode() if isinstance(head_sha, bytes) else str(head_sha)
    )
    tree = repo_with_feature.get(str(commit.tree_id))
    files = [entry.name.encode() for entry in tree]
    assert b"staged.txt" in files


def test_cli_modify_no_verify(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify with --no-verify skips hooks."""
    monkeypatch.chdir(tmp_path)

    # Create a failing hook
    hooks_dir = Path(repo_with_feature.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    add_paths(repo_with_feature, new_file)

    # With --no-verify, should succeed despite failing hook
    result = runner.invoke(app, ["modify", "-m", "feat: test", "-n"])

    assert result.exit_code == 0
    assert "Created commit" in result.output


def test_cli_modify_hook_failure(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify fails when pre-commit hook fails."""
    monkeypatch.chdir(tmp_path)

    # Create a failing hook
    hooks_dir = Path(repo_with_feature.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'Hook failed!'\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    add_paths(repo_with_feature, new_file)

    result = runner.invoke(app, ["modify", "-m", "feat: test"])

    assert result.exit_code == 1
    assert "Pre-commit hook failed" in result.output


def test_cli_modify_no_flags_amends_with_staged(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify with no flags amends with staged changes."""
    monkeypatch.chdir(tmp_path)

    old_sha = str(repo_with_feature.head.target).encode()

    # Stage a new file
    new_file = tmp_path / "staged.txt"
    new_file.write_text("staged content")
    add_paths(repo_with_feature, new_file)

    result = runner.invoke(app, ["modify"])

    assert result.exit_code == 0
    assert "Amended commit on 'feature'" in result.output

    # Verify commit was amended (new SHA, same parent)
    new_sha = str(repo_with_feature.head.target).encode()
    assert new_sha != old_sha


def test_cli_modify_no_flags_no_staged_error(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify with no flags requires staged changes."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["modify"])

    assert result.exit_code == 1
    assert "No staged changes to amend" in result.output


def test_cli_modify_both_flags_error(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify cannot use both -m and -e."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["modify", "-m", "message", "-e"])

    assert result.exit_code == 1
    assert "Cannot use both -m and -e" in result.output


def test_cli_modify_message_no_staged_error(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify -m requires staged changes."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["modify", "-m", "feat: message"])

    assert result.exit_code == 1
    assert "No staged changes to commit" in result.output


def test_cli_help_includes_modify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI help includes modify command."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--help"])

    assert "modify" in result.output


# ============================================================================
# Log CLI tests
# ============================================================================


def test_cli_log_basic(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI log command shows commits with tree format."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "◉ feature" in result.output
    assert "●" in result.output
    assert "Add feature" in result.output


def test_cli_log_tracked_branch(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI log on tracked branch shows parent."""
    monkeypatch.chdir(tmp_path)

    # First adopt the branch
    runner.invoke(app, ["adopt"])

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "◉ feature" in result.output
    assert "Add feature" in result.output
    assert "◯ main" in result.output


def test_cli_log_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI log error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_log_no_commits(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI log when no commits on branch relative to parent (after merge)."""
    monkeypatch.chdir(tmp_path)

    # Create feature branch with one commit
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    file1 = tmp_path / "file1.txt"
    file1.write_text("content")
    add_paths(temp_repo, file1)
    commit(temp_repo, b"Feature commit")

    # Adopt the branch
    runner.invoke(app, ["adopt"])

    # Simulate merge: fast-forward main to feature's head
    feature_sha = get_ref(temp_repo, "refs/heads/feature")
    set_ref(temp_repo, "refs/heads/main", feature_sha)

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "No commits on this branch" in result.output


def test_cli_log_multiple_commits(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI log with multiple commits shows tree format."""
    monkeypatch.chdir(tmp_path)

    # Create feature branch with multiple commits
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # First commit
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    add_paths(temp_repo, file1)
    commit(temp_repo, b"First commit")

    # Second commit
    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")
    add_paths(temp_repo, file2)
    commit(temp_repo, b"Second commit")

    # Adopt and log
    runner.invoke(app, ["adopt"])
    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert "◉ feature" in result.output
    assert "● " in result.output  # Commit bullets
    assert "First commit" in result.output
    assert "Second commit" in result.output
    assert "◯ main" in result.output
    assert "│" in result.output  # Pipe connectors


def test_cli_help_includes_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI help includes log command."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--help"])

    assert "log" in result.output


def test_cli_log_json(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test log --json emits commits in a JSON envelope."""
    import json

    monkeypatch.chdir(tmp_path)

    # Create feature branch with one commit and adopt it
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    file1 = tmp_path / "file1.txt"
    file1.write_text("content")
    add_paths(temp_repo, file1)
    commit(temp_repo, b"Feature commit")

    runner.invoke(app, ["adopt"])

    result = runner.invoke(app, ["log", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["branch"] == "feature"
    assert document["data"]["parent"] == "main"
    commits = document["data"]["commits"]
    assert len(commits) == 1
    assert commits[0]["subject"] == "Feature commit"
    assert len(commits[0]["sha"]) == 7


def test_cli_log_json_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test log --json in detached HEAD state emits a JSON error envelope."""
    import json

    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "HEAD", main_sha)

    result = runner.invoke(app, ["log", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "detached_head"
    assert document["error"]["message"] == "Cannot log in detached HEAD state"


def test_cli_create_positional_name(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create accepts a positional branch name overriding the slug."""
    from datetime import date

    monkeypatch.chdir(tmp_path)

    file1 = tmp_path / "file1.txt"
    file1.write_text("content")
    add_paths(temp_repo, file1)

    result = runner.invoke(app, ["create", "my-custom-name", "-m", "Add feature"])

    assert result.exit_code == 0
    today = date.today().isoformat()
    assert f"Created branch '{today}-my-custom-name' from 'main'" in result.output

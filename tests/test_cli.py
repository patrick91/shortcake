import stat
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


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
    repo_with_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

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
    assert "Created branch 'feat-add-new-feature' from 'main'" in result.output


def test_cli_create_no_staged_changes_error(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create fails without staged changes."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["create", "-m", "feat: something"])

    assert result.exit_code == 1
    assert "No staged changes" in result.output
    assert "--allow-empty" in result.output


def test_cli_create_prompts_when_branch_exists(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI prompts for new name when branch exists."""
    monkeypatch.chdir(tmp_path)

    # Create a branch first
    temp_repo.refs[b"refs/heads/feat-existing"] = temp_repo.refs[b"refs/heads/main"]

    # Provide alternative name via input
    result = runner.invoke(
        app,
        ["create", "-m", "feat: existing", "--allow-empty"],
        input="my-new-branch\n",
    )

    assert result.exit_code == 0
    assert "already exists" in result.output
    assert "Created branch 'my-new-branch'" in result.output


def test_cli_create_error_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = main_sha

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
    hooks_dir = Path(temp_repo.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(temp_repo, paths=[str(new_file)])

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
    hooks_dir = Path(temp_repo.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'Hook failed!'\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(temp_repo, paths=[str(new_file)])

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
    assert "Created branch 'my-custom-branch'" in result.output


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


def test_cli_create_invalid_branch_name_after_exists_prompt(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test error when user enters invalid name after branch exists prompt."""
    monkeypatch.chdir(tmp_path)

    # Create existing branch
    temp_repo.refs[b"refs/heads/feat-existing"] = temp_repo.refs[b"refs/heads/main"]

    # User enters invalid name (only special chars)
    result = runner.invoke(
        app, ["create", "-m", "feat: existing", "--allow-empty"], input="...\n"
    )

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert "Invalid branch name" in result.output


def test_cli_create_interactive_mode(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI create in interactive mode (opens editor)."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.create.open_editor") as mock_editor:
        mock_editor.return_value = "feat: interactive feature"
        result = runner.invoke(app, ["create", "--allow-empty"])

    assert result.exit_code == 0
    assert "Created branch 'feat-interactive-feature'" in result.output


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
    porcelain.switch(repo_with_feature, "main")

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
    porcelain.switch(repo_with_feature, "main")

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    # Create branch_b from branch_a
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    # Create branch_b from branch_a
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    # Create branch_c from branch_a (fork!)
    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    # Switch to branch_a
    porcelain.switch(repo, "branch_a")

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create branch_a
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    # Create branch_b from branch_a
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    # Create branch_c from branch_a (fork!)
    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    # Switch to branch_a
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    # Switch to branch_a
    porcelain.switch(repo, "branch_a")

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
    main_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = main_sha

    result = runner.invoke(app, ["up"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_down_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI down error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = main_sha

    result = runner.invoke(app, ["down"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_top_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI top error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = main_sha

    result = runner.invoke(app, ["top"])

    assert result.exit_code == 1
    assert "detached HEAD" in result.output


def test_cli_bottom_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI bottom error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = main_sha

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    # Switch to main
    porcelain.switch(repo, "main")

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    # Switch to main
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    # Switch to main (which has branch_a as child)
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # branch_b from branch_a
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    branch_b_sha = repo.refs[b"refs/heads/branch_b"]

    # branch_d from branch_b (so branch_b has a child)
    repo.refs[b"refs/heads/branch_d"] = branch_b_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_d")
    trailers_d = Trailers(parent_branch="branch_b")
    msg_d = trailers_d.apply_to("feat: d")
    file_d = tmp_path / "d.txt"
    file_d.write_text("d")
    porcelain.add(repo, paths=[str(file_d)])
    porcelain.commit(repo, message=msg_d.encode())

    # branch_c from branch_a (fork sibling of branch_b)
    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    # Switch to main
    porcelain.switch(repo, "main")

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
    repo = Repo.init(tmp_path, default_branch=b"main")
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # branch_b from branch_a
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    branch_b_sha = repo.refs[b"refs/heads/branch_b"]

    # branch_d from branch_b
    repo.refs[b"refs/heads/branch_d"] = branch_b_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_d")
    trailers_d = Trailers(parent_branch="branch_b")
    msg_d = trailers_d.apply_to("feat: d")
    file_d = tmp_path / "d.txt"
    file_d.write_text("d")
    porcelain.add(repo, paths=[str(file_d)])
    porcelain.commit(repo, message=msg_d.encode())

    # branch_e from branch_b (another fork!)
    repo.refs[b"refs/heads/branch_e"] = branch_b_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_e")
    trailers_e = Trailers(parent_branch="branch_b")
    msg_e = trailers_e.apply_to("feat: e")
    file_e = tmp_path / "e.txt"
    file_e.write_text("e")
    porcelain.add(repo, paths=[str(file_e)])
    porcelain.commit(repo, message=msg_e.encode())

    # branch_c from branch_a
    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")
    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    # Switch to main
    porcelain.switch(repo, "main")

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

    old_sha = repo_with_feature.head()

    # Stage a new file (required for -m)
    new_file = tmp_path / "new.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    result = runner.invoke(app, ["modify", "-m", "feat: new commit"])

    assert result.exit_code == 0
    assert "Created commit on 'feature'" in result.output

    # Verify old commit is parent of new commit
    new_commit = repo_with_feature[repo_with_feature.head()]
    assert old_sha in new_commit.parents


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
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    result = runner.invoke(app, ["modify", "-m", "feat: completely new message"])

    assert result.exit_code == 0

    # Verify trailer is still there
    from shortcake import _git as git

    head_sha = repo_with_feature.head()
    message = git.get_commit_message(repo_with_feature, head_sha)
    trailers = Trailers.from_message(message)
    assert trailers.parent_branch == "main"


def test_cli_modify_detached_head(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify error in detached HEAD state."""
    monkeypatch.chdir(tmp_path)

    # Detach HEAD
    main_sha = temp_repo.refs[b"refs/heads/main"]
    del temp_repo.refs[b"HEAD"]
    temp_repo.refs[b"HEAD"] = main_sha

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
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    result = runner.invoke(app, ["modify", "-m", "feat: with staged changes"])

    assert result.exit_code == 0

    # Verify file is in the commit
    head_sha = repo_with_feature.head()
    commit = repo_with_feature[head_sha]
    tree = repo_with_feature[commit.tree]
    files = [entry.path for entry in tree.items()]
    assert b"staged.txt" in files


def test_cli_modify_no_verify(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify with --no-verify skips hooks."""
    monkeypatch.chdir(tmp_path)

    # Create a failing hook
    hooks_dir = Path(repo_with_feature.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

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
    hooks_dir = Path(repo_with_feature.controldir()) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'Hook failed!'\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    # Stage a file to trigger hook check
    new_file = tmp_path / "test.txt"
    new_file.write_text("content")
    porcelain.add(repo_with_feature, paths=[str(new_file)])

    result = runner.invoke(app, ["modify", "-m", "feat: test"])

    assert result.exit_code == 1
    assert "Pre-commit hook failed" in result.output


def test_cli_modify_no_flags_defaults_to_amend(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test CLI modify with no flags defaults to amend with editor."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)

    with patch("shortcake.commands.modify.open_editor") as mock_editor:
        mock_editor.return_value = "feat: amended via default"
        result = runner.invoke(app, ["modify"])

    assert result.exit_code == 0
    assert "Amended commit on 'feature'" in result.output


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

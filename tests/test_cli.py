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

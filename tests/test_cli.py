from pathlib import Path

import pytest
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

import pytest
from pathlib import Path
from typer.testing import CliRunner
from dulwich import porcelain

from shortcake.cli import app

runner = CliRunner()


def test_cli_adopt_success(repo_with_feature, tmp_path: Path, monkeypatch):
    """Test CLI adopt command success."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["adopt"])

    assert result.exit_code == 0
    assert "Adopted 'feature' with parent 'main'" in result.output


def test_cli_adopt_with_branch(repo_with_feature, tmp_path: Path, monkeypatch):
    """Test CLI adopt with explicit branch argument."""
    monkeypatch.chdir(tmp_path)
    # Switch to main first
    repo_with_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")

    result = runner.invoke(app, ["adopt", "feature"])

    assert result.exit_code == 0
    assert "Adopted 'feature'" in result.output


def test_cli_adopt_with_parent_option(repo_with_feature, tmp_path: Path, monkeypatch):
    """Test CLI adopt with --parent option."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["adopt", "--parent", "main"])

    assert result.exit_code == 0


def test_cli_adopt_error(temp_repo, tmp_path: Path, monkeypatch):
    """Test CLI adopt command error handling."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["adopt", "main"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Cannot adopt default branch" in result.output


def test_cli_help(tmp_path: Path, monkeypatch):
    """Test CLI shows help."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Shortcake" in result.output
    assert "adopt" in result.output

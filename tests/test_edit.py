"""Tests for the edit command."""

from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_edit_help():
    result = runner.invoke(app, ["edit", "--help"])
    assert result.exit_code == 0
    assert "Edit the current stack by amending the commit" in result.stdout

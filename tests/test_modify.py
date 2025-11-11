"""Tests for the modify command."""

from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_modify_help():
    """Test modify command help."""
    result = runner.invoke(app, ["modify", "--help"])
    assert result.exit_code == 0
    assert "Alias for edit" in result.stdout

"""Tests for the version command."""

from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_version():
    """Test version command."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Shortcake version" in result.stdout
    assert "0.1.0" in result.stdout

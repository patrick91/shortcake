"""Tests for the create command."""

from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_create_help():
    """Test create command help."""
    result = runner.invoke(app, ["create", "--help"])
    assert result.exit_code == 0
    assert "Create a stack with a new branch and commit" in result.stdout
    assert "keep" in result.stdout.lower()
    assert "emoji" in result.stdout.lower()

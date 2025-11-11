"""Tests for the hello command."""

from inline_snapshot import snapshot
from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_hello_default():
    """Test hello command with default name."""
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert result.stdout == snapshot("Hello World!\n")


def test_hello_with_name():
    """Test hello command with custom name."""
    result = runner.invoke(app, ["hello", "--name", "Patrick"])
    assert result.exit_code == 0
    assert result.stdout == snapshot("Hello Patrick!\n")

"""Tests for the config command."""

import os

from typer.testing import CliRunner

from shortcake import config
from shortcake.cli import app

runner = CliRunner()


def test_config_help():
    """Test config command help."""
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "Manage shortcake configuration" in result.stdout


def test_config_list():
    """Test config list."""
    # First, remove any existing config
    config_path = config.get_config_path()
    if config_path.exists():
        os.remove(config_path)

    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert "keep_emoji = False" in result.stdout


def test_config_set_and_get():
    """Test setting and getting configuration values."""
    # Set a value
    result = runner.invoke(app, ["config", "set", "keep_emoji", "true"])
    assert result.exit_code == 0
    assert "Set keep_emoji = true" in result.stdout

    # Get the value
    result = runner.invoke(app, ["config", "get", "keep_emoji"])
    assert result.exit_code == 0
    assert "keep_emoji = True" in result.stdout

    # List all config
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert "keep_emoji = True" in result.stdout


def test_config_set_false():
    """Test setting keep_emoji to false."""
    result = runner.invoke(app, ["config", "set", "keep_emoji", "false"])
    assert result.exit_code == 0
    assert "Set keep_emoji = false" in result.stdout

    result = runner.invoke(app, ["config", "get", "keep_emoji"])
    assert result.exit_code == 0
    assert "keep_emoji = False" in result.stdout


def test_config_invalid_action():
    """Test config with invalid action."""
    result = runner.invoke(app, ["config", "invalid"])
    assert result.exit_code == 1
    assert "Unknown action" in result.stdout

"""Tests for the CLI commands."""

import subprocess
import tempfile
from pathlib import Path

import pytest
from inline_snapshot import snapshot
from typer.testing import CliRunner

from shortcake.cli import app, _generate_branch_name

runner = CliRunner()


class TestGenerateBranchName:
    """Test the _generate_branch_name function."""

    def test_basic_message(self):
        """Test basic commit message conversion."""
        assert _generate_branch_name("Add new feature") == "add-new-feature"

    def test_multiple_spaces(self):
        """Test that multiple spaces are converted to single hyphen."""
        assert _generate_branch_name("Add  new   feature") == "add-new-feature"

    def test_special_characters(self):
        """Test that special characters are removed."""
        assert _generate_branch_name("Add new feature!@#$%") == "add-new-feature"

    def test_emoji_removed_by_default(self):
        """Test that emojis are removed by default."""
        assert _generate_branch_name("🚀 Add new feature") == "add-new-feature"

    def test_emoji_kept_when_flag_set(self):
        """Test that emojis are kept when keep_emoji=True."""
        result = _generate_branch_name("🚀 Add new feature", keep_emoji=True)
        assert result == "🚀-add-new-feature"

    def test_multiple_emojis_kept(self):
        """Test multiple emojis are kept when flag is set."""
        result = _generate_branch_name("🔥 🚀 Add feature", keep_emoji=True)
        assert result == "🔥-🚀-add-feature"

    def test_length_limit(self):
        """Test that branch names are limited to 50 characters."""
        long_message = "a" * 100
        result = _generate_branch_name(long_message)
        assert len(result) == 50

    def test_leading_trailing_hyphens_removed(self):
        """Test that leading and trailing hyphens are removed."""
        assert _generate_branch_name("!!! Add feature !!!") == "add-feature"

    def test_empty_after_cleanup(self):
        """Test handling of messages that become empty after cleanup."""
        # Just emojis with keep_emoji=False
        result = _generate_branch_name("🚀🔥", keep_emoji=False)
        assert result == ""

    def test_mixed_case_converted_to_lowercase(self):
        """Test that mixed case is converted to lowercase."""
        assert _generate_branch_name("Add NEW Feature") == "add-new-feature"


class TestHelloCommand:
    """Test the hello command."""

    def test_hello_default(self):
        """Test hello command with default name."""
        result = runner.invoke(app, ["hello"])
        assert result.exit_code == 0
        assert result.stdout == snapshot("Hello World!\n")

    def test_hello_with_name(self):
        """Test hello command with custom name."""
        result = runner.invoke(app, ["hello", "--name", "Patrick"])
        assert result.exit_code == 0
        assert result.stdout == snapshot("Hello Patrick!\n")


class TestVersionCommand:
    """Test the version command."""

    def test_version(self):
        """Test version command."""
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "Shortcake version" in result.stdout
        assert "0.1.0" in result.stdout


class TestCreateCommand:
    """Test the create command."""

    def test_create_help(self):
        """Test create command help."""
        result = runner.invoke(app, ["create", "--help"])
        assert result.exit_code == 0
        assert "Create a stack with a new branch and commit" in result.stdout
        assert "keep" in result.stdout.lower()
        assert "emoji" in result.stdout.lower()


class TestEditCommand:
    """Test the edit command."""

    def test_edit_help(self):
        """Test edit command help."""
        result = runner.invoke(app, ["edit", "--help"])
        assert result.exit_code == 0
        assert "Edit the current stack by amending the commit" in result.stdout


class TestModifyCommand:
    """Test the modify command (alias for edit)."""

    def test_modify_help(self):
        """Test modify command help."""
        result = runner.invoke(app, ["modify", "--help"])
        assert result.exit_code == 0
        assert "Alias for edit" in result.stdout


class TestConfigCommand:
    """Test the config command."""

    def test_config_help(self):
        """Test config command help."""
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "Manage shortcake configuration" in result.stdout

    def test_config_list_empty(self):
        """Test config list with no configuration."""
        # First, remove any existing config
        import os
        from shortcake import config
        config_path = config.get_config_path()
        if config_path.exists():
            os.remove(config_path)
        
        result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 0
        assert "No configuration settings found" in result.stdout

    def test_config_set_and_get(self):
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

    def test_config_set_false(self):
        """Test setting keep_emoji to false."""
        result = runner.invoke(app, ["config", "set", "keep_emoji", "false"])
        assert result.exit_code == 0
        assert "Set keep_emoji = false" in result.stdout
        
        result = runner.invoke(app, ["config", "get", "keep_emoji"])
        assert result.exit_code == 0
        assert "keep_emoji = False" in result.stdout

    def test_config_invalid_action(self):
        """Test config with invalid action."""
        result = runner.invoke(app, ["config", "invalid"])
        assert result.exit_code == 1
        assert "Unknown action" in result.stdout



from pathlib import Path

from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_config_help():
    result = runner.invoke(app, ["config", "--help"])

    assert result.exit_code == 0
    assert "Manage shortcake configuration" in result.stdout


def test_config_list(isolated_config: Path):
    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0
    assert "keep_emoji = False" in result.stdout


def test_config_set_and_get(isolated_config: Path):
    result = runner.invoke(app, ["config", "set", "keep_emoji", "true"])

    assert result.exit_code == 0
    assert "Set keep_emoji = true" in result.stdout

    result = runner.invoke(app, ["config", "get", "keep_emoji"])

    assert result.exit_code == 0
    assert "keep_emoji = True" in result.stdout

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0
    assert "keep_emoji = True" in result.stdout


def test_config_set_false(isolated_config: Path):
    result = runner.invoke(app, ["config", "set", "keep_emoji", "false"])

    assert result.exit_code == 0
    assert "Set keep_emoji = false" in result.stdout

    result = runner.invoke(app, ["config", "get", "keep_emoji"])

    assert result.exit_code == 0
    assert "keep_emoji = False" in result.stdout


def test_config_invalid_action():
    result = runner.invoke(app, ["config", "invalid"])

    assert result.exit_code == 1
    assert "Unknown action" in result.stderr

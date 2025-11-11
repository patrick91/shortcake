"""Configuration management for shortcake."""

from pathlib import Path

from pydantic import BaseModel, Field


class ShortcakeConfig(BaseModel):
    """Configuration model for shortcake."""

    keep_emoji: bool = Field(default=False, description="Whether to keep emojis in branch names")


def get_config_path() -> Path:
    """Get the path to the configuration file in the user's home directory."""
    config_dir = Path.home() / ".shortcake"
    config_dir.mkdir(exist_ok=True)
    return config_dir / "config.json"


def load_config() -> ShortcakeConfig:
    """Load configuration from the user's config file.

    Returns:
        ShortcakeConfig instance with configuration values.
        Returns default config if file doesn't exist.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return ShortcakeConfig()

    try:
        with open(config_path) as f:
            config_data = f.read()
            return ShortcakeConfig.model_validate_json(config_data)
    except Exception:
        return ShortcakeConfig()


def save_config(config: ShortcakeConfig) -> None:
    """Save configuration to the user's config file.

    Args:
        config: ShortcakeConfig instance containing configuration values to save.
    """
    config_path = get_config_path()
    with open(config_path, "w") as f:
        f.write(config.model_dump_json(indent=2))


def get_keep_emoji() -> bool:
    """Get the keep_emoji configuration value.

    Returns:
        True if emojis should be kept in branch names, False otherwise.
    """
    config = load_config()
    return config.keep_emoji


def set_keep_emoji(value: bool) -> None:
    """Set the keep_emoji configuration value.

    Args:
        value: True to keep emojis in branch names, False to remove them.
    """
    config = load_config()
    config.keep_emoji = value
    save_config(config)

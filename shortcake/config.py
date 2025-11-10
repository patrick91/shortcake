"""Configuration management for shortcake."""

import json
from pathlib import Path
from typing import Any


def get_config_path() -> Path:
    """Get the path to the configuration file in the user's home directory."""
    config_dir = Path.home() / ".shortcake"
    config_dir.mkdir(exist_ok=True)
    return config_dir / "config.json"


def load_config() -> dict[str, Any]:
    """Load configuration from the user's config file.
    
    Returns:
        Dictionary containing configuration values. Returns empty dict if config doesn't exist.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_config(config: dict[str, Any]) -> None:
    """Save configuration to the user's config file.
    
    Args:
        config: Dictionary containing configuration values to save.
    """
    config_path = get_config_path()
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def get_keep_emoji() -> bool:
    """Get the keep_emoji configuration value.
    
    Returns:
        True if emojis should be kept in branch names, False otherwise.
    """
    config = load_config()
    return config.get("keep_emoji", False)


def set_keep_emoji(value: bool) -> None:
    """Set the keep_emoji configuration value.
    
    Args:
        value: True to keep emojis in branch names, False to remove them.
    """
    config = load_config()
    config["keep_emoji"] = value
    save_config(config)

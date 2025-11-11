# shortcake

A CLI application built with [typer](https://typer.tiangolo.com/) and [uv](https://docs.astral.sh/uv/), supporting only Python 3.14.

## Requirements

- Python 3.14+
- uv package manager

## Installation

Install dependencies using uv:

```bash
uv sync
```

## Usage

Run the CLI using uv:

```bash
# Show help
uv run shortcake --help

# Say hello with default greeting
uv run shortcake hello

# Say hello with custom name
uv run shortcake hello --name "Patrick"

# Show version
uv run shortcake version
```

## Commands

### `hello`
Say hello to someone.

Options:
- `--name TEXT`: Name to greet (default: "World")

### `version`
Show the current version of shortcake.

### `create`
Create a stack with a new branch and commit.

This command helps you create stacked PRs by:
1. Prompting for a commit message (emojis are fully supported! 🎉)
2. Generating a branch name from the commit message (lowercase, hyphenated, alphanumeric only)
3. Creating and checking out a new branch
4. Staging all changes
5. Creating a commit with your message

**Emoji Support:**
- Commit messages fully support emojis
- Emoji handling in branch names is controlled by the `keep_emoji` configuration setting
- Use `shortcake config set keep_emoji true` to preserve emojis in branch names
- Use `shortcake config set keep_emoji false` to remove emojis from branch names (default)

**Note:** Future enhancement will include gitmoji integration for conventional emoji commits.

Example:
```bash
# Basic usage (emojis removed from branch name by default)
uv run shortcake create
# You'll be prompted: Enter commit message: 🚀 Add rocket feature
# Creates branch: add-rocket-feature
# Creates commit: 🚀 Add rocket feature

# Configure to keep emojis in branch names
uv run shortcake config set keep_emoji true
uv run shortcake create
# You'll be prompted: Enter commit message: 🔥 Add fire feature
# Creates branch: 🔥-add-fire-feature
# Creates commit: 🔥 Add fire feature
```

### `edit` / `modify`
Edit the current stack by amending the commit.

This command helps you modify the current stack by:
1. Staging all changes
2. Opening the commit in your configured git editor for amendment

Example:
```bash
# Make some changes to your files
uv run shortcake edit
# Editor opens with the current commit message
# Edit the message if needed, save and close

# Or use the modify alias
uv run shortcake modify
```

### `config`
Manage shortcake configuration settings.

Configuration is stored in `~/.shortcake/config.json` in your home directory.

Available settings:
- `keep_emoji`: Whether to keep emojis in branch names (true/false, default: false)

**Actions:**
- `list` - List all configuration settings
- `get <key>` - Get a specific configuration value
- `set <key> <value>` - Set a configuration value

Example:
```bash
# List all configuration
uv run shortcake config list

# Get a specific setting
uv run shortcake config get keep_emoji

# Set keep_emoji to true
uv run shortcake config set keep_emoji true

# Set keep_emoji to false
uv run shortcake config set keep_emoji false
```

## Development

This project uses uv for dependency management and requires Python 3.14 or higher.
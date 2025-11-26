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

# Show version
uv run shortcake version

# List tracked branches
uv run shortcake ls

# Adopt an existing branch
uv run shortcake adopt
```

## Commands

### `version`
Show the current version of shortcake.

### `create`
Create a stack with a new branch and commit.

This command helps you create stacked PRs by:
1. Creating a temporary branch
2. Opening your configured editor ($EDITOR) to compose the commit message (emojis are fully supported! 🎉)
3. Creating the commit
4. Generating a branch name from the commit message (lowercase, hyphenated, alphanumeric only)
5. Renaming the temporary branch to the final branch name

**Important:** Stage your changes with `git add` before running this command. Only staged changes will be committed.

**Emoji Support:**
- Commit messages fully support emojis
- Emoji handling in branch names is controlled by the `keep_emoji` configuration setting
- Use `shortcake config set keep_emoji true` to preserve emojis in branch names
- Use `shortcake config set keep_emoji false` to remove emojis from branch names (default)

**Note:** Future enhancement will include gitmoji integration for conventional emoji commits.

Example:
```bash
# Stage your changes first
git add .

# Basic usage (emojis removed from branch name by default)
uv run shortcake create
# Opens your editor to compose commit message
# Type: 🚀 Add rocket feature
# Save and close
# Creates commit: 🚀 Add rocket feature
# Creates branch: add-rocket-feature

# Configure to keep emojis in branch names
uv run shortcake config set keep_emoji true

# Stage changes and create
git add .
uv run shortcake create
# Opens your editor to compose commit message
# Type: 🔥 Add fire feature
# Save and close
# Creates commit: 🔥 Add fire feature
# Creates branch: 🔥-add-fire-feature
```

### `edit` / `modify`
Edit the current stack by amending the commit.

This command helps you modify the current stack by amending the previous commit without opening an editor.

**Important:** Stage your changes with `git add` before running this command. Only staged changes will be amended.

Example:
```bash
# Make some changes to your files
echo "more content" >> file.txt

# Stage your changes first
git add .

# Amend the previous commit
uv run shortcake edit
# Successfully amended the commit (reuses previous commit message)

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

### `ls`
List all branches tracked by shortcake.

This command displays all branches that have been created with `shortcake create` or adopted with `shortcake adopt`, showing their parent relationships.

Example:
```bash
# List all tracked branches
uv run shortcake ls

# Example output:
# Tracked branches:
#   feature-1 (parent: main)
#   feature-2 (parent: feature-1)
#   feature-3 (parent: feature-2)
```

### `adopt`
Adopt an existing Git branch to be tracked by shortcake.

This command adds shortcake tracking to branches that were created manually (without `shortcake create`). The parent branch is **automatically detected** from your Git history - shortcake finds the closest ancestor branch and sets it as the parent.

**Key Features:**
- 🎯 **Smart Parent Detection**: Automatically finds the best parent by analyzing Git history
- 🔍 **Dry-Run Mode**: Preview what will happen with `--dry-run`
- 🎛️ **Manual Override**: Explicitly set parent with `--parent` if needed

Options:
- `BRANCH`: Branch name to adopt (defaults to current branch)
- `-p, --parent TEXT`: Explicitly specify parent branch (overrides auto-detection)
- `-n, --dry-run`: Show what would be adopted without actually doing it

Example:
```bash
# Basic usage - automatically detects parent from Git history
uv run shortcake adopt feature-2
# Auto-detected parent: feature-1
# Adopted branch 'feature-2' with parent 'feature-1'

# Preview what will happen (dry-run)
uv run shortcake adopt feature-3 --dry-run
# Auto-detected parent: feature-2
# Would adopt branch 'feature-3' with parent 'feature-2'

# Explicitly specify parent (overrides auto-detection)
uv run shortcake adopt feature-2 --parent main

# Adopt current branch
uv run shortcake adopt
```

**How Auto-Detection Works:**

When you have a manual stack like:
```
main → feature-1 → feature-2 → feature-3
```

Shortcake analyzes your Git history to find which branch is the direct parent:
- `shortcake adopt feature-2` → detects `feature-1` as parent (1 commit away)
- `shortcake adopt feature-3` → detects `feature-2` as parent (1 commit away)

This works even if you adopt branches in any order!

## Development

This project uses uv for dependency management and requires Python 3.14 or higher.

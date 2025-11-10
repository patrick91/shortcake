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
- By default, emojis are removed from branch names
- Use `--keep-emoji` (or `-e`) to preserve emojis in branch names

**Note:** Future enhancement will include gitmoji integration for conventional emoji commits.

Options:
- `--keep-emoji` / `-e`: Keep emojis in the generated branch name

Example:
```bash
# Basic usage
uv run shortcake create
# You'll be prompted: Enter commit message: Add new feature
# Creates branch: add-new-feature
# Creates commit: Add new feature

# With emojis (removed from branch name by default)
uv run shortcake create
# You'll be prompted: Enter commit message: 🚀 Add rocket feature
# Creates branch: add-rocket-feature
# Creates commit: 🚀 Add rocket feature

# Keep emojis in branch name
uv run shortcake create --keep-emoji
# You'll be prompted: Enter commit message: 🔥 Add fire feature
# Creates branch: 🔥-add-fire-feature
# Creates commit: 🔥 Add fire feature
```

### `edit` / `modify`
Edit the current stack by amending the commit.

This command helps you modify the current stack by:
1. Staging all changes
2. Amending the previous commit without opening an editor

Example:
```bash
# Make some changes to your files
uv run shortcake edit
# or
uv run shortcake modify
```

## Development

This project uses uv for dependency management and requires Python 3.14 or higher.
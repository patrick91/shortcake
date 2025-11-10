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

## Development

This project uses uv for dependency management and requires Python 3.14 or higher.
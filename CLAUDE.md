# Shortcake

A stacked PR workflow tool using git trailers.

## Project Structure

```
src/shortcake/
├── __init__.py
├── cli.py              # Typer app, imports and registers commands
├── _git.py             # Git operations (dulwich) - internal
└── commands/
    ├── __init__.py
    └── adopt.py        # adopt command + _adopt() business logic

tests/
├── conftest.py         # Fixtures (temp repos)
├── test_adopt.py       # Tests for _adopt() business logic
├── test_cli.py         # CLI integration tests
└── test_git.py         # Tests for _git module
```

## Conventions

### Commands

Each `commands/*.py` file contains:
- `_function()` - Internal business logic (testable directly)
- `function()` - Typer command (thin wrapper)

Example:
```python
# commands/adopt.py

def _adopt(repo, branch, parent) -> AdoptResult:
    """Business logic - testable directly."""
    ...

def adopt(branch: ..., parent: ...) -> None:
    """Typer command - thin wrapper."""
    repo = git.open_repo()
    result = _adopt(repo, branch, parent)
    ...
```

### Modules

- `_module.py` - Internal modules (prefixed with underscore)
- Tests call `_function()` directly for unit tests, CLI for integration

### Testing

- 100% coverage required
- Use `dulwich` fixtures from `conftest.py` for git operations
- Test business logic via `_function()`, CLI via `CliRunner`

## Tech Stack

- **CLI**: Typer
- **Git**: dulwich (pure Python)
- **Testing**: pytest, pytest-cov
- **Python**: 3.14+

## Core Concept

Each tracked branch has a `Shortcake-Parent` trailer in its first commit:

```
feat: add login form

Shortcake-Parent: main
```

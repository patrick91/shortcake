# Shortcake Implementation Plan

A command-first, iterative approach to building a stacked PR workflow tool.

## Principles

1. **Command-first** - Implement commands one at a time, extract patterns when they emerge
2. **Test-driven** - Write tests alongside implementation
3. **No premature abstraction** - Add infrastructure only when needed
4. **Trailers are truth** - `Shortcake-Parent` trailer in first commit is the source of truth

## Tech Stack

- **CLI**: Typer
- **UI**: rich-toolkit (consistent CLI styling)
- **Git**: dulwich (pure Python, optional Rust extensions)
- **Testing**: pytest
- **Linting**: ruff

## Core Concept: Trailers

Each tracked branch has a `Shortcake-Parent` trailer in its first commit (relative to parent):

```
feat: add login form

Shortcake-Parent: main
```

This trailer defines the stack relationship. No external state files needed.

## Phases

1. [Foundation](./phase-1-foundation.md) - `adopt`, `ls`, `create`
2. [Navigation](./phase-2-navigation.md) - `up`, `down`, `top`, `bottom`
3. [Daily Workflow](./phase-3-workflow.md) - `commit`, `status`, `log`
4. [Stack Operations](./phase-4-stack-ops.md) - `restack`, `continue`, `abort`, `sync`
5. [GitHub Integration](./phase-5-github.md) - `submit`, `checkout`
6. [Advanced](./phase-6-advanced.md) - `delete`, `move`

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

### Convention

- `commands/*.py` - Each file contains:
  - `_function()` - Internal business logic (testable directly)
  - `function()` - Typer command (thin wrapper)
- `_module.py` - Internal modules (prefixed with underscore)
- Tests call `_function()` directly for unit tests, CLI for integration

## Notes

- No cache layer initially - read trailers directly
- Add caching only if performance becomes an issue
- Gitmoji picker deferred - use `-m` flag first

---

*Last updated: 2025-01-19*

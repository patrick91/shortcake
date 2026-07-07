# Shortcake

A stacked-PR workflow tool. Each branch records its parent in a git **trailer**, so the
whole stack is reconstructed from commits — no state files, no metadata branch, no daemon.
Ships a CLI (`sc` / `shortcake`) and a local web UI (`sc ui`).

## Project Structure

```
src/shortcake/
├── __init__.py
├── cli.py                  # Typer app — imports & registers every command
├── _git/                   # Git layer (pygit2 + a few `git` subprocess calls) — internal package
│   ├── __init__.py         #   re-exports everything; used as `from shortcake import _git as git`
│   ├── _core.py            #   repo / branch / commit / staging / hooks
│   ├── _rebase.py          #   rebase, cherry-pick, merge-base, conflict state
│   ├── _remote.py          #   fetch, remote refs
│   ├── _stack.py           #   stack ops: parent/children, tracked & merged branches
│   ├── _patch.py           #   patch extraction (split / move-lines)
│   └── _pygit2.py          #   low-level pygit2 helpers
├── _github.py              # GitHub API via httpx + `gh` for auth/token
├── _trailers.py            # Shortcake-Parent trailer parsing & writing
├── _restack_state.py       # persisted restack plan (conflict recovery for continue/abort)
├── _pr_stack.py            # stack → PR mapping
├── _tree.py                # stack tree rendering
├── _editor.py _gitmoji.py _cache.py _constants.py _exceptions.py   # internal helpers
├── _web/                   # React 19 + Vite + Tailwind v4 web UI, served by `sc ui`
└── commands/               # one module per command (+ a few `_helper.py` modules)
    ├── create.py adopt.py modify.py fold.py reorder.py move.py split.py
    ├── restack.py continue_.py abort.py
    ├── up.py down.py top.py bottom.py checkout.py ls.py log.py
    ├── submit.py sync.py pull.py review.py skill.py recap.py ui.py
    └── _review.py _suggest.py move_lines.py   # helpers (hunk-level split/move-lines engine; not registered directly)

tests/
├── conftest.py             # fixtures (temp repos)
├── _git_helpers.py         # pygit2-backed repo helpers used by fixtures/tests
├── test_*.py               # unit + integration tests (one area per file)
├── navigation/             # up / down / top / bottom tests
├── benchmarks/             # pytest-benchmark performance tests
└── e2e/                    # Playwright browser tests for the web UI (marker: e2e)

e2e/
├── docs/*.md               # executable markdown docs = CLI end-to-end tests
├── markdown_runner.py      # runs the `console` blocks in e2e/docs
└── github_mock.py          # mock GitHub server for the CLI e2e runs
```

## Commands

Registered in `cli.py`. `sc` and `shortcake` are both entry points.

- **Build the stack** — `create` (new tracked branch; `--before`/`--after` to insert),
  `adopt` (track an existing branch)
- **Edit the stack** — `modify`, `fold`, `reorder`, `move`, `split` (move files
  into a new stacked branch)
- **Restack** — `restack` (rebase children when a parent changes), `continue`, `abort`
  (resume/abandon after a conflict)
- **Navigate / inspect** — `up`, `down`, `top`, `bottom`, `checkout` (alias `co`), `ls`, `log`
- **GitHub / remote** — `submit` (open/update stacked PRs), `sync`, `pull`, `review`
- **Web UI** — `ui`

## Conventions

### Commands

Each `commands/*.py` file contains:
- `_function()` — internal business logic (testable directly); may be split into several
  `_`-prefixed helpers
- `function()` — the Typer command (thin wrapper): opens the repo, calls the business
  logic, prints via `typer.echo`, maps errors to `typer.Exit`

```python
# commands/adopt.py
def _adopt(repo: Repo, branch: str, parent: str) -> AdoptResult:
    """Business logic — testable directly."""
    ...

def adopt(branch: ..., parent: ...) -> None:
    """Typer command — thin wrapper."""
    repo = git.open_repo()
    result = _adopt(repo, branch, parent)
    ...
```

**When adding a command:** create `commands/<name>.py` with the `_fn` + `fn` pair, register
it in `cli.py`, add unit/integration tests, and add an `e2e/docs/XX-<name>.md` doc (below).

### Modules

- `_module.py` / `_package/` — internal (underscore-prefixed)
- `_git` is a package but re-exports a flat API; always use it as `from shortcake import _git as git`
- Tests call `_function()` directly for unit tests, and the CLI for integration

### Testing

- **100% coverage required** (CI runs `coverage report --fail-under=100`)
- Use the repo fixtures from `conftest.py` (helpers in `tests/_git_helpers.py`, pygit2-backed)
- Test business logic via `_function()`, CLI via Typer's `CliRunner`
- `inline-snapshot` for snapshot assertions; `respx` to mock GitHub/httpx; `pytest-benchmark`
  for `tests/benchmarks/`
- Use plain functions for tests, not test classes

### E2E tests

Two suites:

1. **CLI e2e** live in `e2e/docs/*.md` as executable markdown documentation. Use ````console`
   blocks for commands and expected output; run with `uv run python e2e/markdown_runner.py`.
2. **Browser e2e** for the web UI live in `tests/e2e/` (Playwright, `e2e` marker); run with
   `uv run pytest tests/e2e/`.

Example CLI e2e doc:

```markdown
# Command Name

Description of the command.

## Setup

\`\`\`console
$ echo "setup" > file.txt && git add file.txt
$ sc create -m "Setup branch"
Created branch 'setup-branch' from 'main'
\`\`\`

## Basic Usage

\`\`\`console
$ sc command
Expected output here
\`\`\`
```

## Dev Commands

```bash
# Tests + coverage (must stay at 100%)
uv run pytest tests/ -v --cov=src/shortcake --cov-report=term
uv run coverage report --fail-under=100

# Browser e2e (one-time: uv run playwright install --with-deps chromium)
uv run pytest tests/e2e/ -v

# CLI e2e (markdown docs)
uv run python e2e/markdown_runner.py

# Lint & format
uv run --group linting ruff check src/ tests/
uv run --group linting ruff format --check src/ tests/

# Type check
uv run --group typing ty check src/
```

## Tech Stack

- **CLI / output**: Typer, rich-toolkit
- **Git**: pygit2 (libgit2 bindings), plus a few `git` subprocess calls (e.g. rebase, hooks);
  `dulwich[merge]` is still a declared dependency but legacy and being phased out
- **GitHub**: httpx for the API, the `gh` CLI for auth/token
- **Data**: pydantic, pyyaml
- **Web UI** (`_web/`, `sc ui`): React 19, Vite, Tailwind v4 (TypeScript)
- **Tooling**: uv (build backend + workspace), ruff (lint/format), ty (type checker)
- **Testing**: pytest, pytest-cov, inline-snapshot, respx, pytest-benchmark, pytest-playwright
- **Python**: 3.14+

## Core Concept

Each tracked branch stores its parent in a `Shortcake-Parent` trailer in its first commit:

```
feat: add login form

Shortcake-Parent: main
```

The stack is rebuilt entirely from these trailers — `ls`/`log` walk parent→children, and
`restack` rebases descendants whenever a parent moves. Because the relationship lives in the
commit, the stack survives rebasing, pushing, and branching with any git tooling.
```

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/patrick91/shortcake/main/website/static/logo-dark.svg">
  <img src="https://raw.githubusercontent.com/patrick91/shortcake/main/website/static/logo.svg" alt="Shortcake" width="320">
</picture>

### Turn big changes into pull requests reviewers can follow.

Shortcake helps you build a feature as a stack of small, review-sized branches,
see the order at a glance, and submit matching GitHub PRs from your terminal.

<a href="https://github.com/patrick91/shortcake"><img alt="Python 3.14+" src="https://img.shields.io/badge/python-3.14%2B-3776AB?logo=python&logoColor=white"></a>
<a href="#license"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-F02D57"></a>
<a href="https://github.com/sponsors/patrick91"><img alt="Sponsor" src="https://img.shields.io/badge/sponsor-%E2%9D%A4-F02D57?logo=githubsponsors&logoColor=white"></a>

</div>

---

The whole stack is reconstructed from your commits. Each branch records its parent in a
git **trailer** — no state files, no metadata branch, no daemon. Rebase, push, checkout, and
branch with the tools you already use; the stack travels with your Git history.

```
feat: add login form

Shortcake-Parent: main
```

That one marker is why Shortcake can recover the order after rebases, checkouts, and pushes
without a separate state file to keep in sync.

## Why stacked PRs?

Big pull requests are hard to review. Stacked branches let you send a large change in the
order it should be *read* — foundation first, follow-ups after — so each PR stays focused on
one idea instead of the whole feature. Shortcake keeps that order visible and submits PRs with
bases that match it, so reviewers always know what builds on what.

## Install

```bash
uv tool install shortcake
```

Requires **Python 3.14+**. The install exposes both `shortcake` and the short alias `sc`.

<details>
<summary>Other ways to install</summary>

```bash
# pipx
pipx install shortcake
```

</details>

## Quick start

Build a two-branch stack, look at it, and open the PRs — start to finish.

**1. Create a branch from your staged changes.** `sc create` commits what's staged and records
`main` as the parent.

```console
$ echo "def login(): ..." > login.py && git add login.py
$ sc create -m "Add login form"
Created branch 'add-login-form' from 'main'
```

**2. Stack the next change on top.** Run `create` again — the new branch's parent is the one
you're on.

```console
$ echo "def reset(): ..." > reset.py && git add reset.py
$ sc create -m "Add password reset"
Created branch 'add-password-reset' from 'add-login-form'
```

**3. See the stack** with `sc ls`:

```console
$ sc ls
◉ add-password-reset (current)
│
◯ add-login-form
│
◯ main
```

**4. Submit through the current diff** to GitHub. `sc submit` pushes the current branch and
its downstack ancestors, opening or updating their PRs in dependency order. Use
`sc submit --stack` to include upstack branches too. In an interactive terminal, `sc submit`
offers to expand the submission to the whole stack when there are branches above the current
one. Add `--draft`/`-d` for drafts, `--dry-run`/`-n` to preview first, or `--stealth` to push
without creating or updating PRs.

```console
$ sc submit --stack
Submit plan:

  ◯ main (base)
  │
  ● add-login-form — create PR
  │
  ◉ add-password-reset (current) — create PR

● 2 selected

Pushing 'add-login-form'...
  Creating PR for 'add-login-form'...
  Created PR #1: https://github.com/you/repo/pull/1
Pushing 'add-password-reset'...
  Creating PR for 'add-password-reset'...
  Created PR #2: https://github.com/you/repo/pull/2

Created 2 PR(s)
```

For a linear stack, Shortcake registers the PR sequence with GitHub's native
stack API, so GitHub renders the stack header and enables stack-wide merge and
rebase actions. `sc ls` shows the PR numbers and native positions:

```console
$ sc ls --refresh
◉ add-password-reset #2 stack #1 2/2 (current)
│
◯ add-login-form #1 stack #1 1/2
│
◯ main
```

> `sc submit` normally needs a GitHub token (from `gh auth login`, or `GH_TOKEN`/`GITHUB_TOKEN`)
> and an `origin` remote pointing at GitHub. `sc submit --stealth` only pushes branches, so it
> does not need the GitHub API token. Submit restacks branches before pushing and uses
> `--force-with-lease` so it won't clobber others' work. GitHub native stacks require a
> linear sequence of branches in one repository. Non-linear Shortcake trees,
> repositories where stacked pull requests are not available yet, and stacks outside
> GitHub's limits keep the managed PR-body map as a compatibility fallback.

That's the core loop: **`create` → `ls` → `submit`**. The rest of the CLI is there when you
need to move, split, restack, or repair branches.

## How it works

The stack is rebuilt entirely from the `Shortcake-Parent` trailer in each branch's first
commit. `ls`/`log` walk parent → children, and `restack` rebases descendants whenever a parent
moves. Because the relationship lives in the commit, the stack survives rebasing, pushing, and
branching with any Git tooling — there's nothing else to keep in sync.

Already started a branch the normal way? `sc adopt` brings an existing branch into a stack
instead of recreating it.

The commit trailers remain Shortcake's local source of truth. On submit,
GitHub's native stack resource is the remote representation: Shortcake creates
it, appends new top PRs, and safely recreates it for whole-stack
reorders/removals. A scoped submit will not dismantle a stack containing
unselected PRs. Migrating an existing PR-body stack is atomic too: when a
scoped submit leaves open legacy layers out, Shortcake keeps the body map and
points to `sc submit --stack` to migrate the whole stack.

To bring a stack created on GitHub onto a new machine, run `sc checkout <PR>`.
When that PR belongs to a native stack, Shortcake fetches every open branch,
preserves correctly tracked local work, and writes the matching trailers before
checking out the requested branch. It will not overwrite a divergent branch
that needs re-parenting. If GitHub rebases a stack server-side, run
`sc pull` before continuing local work; remote branch refs win by default.

## Commands

| | |
| --- | --- |
| **Build the stack** | `create` (new tracked branch; `--before`/`--after` to insert) · `adopt` (track an existing branch) |
| **Edit the stack** | `modify` · `fold` · `reorder` · `move` · `split` (move files into a new stacked branch) |
| **Restack** | `restack` (rebase children when a parent changes) · `continue` · `abort` (resume/abandon after a conflict) |
| **Navigate & inspect** | `up` · `down` · `top` · `bottom` · `checkout` (alias `co`) · `ls` · `log` |
| **GitHub & remote** | `submit` (open/update and register native stacks) · `sync` · `pull` · `review` |
| **Web UI** | `ui` |

Run `sc <command> --help` for the options on any command.

## Review the stack in your browser

```bash
sc ui
```

Opens a local, GitHub-style view of every branch diff — file tree, inline comments, and AI
review — reading straight from your repo. Nothing leaves your machine.

You can also review a branch from the terminal with `sc review`, which runs the change through
an installed AI CLI (`claude` or `codex`).

## Working with coding agents

Shortcake is built to be driven by agents as well as humans: every core command takes
`--json` (exactly one JSON document on stdout — `{"data": ...}` or
`{"error": {"code", "message", "hint"}}`), `sc sync` never blocks on prompts in
non-interactive shells, and pre-commit formatter failures self-heal.

Shortcake ships a workflow skill that teaches an agent the stacked-PR model. Install it for
Claude Code with:

```bash
mkdir -p .claude/skills/shortcake-stacked-prs
sc skill --print shortcake-stacked-prs > .claude/skills/shortcake-stacked-prs/SKILL.md
```

Or paste this into your project's `CLAUDE.md` / agent instructions:

```markdown
This repo uses shortcake (`sc`) for stacked PRs. Read the workflow first:
run `sc skill --print shortcake-stacked-prs`. Key points: use `sc ls --json`
to read the stack, `sc create -m` / `sc modify` / `sc split` to shape it,
`sc restack` + `sc continue` for rebases, and `sc submit` for PRs. Pass
`--json` to any of these for machine-readable output.
```

## Development

```bash
# Tests + coverage (must stay at 100%)
uv run pytest tests/ -v --cov=src/shortcake --cov-report=term
uv run coverage report --fail-under=100

# CLI end-to-end tests (executable markdown docs in e2e/docs/)
uv run python e2e/markdown_runner.py

# Browser e2e for the web UI (one-time: uv run playwright install --with-deps chromium)
uv run pytest tests/e2e/ -v

# Lint, format, and type-check
uv run --group linting ruff check src/ tests/
uv run --group linting ruff format --check src/ tests/
uv run --group typing ty check src/
```

See [`CLAUDE.md`](https://github.com/patrick91/shortcake/blob/main/CLAUDE.md) for the project layout and conventions.

## License

[MIT](https://github.com/patrick91/shortcake/blob/main/LICENSE) © [Patrick Arminio](https://github.com/patrick91). If Shortcake is useful to
you, consider [sponsoring](https://github.com/sponsors/patrick91). 🍰
</content>
</invoke>

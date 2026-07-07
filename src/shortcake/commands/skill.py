from __future__ import annotations

from typing import Annotated

import typer

SHORTCAKE_VISUAL_RECAP_SKILL = """---
name: shortcake-visual-recap
description: Author a local Shortcake visual recap as restricted MDX.
---

# Shortcake Visual Recap

Use this when the user asks for a local visual recap of a branch or working diff.
Shortcake renders and validates the recap locally; the agent writes restricted MDX.

## Flow

1. Run `shortcake recap context [BASE] --json` to recap the current branch
   against any git base revision such as `main`, `origin/main`, or `HEAD~3`.
   When no base is passed, Shortcake uses tracked parent metadata if available
   and otherwise falls back to the repository default branch.
   Use `shortcake recap context --working --json` for uncommitted changes.
   Add `--no-patch` when you only need `files`/`source`/`template` — the raw
   patch can be hundreds of KB.
2. Read the returned `patch`, `files`, `source`, and `template`.
3. Inspect the real changed files as needed. Do not infer behavior from filenames alone.
4. Write an MDX file that keeps the frontmatter exactly aligned with `source`.
5. Use only supported Markdown and Shortcake recap components:
   `FileMap`, `Diff`, `DiffTabs`, `Mermaid`, `DataModel`, `Endpoint`, and
   `StateSummary`.
6. Validate it with `shortcake recap validate --mdx @recap.mdx`.
7. Store it with `shortcake recap create --mdx @recap.mdx`.
8. Open it with `shortcake recap open <id> --background` so the local UI server
   does not block the agent shell. Reuse the printed URL in the response.

## MDX Rules

- Do not use `import`, `export`, arbitrary JSX, JS expressions, or event props.
- Keep structured payloads as JSON strings or fenced JSON blocks.
- Attributes are JSX-style quoted strings. Use double quotes for plain prose,
  single quotes for JSON payloads, and avoid backslash-escaped inner quotes.
- Point `Diff` and `DiffTabs` paths at files from the stored patch.
- Mermaid diagrams can be written as fenced ` ```mermaid ` Markdown blocks or
  with the `<Mermaid>` component.
  Quote edge labels that contain punctuation or directives, for example
  `A -->|"@defer / @stream"| B`.
- Add inline annotations for important line-level behavior, risks, or review
  stops. Use `annotations='[...]'` with JSON objects containing `line` or
  `startLine`/`endLine`, `side`, `title`, `text`, and optional `severity`.
  Multi-line JSON attributes are supported.
  Use `side: "right"` or `side: "additions"` for new-file lines and
  `side: "left"` or `side: "deletions"` for old-file lines.
  Use these sparingly and only after inspecting the real changed file.
- Every `## Validation` section must start with a short prose summary of what
  passed, failed, was manually checked, or was not run. Do not leave it as a bare
  command list.
- After that summary, list the concrete commands or manual checks that back it
  up only when there are multiple items. Use prose for one validation item.
"""


SHORTCAKE_STACKED_PRS_SKILL = """---
name: shortcake-stacked-prs
description: Drive stacked PRs with the sc CLI (create, modify, restack, submit).
---

# Shortcake Stacked PRs

Shortcake (`sc`) manages stacked branches/PRs. Each tracked branch records its
parent in a `Shortcake-Parent` trailer in its first commit — the whole stack is
rebuilt from commits, so it survives any git tooling. A branch without that
trailer is "untracked" and most sc commands refuse to touch it.

## Read the stack

- `sc ls --json` — the stack as data: each branch has `name`, `parent`,
  `current`, `pr`, `needs_restack`. Prefer this over parsing the tree glyphs.
- Mutating commands (`create`, `modify`, `restack`, `continue`, `split`) also
  take `--json`: stdout is exactly one JSON document — `{"data": ...}` on
  success, `{"error": {"code", "message", "hint"}}` on failure. A conflicted
  restack/continue exits 1 with `data.conflict = {branch, files, resolve}`.
- `sc ls` — human tree. `◉` marks the current branch; `⟳ needs restack` marks
  branches whose parent moved (amend/advance) — run `sc restack` before
  shipping from them.
- `sc log --json` — commits on the current branch vs its parent.

## Core loop

1. Stage exactly the files for one logical change: `git add <files>`.
2. `sc create -m "Message"` — commits and creates a tracked child branch of the
   current branch. Pass a positional name to override the generated one:
   `sc create my-branch-name -m "Message"`. Use `--before`/`--after` to insert
   into the middle of a stack.
3. Edit a branch: stage changes, then `sc modify` — it amends the current
   branch's commit AND knows the stack. Prefer it over
   `git commit --amend` + `sc restack` by hand. `sc modify -t <branch>` folds
   staged changes into another branch's commit.
   Too much in one branch? `sc split <file>... -m "Message"` moves whole
   files into a new branch below the current one (`--after` for above) and
   verifies no content is lost — no manual patch surgery needed.
4. `sc restack` — rebases all descendants after a parent changed.
5. `sc submit --dry-run` to preview, then `sc submit` — pushes the stack and
   creates/updates one PR per branch with correct bases.

Pre-commit hooks: sc runs them itself and self-heals the "formatter rewrote
files, exit 1" pattern by re-staging and re-running once. Do not run commands
twice to work around hook failures; a second failure is a real error.

## Conflicts

`sc restack` / `sc sync` stop on conflicts and print the files. Resolve them,
`git add <files>`, then `sc continue` (it cascades through the remaining
branches). `sc abort` rolls back. Never run `git rebase --continue` yourself —
sc owns the rebase state.

## Sync with remote

`sc sync` pulls trunk, deletes merged branches, reparents and restacks. In
non-interactive shells it never prompts: merged branches are kept and a hint
suggests `--yes`. Run `sc sync --yes` to delete merged branches. After the
release bot or teammates push to trunk, re-fetch before cutting new branches.

## Navigation

`sc up` / `sc down` (child/parent), `sc top` / `sc bottom`, `sc co <branch>`
(also `sc co <pr-number>`).

## Untracked branches

"Branch 'X' is not tracked by Shortcake" → track it: `sc adopt X -p <parent>`
(add `-f` to re-parent an already-tracked branch). Repos that forbid commits
on trunk: create the branch with git first, commit there, then `sc adopt`.

## PR bodies

`sc submit` maintains a stack overview between `<!-- shortcake:start -->` and
`<!-- shortcake:end -->` markers in each PR body. When editing bodies via
`gh pr edit`, preserve those markers.
"""

_SKILLS = {
    "shortcake-visual-recap": SHORTCAKE_VISUAL_RECAP_SKILL,
    "shortcake-stacked-prs": SHORTCAKE_STACKED_PRS_SKILL,
}


def skill(
    print_skill: Annotated[
        str | None,
        typer.Option("--print", help="Print a bundled Shortcake skill by name."),
    ] = None,
) -> None:
    """Print bundled local agent skills."""
    if print_skill is None:
        typer.echo(f"Available skills: {', '.join(sorted(_SKILLS))}")
        return

    if print_skill in _SKILLS:
        typer.echo(_SKILLS[print_skill])
        return

    typer.echo(f"Error: Unknown skill '{print_skill}'", err=True)
    raise typer.Exit(1)

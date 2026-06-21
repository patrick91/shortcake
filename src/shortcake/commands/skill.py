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


def skill(
    print_skill: Annotated[
        str | None,
        typer.Option("--print", help="Print a bundled Shortcake skill by name."),
    ] = None,
) -> None:
    """Print bundled local agent skills."""
    if print_skill == "shortcake-visual-recap":
        typer.echo(SHORTCAKE_VISUAL_RECAP_SKILL)
        return

    if print_skill is None:
        typer.echo("Available skills: shortcake-visual-recap")
        return

    typer.echo(f"Error: Unknown skill '{print_skill}'", err=True)
    raise typer.Exit(1)

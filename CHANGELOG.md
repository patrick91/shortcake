CHANGELOG
=========

1.2.1 - 2026-06-23
------------------

This release polishes the "Switch Diff" branch menu in `sc ui`.

Branch names now show in full instead of being truncated — long names wrap onto
a second line — and the menu uses a wider popover with more breathing room
between rows. The stack connector guides line up with the first line of each
branch name, so they stay aligned when a name wraps. The hover and
keyboard-navigation highlight is now a clean background fill instead of a boxed
accent ring.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#124](https://github.com/patrick91/shortcake/pull/124)

1.2.0 - 2026-06-21
------------------

This release adds local visual recaps for Shortcake diffs.

Agents can now run `sc recap context [BASE] --json` to capture the branch or
working-tree patch, write a restricted MDX recap, store it with
`sc recap create --mdx @recap.mdx`, and open it with `sc recap open <id>`.
Recaps are stored privately under `.git/shortcake/recaps` with the source
metadata, patch, and MDX needed to render them later.

```bash
sc recap context main --json > context.json
sc recap create --mdx @recap.mdx
sc recap open <id>
```

The local UI now renders recap documents with supported blocks such as
`FileMap`, `Diff`, `DiffTabs`, `Mermaid`, `DataModel`, `Endpoint`, and
`StateSummary`. `sc skill --print shortcake-visual-recap` prints the bundled
agent instructions for authoring compatible recap MDX.

`sc ui` and `sc recap open` now serve built UI assets and the API from one
configurable Shortcake UI server by default, using `SHORTCAKE_UI_PORT`,
`git config shortcake.uiPort`, or port `8765`. Vite is only used with `--dev`,
and its default port is `6173` or `SHORTCAKE_UI_DEV_PORT` /
`git config shortcake.uiDevPort`.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#123](https://github.com/patrick91/shortcake/pull/123)

1.1.0 - 2026-06-21
------------------

This release adds persistent review state to `sc ui`.

The review UI now remembers which files you marked as Viewed and whether you
prefer the unified or split diff layout across reloads. Viewed files are matched
to the current patch for each file, so Shortcake shows a file as unviewed again
when its diff changes instead of hiding fresh changes behind an old Viewed mark.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#122](https://github.com/patrick91/shortcake/pull/122)

1.0.3 - 2026-06-19
------------------

**`sc ui`**: the "Large file" diff placeholder (the "Show changes" prompt for
big files) used fixed dark-theme yellows, so in light mode the pale text and
button sat on a pale tint with almost no contrast. The diff pane now uses
theme-aware `warning` color tokens that adapt to both light and dark themes,
so the placeholder and its button stay legible either way.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#121](https://github.com/patrick91/shortcake/pull/121)

1.0.2 - 2026-06-18
------------------

**`sc ui`**: the diff pane now lists files in the same order as the sidebar
file tree (folders first, then files, natural sort) instead of raw git-diff
order, so the two stay in sync as you scroll. The ordering reuses the file
tree's own sort, so it's guaranteed to match.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#119](https://github.com/patrick91/shortcake/pull/119)

1.0.1 - 2026-06-18
------------------

Two small fixes:

- **README on PyPI**: the logo and doc links used repo-relative paths, which
  PyPI doesn't resolve, so the logo showed as a broken image. They now use
  absolute URLs.
- **`sc submit`**: the stack section added to each PR description now links back
  to [shortcake](https://shortcake.patrick.wtf) with a 🍰 on its heading.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#118](https://github.com/patrick91/shortcake/pull/118)

1.0.0 - 2026-06-18
------------------

Initial release of Shortcake! 🍰

This release was contributed by [@patrick91](https://github.com/patrick91) in [#117](https://github.com/patrick91/shortcake/pull/117)
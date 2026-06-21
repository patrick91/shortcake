---
release type: minor
---

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

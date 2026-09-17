# Shortcake Stack Diff UI

This Vite + React app renders each tracked branch diff in a GitHub-like view using
[`@pierre/diffs`](https://diffs.com/).

## Run from the CLI

From any git repo managed by Shortcake:

```bash
sc ui
```

By default, `sc ui` serves the built `dist/` assets and the API from the same
local server. It does not start Vite or require Bun unless the built assets are
missing.

GitHub PR and CI information refreshes every five minutes while the tab is visible.
All tabs connected to the same UI server share a five-minute cache; newly tracked
branches appear in GitHub results on the next refresh. If GitHub rate-limits a
request, the server keeps the previous results and waits for the reset or
`Retry-After` time before trying again. Repeated failures increase the retry delay.

Useful flags:

```bash
sc ui --skip-install
sc ui --host 127.0.0.1 --ui-port 8765
sc ui --build-ui
sc ui --dev --web-port 6173
```

The UI port can also be configured with `SHORTCAKE_UI_PORT` or git config
`shortcake.uiPort`.

## Run frontend manually

```bash
cd web
bun install
bun run dev
```

The frontend expects the backend API at `/api` and uses Vite proxy forwarding to
`SHORTCAKE_API_ORIGIN` (default: `http://127.0.0.1:8765`).

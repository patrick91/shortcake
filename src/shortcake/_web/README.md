# Shortcake Stack Diff UI

This Vite + React app renders each tracked branch diff in a GitHub-like view using
[`@pierre/diffs`](https://diffs.com/).

## Run from the CLI

From any git repo managed by Shortcake:

```bash
sc ui
```

Useful flags:

```bash
sc ui --skip-install
sc ui --host 127.0.0.1 --api-port 8765 --web-port 5173
```

## Run frontend manually

```bash
cd web
bun install
bun run dev
```

The frontend expects the backend API at `/api` and uses Vite proxy forwarding to
`SHORTCAKE_API_ORIGIN` (default: `http://127.0.0.1:8765`).

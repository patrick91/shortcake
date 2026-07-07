Release type: minor

Make shortcake agent-friendly — every fix rooted in transcripts of real agent
sessions driving `sc` (tracked in #127):

- Add `--json` to `ls`, `log`, `create`, `modify`, `restack`, `continue`,
  `split`, and `submit`: stdout is exactly one JSON document — `{"data": ...}`
  on success, `{"error": {"code", "message", "hint"}}` on failure. Conflicted
  restacks report `{branch, files, resolve}` structurally. Built on
  rich-toolkit's JSON mode.
- Add `sc submit --stealth` to push the stack without creating or updating PRs.
- Add `sc split <file>... -m "msg"`: move whole files out of the current branch
  into a new stacked branch (`--after` for on top), with an integrity check
  that no content is lost.
- Pre-commit formatter failures now self-heal: when hooks rewrite staged files
  and exit non-zero, sc re-stages and re-runs once instead of failing.
- `sc sync` never blocks on prompts in non-interactive shells; merged branches
  are kept with a hint to use `--yes`.
- `sc ls` marks stale branches with `⟳ needs restack`; `sc create` accepts a
  positional branch name; "not tracked" errors suggest the exact `sc adopt`
  command to run.
- Fix `sc move` leaving grandchildren on stale bases (the whole subtree is now
  restacked) and fix repo detection when `url.<base>.insteadOf` rewrites point
  the origin transport elsewhere.
- Slim `recap create`/`validate` output (full payload behind `--verbose`) and
  add `recap context --no-patch`.
- Bundle a stacked-PR workflow skill for coding agents:
  `sc skill --print shortcake-stacked-prs`.
- Repair the CLI e2e docs suite (date-independent branch names via
  `SHORTCAKE_NO_DATE_PREFIX`, regenerated docs) and run it in CI.

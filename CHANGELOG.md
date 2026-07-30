CHANGELOG
=========

1.5.2 - 2026-07-30
------------------

Prune deleted remote branches when fetching. A plain fetch leaves
`refs/remotes/origin/<branch>` behind after the branch is deleted upstream, so
the stale ref lingers indefinitely and anything reading it believes the branch
still exists on the remote. That affects merged-branch detection in `sc sync`
and remote lookups in `sc checkout`.

This was invisible for anyone with `fetch.prune = true` in their git config;
shortcake no longer depends on that setting.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#135](https://github.com/patrick91/shortcake/pull/135)

1.5.1 - 2026-07-29
------------------

Fix the status column not tracking the highlighted option in the `sc submit`
scope menu: a branch moving into scope kept reading "not submitted", and one
leaving it kept promising "create PR".

The scope menu also no longer waits on GitHub before drawing. It looks up one
PR per branch, which left the terminal blank for seconds on a large stack; the
stack now appears immediately with each row marked while its own lookup is in
flight.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#134](https://github.com/patrick91/shortcake/pull/134)

1.5.0 - 2026-07-29
------------------

Redesign `sc submit` output around a single live stack tree. The plan tree is
now the progress display: rows keep their place while their markers and status
column fill in, instead of the plan being printed and then replaced by three
flat lines per branch. The header states the target repo and whether PRs are
drafts, PR numbers are hyperlinked, and the footer links the top of the stack —
or lists each tip when the stack forks. Failed branches keep their position so
you can see where a stack broke.

`sc submit` now asks what to submit with a menu whose tree previews the
highlighted choice, and it offers a real Cancel — answering "no" to the old
prompt still submitted the downstack. `--stack` on a forked stack asks before
sweeping in a sibling arm, offering to submit just your own arm instead.

`sc restack` renders through the same view. Output adapts to the terminal: the
tree drops spacing before it hides any branch, and neither the progress tree
nor the menu can be cropped. Piped output streams row by row, and `--json` is
unchanged.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#133](https://github.com/patrick91/shortcake/pull/133)

1.4.0 - 2026-07-27
------------------

Redesign the diff switcher in `sc ui`. Stacks now render like `sc ls`: a
commit node per branch on a straight vertical rail, with a filled node on the
checked-out branch, instead of stair-step indentation. The list is anchored by
a trunk row, and independent stacks are grouped with separators — the active
stack first, the rest by most recent commit, stale roots dated.

The switcher also opens with the keyboard highlight on the branch being
viewed, filters with multi-word queries across branch name, commit subject,
and PR number (Esc clears the query first, closes second), dims shared date
prefixes in branch names, shows per-branch commit counts, marks merged and
draft PRs in the PR pill, and shows working-tree stats on the Working Changes
row.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#131](https://github.com/patrick91/shortcake/pull/131)

1.3.2 - 2026-07-22
------------------

Make `sc submit` push and create or update PRs from the bottom of the stack
through the current diff, so every PR base exists on GitHub. Use
`sc submit --stack` to include upstack branches; interactive runs also offer
to expand a downstack submission to the full stack. Submit now prints a
downward stack graph with each live PR action before prompting or acting, and
dims branches that are not selected. Existing PR numbers use the same cyan,
underlined, clickable links as `sc ls`.
PR stack descriptions omit branches excluded from that submission instead of
listing them as `(no PR)`.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#130](https://github.com/patrick91/shortcake/pull/130)

1.3.1 - 2026-07-07
------------------

This relase fix sc adopt and sc sync issues.

- Fix `sc adopt` rewriting a branch's entire history (thousands of commits)
  when the new parent's head is not an ancestor of the branch — the walk now
  stops at the merge base, and a tracked branch is always detected as tracked
  regardless of where its trailer sits in the range.
- `sc sync` no longer deletes a merged branch when its children cannot be
  reparented (which orphaned the stack); it keeps the branch and explains how
  to resolve.
- `sc restack` on a branch whose parent was deleted now says so and suggests
  `sc adopt -f -p <new-parent>` instead of reporting "Everything up to date."

This release was contributed by [@patrick91](https://github.com/patrick91) in [#129](https://github.com/patrick91/shortcake/pull/129)

1.3.0 - 2026-07-07
------------------

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

This release was contributed by [@patrick91](https://github.com/patrick91) in [#128](https://github.com/patrick91/shortcake/pull/128)

1.2.3 - 2026-07-02
------------------

- Add worktree-aware `sc checkout` and `sc sync` behavior, including directing
  checkout to existing branch worktrees and cleaning up removable worktrees during
  sync.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#126](https://github.com/patrick91/shortcake/pull/126)

Additional contributors: [@Copilot](https://github.com/Copilot)

1.2.2 - 2026-06-23
------------------

This release fixes the inline review comments in `sc ui` visual recaps showing a
washed-out light background while the rest of the UI was in dark mode.

The recap comment callouts now follow the active theme: a subtle rose-tinted
panel in dark mode, and their original light styling in light mode. The comment
title also gets a touch more contrast than its body so it reads as a heading.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#125](https://github.com/patrick91/shortcake/pull/125)

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
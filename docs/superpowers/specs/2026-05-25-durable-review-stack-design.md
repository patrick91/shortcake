# Durable Review Stack Design

## Context

Large diffs are hard to review when unrelated changes are mixed into one
branch. Shortcake already has the right primitives to help:

- A stack model based on `Shortcake-Parent` trailers.
- A diff UI with file navigation, hunk selection, line selection, and AI review.
- Existing operations that move, accept, and split selected hunks/lines into new
  branches while restacking descendants.

The feature should build on those primitives and make a large branch reviewable
as a sequence of logical commits or branches. The proposed review structure must
survive closing the UI, restarting Shortcake, and normal Git edits where
possible.

Related tools found during research:

- Branchlet and Diffo position AI-generated stacked changes as an easier way to
  review large work.
- Prume focuses on turning an uncommitted diff into atomic commits.
- GitHub has added commit-by-commit review support in its newer pull request
  files experience.
- Existing Git MCP servers expose general Git operations, but no researched MCP
  tool appears to provide this exact durable "split a big diff into a review
  walkthrough" workflow.

## Goals

- Let a user generate a proposed commit-by-commit walkthrough from one large
  branch diff.
- Persist the generated walkthrough in Git history using trailers, not only in a
  local UI state file.
- Let Shortcake reconstruct active review stacks after the UI closes or local
  state is deleted.
- Detect when the source branch changes after a review stack was generated.
- Keep the source branch intact until the user explicitly finalizes changes.
- Reuse existing hunk/line splitting, branch creation, trailer, and restack
  logic where practical.

## Non-Goals

- Do not replace the current manual split/move tools.
- Do not require GitHub or an online service to use the feature.
- Do not silently rewrite the user's original source branch.
- Do not make generated review branches part of the user's publishable stack
  without an explicit finalize action.
- Do not solve semantic merge conflicts automatically. Report them clearly and
  leave the user in Shortcake's existing recovery path.

## Core Model

The source branch remains unchanged. Shortcake creates a separate draft review
stack representing the proposed logical walkthrough. Each review branch contains
one generated review commit for one logical group. Each generated commit includes
both normal Shortcake stack metadata and review-specific metadata.

Example trailers:

```text
Shortcake-Parent: review/add-auth-001
Shortcake-Review: 20260525-143012-feature-login
Shortcake-Review-Source: feature-login@abc1234
Shortcake-Review-Group: 2/5
Shortcake-Review-State: draft
```

`Shortcake-Parent` remains the canonical stack relationship. Review trailers are
metadata that identify and recover the generated review session.

## Trailer Schema

`Shortcake-Review`
: Stable review session identifier. It should be unique within the repository
  and deterministic enough to display, but not dependent on branch names staying
  unchanged. A timestamp plus source branch slug is acceptable.

`Shortcake-Review-Source`
: Source branch and source commit SHA at generation time, formatted as
  `<branch>@<sha>`. This is used to detect stale review stacks.

`Shortcake-Review-Group`
: One-based group position and total count, formatted as `<index>/<total>`.
  This lets Shortcake reconstruct ordering even when branch names are edited.

`Shortcake-Review-State`
: Review lifecycle state. Initial supported values are `draft`, `finalized`,
  and `discarded`. `draft` is active and visible in the review workflow.

The existing trailer parser should be expanded to preserve unknown trailers and
parse Shortcake-owned review trailers without stripping non-Shortcake trailers
from commit messages.

## Branch and Commit Layout

Generated branches should use a namespaced prefix so they are easy to recognize
and ignore in normal stack operations unless review mode is active:

```text
review/<source-slug>/<group-index>-<message-slug>
```

Example:

```text
review/feature-login/01-add-login-model
review/feature-login/02-wire-login-api
review/feature-login/03-add-ui-state
```

The first review branch's `Shortcake-Parent` points to the source branch's
parent. Each subsequent review branch points to the previous review branch. This
forms a normal Shortcake-compatible stack while keeping it separate from the
source branch.

## User Flow

1. User opens a large branch in the UI or runs a CLI command.
2. User chooses "Split for review".
3. Shortcake builds the source patch using the existing parent-vs-branch diff.
4. An AI grouping pass proposes logical groups:
   - commit message
   - summary/rationale
   - selected hunks
   - confidence and warnings for ambiguous hunks
5. The user reviews the proposed groups before applying them.
6. Shortcake creates the draft review stack with trailers.
7. The UI switches into a walkthrough mode with one generated group at a time.
8. The user can review group diffs, mark groups viewed, edit messages, regroup
   selected hunks, refresh stale sessions, finalize, or discard.

## Walkthrough UI

The UI should add a review walkthrough panel when a draft review stack exists.
It should show:

- Source branch and original source SHA.
- Current source SHA and stale/clean status.
- Ordered review groups with message, summary, changed files, and viewed state.
- A main diff pane scoped to the selected review group.
- Existing comment and AI review controls for the selected group.
- Actions: refresh, keep stale review, finalize, discard.

The UI cache may store conveniences such as viewed groups, expanded files,
review summaries, and generated rationales. Losing that cache must not prevent
recovery of the review stack because trailers are the source of truth.

## Stale Source Handling

On load, Shortcake compares each draft session's `Shortcake-Review-Source` SHA
with the current source branch head.

If they match, the session is clean.

If they differ, the session is stale. The UI should show that the source branch
changed since the review stack was generated and offer:

- `Refresh`: discard or supersede the current draft review stack and generate a
  new one from the current source diff. The refreshed stack gets a new
  `Shortcake-Review` id.
- `Keep`: continue reviewing the existing draft stack against the original
  source SHA.
- `Discard`: mark the review stack discarded and hide it from active review
  surfaces.

No option should silently overwrite the source branch.

## Finalize and Discard

Finalize should be explicit. The MVP supports one conservative finalization
path:

- Mark generated review commits with `Shortcake-Review-State: finalized`.
- Keep the generated review stack and source branch unchanged.
- Refuse to finalize if the source branch is stale unless the user confirms a
  stale finalize.

Discard marks review commits with `Shortcake-Review-State: discarded` and hides
the session from active review surfaces. A separate cleanup action may delete
generated review branches after confirmation when all generated branches are
still identifiable and no uncommitted work would be lost.

## AI Grouping

The AI grouping pass should produce structured JSON rather than free-form text.
The output should include:

- `groups`: ordered list of proposed review groups.
- `commit_message`: one-line proposed message.
- `summary`: short explanation for the group.
- `selections`: file path plus hunk indexes.
- `warnings`: ambiguous selections or files too large to group confidently.

Shortcake should validate the proposed selections against the actual patch
before creating branches. Invalid, overlapping, or incomplete hunk selections
should be rejected with a clear error or shown for manual correction.

The MVP calls the existing local AI CLIs (`claude` and/or `codex`) because
Shortcake already has model discovery and execution helpers. MCP execution is
out of scope for the MVP because the researched MCP tools expose generic Git
operations rather than this durable review-stack workflow.

## Persistence

Git trailers are authoritative for:

- review session identity
- source branch and source SHA
- group order
- lifecycle state
- parent relationships

Local cache is optional and should live under:

```text
.git/shortcake/reviews/<review-id>.json
```

The cache can store:

- AI rationale and grouping confidence
- viewed files and viewed groups
- dismissed comments
- UI layout preferences specific to the session

On startup, Shortcake scans local branches for draft review trailers and rebuilds
the session list. If the cache exists, it is merged in. If it is missing or
invalid, Shortcake still shows the review stack with reduced metadata.

## Error Handling

- If no AI provider is available, show a clear error and offer manual grouping
  with existing hunk/line selection tools.
- If generated branch names collide, add numeric suffixes.
- If patch application fails, roll back created refs and leave the source branch
  unchanged.
- If restack fails, use the existing Shortcake rebase/continue recovery model.
- If the trailer scan finds an incomplete review stack, show it as recoverable
  only when ordering and source metadata are sufficient; otherwise show a repair
  or discard action.
- If the source branch was deleted, show the session as orphaned and only allow
  keep/discard operations.

## Testing Strategy

Unit tests:

- Parse and apply review trailers while preserving `Shortcake-Parent`.
- Discover draft review sessions from branch history.
- Detect clean, stale, orphaned, finalized, and discarded sessions.
- Validate AI grouping JSON against patch hunks.
- Reject invalid, overlapping, or missing selections.

Git operation tests:

- Create a draft review stack from a multi-file branch diff.
- Reconstruct the stack after deleting the local cache.
- Detect staleness after the source branch advances.
- Discard generated branches without touching the source branch.
- Roll back cleanly when one generated group cannot apply.

UI/API tests:

- List active review sessions.
- Start review split generation.
- Show stale state and available actions.
- Navigate group-by-group in the walkthrough.
- Preserve viewed state when cache exists and degrade gracefully when it does not.

E2E documentation:

- Add an executable markdown scenario for generating a draft review stack,
  restarting Shortcake, detecting the same session from trailers, and discarding
  it.

## MVP Decisions

- Finalize marks the generated review stack as `finalized` and keeps the source
  branch unchanged. Replacing the source branch is not part of the MVP.
- Normal stack commands such as `sc ls` hide `review/...` branches by default.
  Review-specific commands and UI surfaces show them.
- AI grouping starts at hunk level. Existing manual line selection remains
  available in the diff UI, but AI-generated line-level grouping is not part of
  the MVP.

# Feedback Bundles And Agent Replies Design

## Context

Issue #115 asks for a way for an agent to reply to comments. Shortcake already
has most of the user-facing review surface:

- The web UI lets a user add inline comments to the active diff.
- AI review comments are shown inline and can be copied as prompts.
- The UI has branch diffs and a synthetic working diff.
- The local API already streams AI review results and already computes branch
  and working patches.

The missing piece is a durable handoff format. Today comments live in browser
memory and copying them only gives an agent text. There is no stable bundle ID,
no reply target, and no UI state for progress or final responses.

## Goals

- Let a user write local Shortcake feedback comments and hand all current
  comments to Codex, Claude, or another local agent.
- Give the agent a stable bundle ID and stable comment IDs to reference.
- Let the agent post incremental progress, per-comment statuses, questions, and
  a final summary back to Shortcake through CLI/API commands.
- Persist feedback bundles in Git-local metadata so replies survive UI reloads
  without being committed to the repository.
- Keep the MVP local-only. GitHub comments, MCP, and remote sharing are future
  layers, not part of this design.
- Provide a printable Codex skill so a user can ask Codex to install the
  Shortcake feedback workflow before working on a bundle.

## Non-Goals

- Do not fetch GitHub PR comments.
- Do not post replies to GitHub.
- Do not build an MCP server in the MVP.
- Do not run Codex or Claude from Shortcake for this workflow.
- Do not persist feedback bundles in the worktree.
- Do not support staged-only feedback as a separate source kind.
- Do not mutate an existing bundle when the user edits in-memory comments after
  bundle creation.

## Core Model

Shortcake persists feedback bundles under:

```text
.git/shortcake/feedback/<id>.json
```

The file is checkout-local and not committed. This matches existing local state
patterns such as `.git/shortcake/pr-cache.json` and `.git/shortcake-restack.json`.

Each bundle is a snapshot of the active comments and the diff source at creation
time. Comment IDs are stable short IDs within the bundle, such as `c1`, `c2`,
and `c3`. The UI can keep any internal IDs it needs, but the bundle and agent
prompt must use these stable IDs.

### Source Kinds

The MVP supports only the two diff sources that the UI already exposes.

For a tracked branch diff:

```json
{
  "kind": "branch",
  "branch": "feature-x",
  "parent": "main",
  "head": "abc123...",
  "patchHash": "sha256..."
}
```

For uncommitted working changes:

```json
{
  "kind": "working",
  "base": "HEAD",
  "head": "abc123...",
  "patchHash": "sha256..."
}
```

`branch.head` is the selected branch head. `working.head` is the repository
`HEAD` at bundle creation time. `patchHash` is computed from the exact patch
Shortcake used for the active diff.

Working bundles use the existing `git diff HEAD` behavior, so staged changes
are included only as part of the working diff. There is no distinct staged-only
source in the MVP.

### Bundle Shape

The persisted shape should be explicit and versioned:

```json
{
  "version": 1,
  "id": "fb-20260608-143012-a1b2",
  "createdAt": "2026-06-08T14:30:12Z",
  "updatedAt": "2026-06-08T14:45:00Z",
  "source": {
    "kind": "branch",
    "branch": "feature-x",
    "parent": "main",
    "head": "abc123...",
    "patchHash": "sha256..."
  },
  "comments": [
    {
      "id": "c1",
      "file": "src/app.py",
      "startLine": 42,
      "endLine": 42,
      "side": "additions",
      "text": "This error path needs a test.",
      "source": "user"
    }
  ],
  "activity": [
    {
      "id": "ev1",
      "createdAt": "2026-06-08T14:40:00Z",
      "type": "progress",
      "summary": "Fixed c1 and checking related tests.",
      "comments": [
        {
          "id": "c1",
          "status": "fixed",
          "note": "Added a regression test for the error path."
        }
      ]
    }
  ],
  "commentReplies": {
    "c1": {
      "status": "fixed",
      "note": "Added a regression test for the error path.",
      "updatedAt": "2026-06-08T14:40:00Z"
    }
  },
  "finalSummary": null
}
```

`source` on a comment is a display hint: `user`, `ai`, or `synthesis`. If a
comment comes from an AI review result, include `model` and `severity` as
optional fields.

`commentReplies` is derived from activity events but stored redundantly for
fast UI rendering. When a new activity event includes comment updates, Shortcake
updates this map.

### Reply Payloads

Agents reply with structured JSON:

```json
{
  "type": "progress",
  "summary": "Fixed c1 and checking c2",
  "comments": [
    {
      "id": "c1",
      "status": "fixed",
      "note": "Replaced the hard-coded value."
    }
  ]
}
```

Supported `type` values:

- `progress`: work is underway.
- `question`: the agent needs user input.
- `final`: the agent is done and this is the completion summary.

Supported per-comment `status` values:

- `open`
- `in_progress`
- `fixed`
- `needs_clarification`
- `wont_fix`

For `final` events, Shortcake stores `summary` in `finalSummary` and still
applies any per-comment updates.

## Agent Handoff

The existing `Copy N comments` action should become the agent handoff action.
The behavior remains "all comments for the active diff"; there is no per-comment
selection in the MVP.

Flow:

1. User writes feedback comments in the Shortcake UI.
2. User clicks the handoff button.
3. UI calls `POST /api/feedback` with the active source and all current
   comments.
4. Server creates a bundle and returns `{ "id": "...", "prompt": "..." }`.
5. UI copies the prompt to the clipboard and shows a copied state.
6. User gives the prompt to Codex or Claude.
7. Agent reads the bundle through `shortcake feedback show <id> --json`.
8. Agent posts progress and final responses through
   `shortcake feedback reply <id> --json '<payload>'`.

The copied prompt should be small and should not include the full patch. It
should include the bundle ID, repository-local commands, and skill setup:

```text
Codex, install the Shortcake feedback skill before working on this.

Run:
mkdir -p ~/.codex/skills/shortcake-feedback
shortcake skill --print shortcake-feedback > ~/.codex/skills/shortcake-feedback/SKILL.md

Then use that skill to process feedback bundle fb-20260608-143012-a1b2 in this repository.
```

The prompt may use `sc` instead of `shortcake` only if the command is available;
the generated prompt should prefer `shortcake` for clarity.

## Skill Command

Add a general skill command:

```bash
shortcake skill --print shortcake-feedback
```

The command prints a complete `SKILL.md` suitable for writing to
`~/.codex/skills/shortcake-feedback/SKILL.md`.

The skill should be concise and should tell Codex to:

1. Extract the bundle ID from the user request.
2. Run `shortcake feedback show <id> --json`.
3. Read the comments and inspect the referenced files.
4. Implement fixes or determine why a comment cannot be fixed.
5. Run relevant checks based on the repository.
6. Post incremental updates when useful with
   `shortcake feedback reply <id> --json '<payload>'`.
7. Always post a final reply with per-comment statuses and an overall summary.

The skill should prefer stable comment IDs over file/line/text matching.

## CLI

Add a `feedback` command group and a `skill` command.

```bash
shortcake feedback create --json comments.json
shortcake feedback show <id> --json
shortcake feedback reply <id> --json '<payload>'
shortcake skill --print shortcake-feedback
```

`feedback create --json` reads a JSON file. The JSON must include `source` and
`comments`. The server/UI will normally create bundles, but this CLI path gives
scripts and tests a direct entry point.

`feedback show <id> --json` prints the full bundle JSON. Non-JSON display can be
added later, but JSON is enough for MVP.

`feedback reply <id> --json` accepts either an inline JSON string or `@path`
syntax. Supporting `@path` avoids shell quoting problems for larger replies.

Error behavior:

- Missing bundle: exit 1 with a clear error.
- Invalid JSON: exit 1 and report parse failure.
- Unknown comment ID in a reply: exit 1 and do not partially update the bundle.
- Unknown status or type: exit 1 and do not partially update the bundle.

## Local API

Add API routes to the existing UI server:

```text
POST /api/feedback
GET  /api/feedback?kind=branch&branch=<name>
GET  /api/feedback?kind=working
GET  /api/feedback/<id>
POST /api/feedback/<id>/reply
```

`POST /api/feedback` validates the request, recomputes or verifies the source
fingerprint, writes the bundle, and returns:

```json
{
  "id": "fb-20260608-143012-a1b2",
  "prompt": "Codex, install..."
}
```

`GET /api/feedback` returns active, non-stale bundles for the requested source.
For branch requests, the active source matches only if kind, branch, head, and
patch hash match. For working requests, it matches only if kind, current `HEAD`,
and working patch hash match.

`POST /api/feedback/<id>/reply` accepts the same payload as the CLI. The copied
prompt tells agents to use the CLI, but the API keeps the UI and CLI behavior
backed by the same application service.

## UI Behavior

The UI should keep the current comment creation model. The handoff button should
use all active comments for the active diff.

When no feedback bundle exists for the active source:

- The top bar shows the handoff button when comments exist.
- Clicking the button creates a bundle and copies the agent prompt.

When a feedback bundle exists for the active source:

- Show a compact activity panel above the diff.
- The panel includes the bundle ID, last activity time, latest progress or final
  summary, and whether the agent asked a question.
- Inline comments show per-comment status badges when replies exist.
- Inline comments show agent notes below the original comment text.
- If multiple bundles exist for the same active source, show the newest first
  and keep older matching bundles accessible in the activity panel.

After a bundle is created, it is a snapshot. Editing or deleting current
in-memory UI comments does not mutate that bundle.

When a bundle is stale, hide it from the active UI by default. A future command
can reopen by ID, but that is not part of the MVP UI.

## Staleness

Staleness is evaluated against the source fingerprint stored in the bundle.

Branch bundle is current when:

- selected source kind is `branch`
- selected branch name equals bundle source branch
- selected branch head equals bundle source head
- selected branch patch hash equals bundle source patch hash

Working bundle is current when:

- selected source kind is `working`
- current `HEAD` equals bundle source head
- current working patch hash equals bundle source patch hash

If any check differs, the bundle is stale and is hidden from the active UI.

## Implementation Notes

Add a dedicated module, likely `shortcake._feedback`, for bundle models and
storage. Keep file I/O out of the React code and out of the command wrappers.

Suggested internal functions:

- `feedback_dir(repo) -> Path`
- `create_bundle(repo, source, comments) -> FeedbackBundle`
- `load_bundle(repo, id) -> FeedbackBundle`
- `list_bundles(repo) -> list[FeedbackBundle]`
- `append_reply(repo, id, reply) -> FeedbackBundle`
- `is_bundle_current(repo, bundle) -> bool`
- `build_agent_prompt(bundle) -> str`
- `print_skill(name) -> str`

Use Pydantic models because the project already depends on Pydantic and this is
schema-heavy local JSON. Keep command wrappers thin, matching existing
Shortcake command conventions.

Use atomic writes for bundle updates: write a temporary file in the feedback
directory and replace the target file.

## Testing

Python tests:

- create branch bundle with valid JSON
- create working bundle with valid JSON
- reject invalid create JSON
- show bundle as JSON
- append progress reply
- append question reply
- append final reply and update `finalSummary`
- update per-comment status map
- reject unknown comment IDs
- reject invalid reply type/status
- hide stale branch bundles when branch head changes
- hide stale working bundles when working patch hash changes
- print `shortcake-feedback` skill
- reject unknown skill names
- API create/list/show/reply paths

Frontend tests:

- comments produce a copied agent prompt instead of plain markdown
- prompt includes skill install snippet and bundle ID
- matching bundle shows activity panel
- per-comment reply status renders inline
- stale bundle is not shown for changed source

Executable markdown scenario:

- Add feedback comments in the UI.
- Create a bundle.
- Simulate an agent reply through `shortcake feedback reply`.
- Verify the UI shows progress/final status.

## Future Work

- MCP resources/tools backed by the same bundle store.
- GitHub PR/review comment import and optional GitHub replies.
- Reopen stale or historical bundles by ID in the UI.
- Per-comment selection when creating a bundle.
- Separate staged-only source kind.
- Built-in agent execution from Shortcake.

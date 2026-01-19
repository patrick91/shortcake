# Implementation Order

Commands are implemented one at a time, with full test coverage before moving to the next.

## Phase 1: Core Infrastructure

Before any command, build:
- `shortcake/adapters/git/` - Git operations (subprocess-based)
- `shortcake/adapters/storage.py` - Trailer read/write + cache
- `shortcake/core/` - Data models

## Phase 2: Commands (In Order)

| # | Command | Why This Order | Depends On |
|---|---------|---------------|------------|
| 1 | `sc adopt` | Simplest, tests trailer mechanism | Infrastructure |
| 2 | `sc ls` | Read-only, tests trailer reading | adopt |
| 3 | `sc create` | Builds on adopt + commit creation | adopt, ls |
| 4 | `sc commit` | Simple wrapper, needed for workflow | Infrastructure |
| 5 | `sc add` | Simple wrapper for staging | Infrastructure |
| 6 | `sc status` | Builds on ls, more detail | ls |
| 7 | `sc checkout` | Remote trailer reading | ls |
| 8 | `sc restack` | Core stack operation | adopt, ls |
| 9 | `sc continue` / `sc abort` | Conflict resolution | restack |
| 10 | `sc sync` | Complex: merge detection + restack | restack |
| 11 | `sc submit` | GitHub integration | sync |
| 12 | `sc pull` | Multi-device workflow | sync |
| 13 | `sc delete` | Cleanup | ls |
| 14 | `sc nav` (up/down/top/bottom) | Navigation | ls |
| 15 | `sc move` | Change parent | restack |
| 16 | `sc split` | Advanced | restack |
| 17 | `sc log` / `sc diff` | Utilities | Infrastructure |

## Implementation Files

- `01-adopt.md` - Detailed plan for sc adopt

## Rules

1. **100% test coverage** for each command before moving on
2. **Manual testing** checklist must pass
3. **No skipping ahead** - dependencies must be complete first

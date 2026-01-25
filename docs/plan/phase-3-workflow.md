# Phase 3: Daily Workflow

Commands for everyday git operations.

## Commands

### 8. `sc log`

Show commits on current branch only.

```bash
sc log           # This branch's commits
sc log --all     # Full git log
```

**Implementation:**
- Read `Shortcake-Parent` trailer to find parent
- Show commits between parent and HEAD
- `--all` falls back to full git log

**Tests:**
- [x] Log on tracked branch
- [x] Log on untracked branch (fallback)
- [x] Log --all

---

## Checklist

- [x] Implement `sc log`

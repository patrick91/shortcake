# Phase 3: Daily Workflow

Commands for everyday git operations.

## Commands

### 8. `sc pull`

Update current branch from remote.

```bash
sc pull           # Fast-forward from origin
sc pull --rebase  # Rebase if not fast-forwardable
```

**Implementation:**
- Fetch from origin
- Check if current branch has remote tracking branch
- Fast-forward if possible
- If not fast-forwardable: error (or rebase with `--rebase` flag)

**Tests:**
- [x] Pull when up to date (no-op)
- [x] Pull when behind remote (fast-forward)
- [x] Pull when diverged (error without --rebase)
- [x] Pull --rebase when diverged
- [x] Pull on branch with no remote (error)

---

### 9. `sc log`

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

- [x] Implement `sc pull`
- [x] Implement `sc log`

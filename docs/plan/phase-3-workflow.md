# Phase 3: Daily Workflow

Commands for everyday git operations.

## Commands

### 8. `sc commit`

Commit wrapper with gitmoji support (later).

```bash
sc commit -m "message"
sc commit --amend
sc commit -a
```

**Key behavior:**
- Does NOT add trailers (only first commit has trailer)
- Works during conflict resolution

**Implementation:**
- Stage files if `-a`
- Create commit with message
- If `--amend`, amend instead

**Tests:**
- [ ] Normal commit
- [ ] Amend commit
- [ ] Commit with `-a`
- [ ] Commit during rebase (conflict resolution)

---

### 9. `sc status`

Show stack status with details.

```bash
sc status
```

**Output:**
```
◉ feature-2     2 commits, no PR
│
◯ feature-1     1 commit, PR #123 (open)
│
◯ main          (trunk)

Working tree: 2 modified files
```

**Implementation:**
- Get current stack
- For each branch: commit count, PR status
- Show working tree status

**Tests:**
- [ ] Stack with no PRs
- [ ] Stack with PRs
- [ ] Uncommitted changes
- [ ] Clean working tree

---

### 10. `sc log`

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
- [ ] Log on tracked branch
- [ ] Log on untracked branch (fallback)
- [ ] Log --all

---

## Checklist

- [ ] Implement `sc commit`
- [ ] Implement `sc status`
- [ ] Implement `sc log`

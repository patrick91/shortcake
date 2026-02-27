# Phase 4: Stack Operations

Commands for rebasing and syncing stacks.

## Commands

### 9. `sc restack`

Rebase branches onto updated parents.

```bash
sc restack              # Restack current stack
sc restack --dry-run    # Preview
```

**Flow:**
1. Find branches needing rebase (merge-base != parent tip)
2. Rebase each in order (parent first)
3. On conflict: stop, guide user

**Implementation:**
- For each branch in stack (bottom to top):
  - Check if merge-base(branch, parent) == parent HEAD
  - If not, rebase branch onto parent
- Save state for `sc continue`

**Tests:**
- [ ] Restack when already up to date
- [ ] Restack single branch
- [ ] Restack multi-branch stack
- [ ] Restack with conflict
- [ ] Dry run

---

### 10. `sc continue`

Continue after conflict resolution.

```bash
sc continue    # Continue after conflict
```

**Implementation:**
- Check if rebase in progress
- Continue rebase
- If more branches to restack, continue with next

**Tests:**
- [ ] Continue restack
- [ ] Continue when nothing in progress (error)

---

### 11. `sc abort`

Abort in-progress operation.

```bash
sc abort       # Abort operation
```

**Implementation:**
- Check if rebase in progress
- Abort rebase
- Clear saved state

**Tests:**
- [ ] Abort restack
- [ ] Abort when nothing in progress (error)

---

### 12. `sc sync`

Clean up after merges.

```bash
sc sync              # Full sync
sc sync --dry-run    # Preview
```

**Flow:**
1. Fetch origin
2. Fast-forward trunk
3. Detect merged branches (regular + squash merge)
4. Update children's parents
5. Delete merged branches

**Implementation:**
- Fetch from origin
- For each tracked branch:
  - Check if merged into trunk (commit reachable or squash-merge detected)
  - If merged: update children to point to merged branch's parent, delete branch
- Fast-forward remaining branches if possible

**Tests:**
- [ ] Sync with nothing to do
- [ ] Sync after regular merge
- [ ] Sync after squash merge
- [ ] Sync with deep stack
- [ ] Dry run

---

## Checklist

- [x] Implement `sc restack`
- [x] Implement `sc continue`
- [x] Implement `sc abort`
- [x] Implement `sc sync`

# Phase 6: Advanced

Additional commands for complex operations.

## Commands

### 17. `sc delete`

Remove branch from stack.

```bash
sc delete <branch>         # Delete branch
sc delete <branch> --keep  # Untrack only (keep branch)
```

**Implementation:**
- Find branch's children
- Update children's `Shortcake-Parent` to point to deleted branch's parent
- Delete branch (unless `--keep`)

**Tests:**
- [ ] Delete leaf branch (no children)
- [ ] Delete middle branch (update children)
- [ ] Delete with --keep
- [ ] Delete current branch (checkout parent first)

---

### 18. `sc move`

Move branch to different parent.

```bash
sc move --onto new-parent
```

**Implementation:**
- Rebase current branch onto new parent
- Update `Shortcake-Parent` trailer in first commit

**Tests:**
- [ ] Move to different branch
- [ ] Move to trunk
- [ ] Move with conflicts

---

## Future Commands (Not Planned)

These might be useful but are out of scope for initial implementation:

- `sc split` - Split branch into multiple branches
- `sc fold` - Merge branch into parent
- `sc edit` - Interactive rebase for branch commits
- `sc diff` - Diff against parent branch

---

## Checklist

- [ ] Implement `sc delete`
- [ ] Implement `sc move`

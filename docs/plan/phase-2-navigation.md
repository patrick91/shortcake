# Phase 2: Navigation

Commands to move within a stack.

## Commands

### 4. `sc up`

Move to child branch.

```bash
sc up      # Go to child
```

**Implementation:**
- Find branches with `Shortcake-Parent` pointing to current branch
- If one child → checkout
- If multiple children → prompt user to pick
- If no children → warning "Already at top of stack"

**Tests:**
- [x] Move up in simple stack
- [x] At top (warning)
- [x] Multiple children (prompt)
- [x] Working directory updated correctly

---

### 5. `sc down`

Move to parent branch.

```bash
sc down    # Go to parent
```

**Implementation:**
- Read `Shortcake-Parent` trailer from current branch
- Checkout parent
- If parent is trunk → warning "Already at bottom of stack"

**Tests:**
- [x] Move down in simple stack
- [x] At bottom (warning)
- [x] On untracked branch (error or warning)
- [x] Working directory updated correctly

---

### 6. `sc top`

Jump to top of current stack.

```bash
sc top     # Top of stack
```

**Implementation:**
- Repeatedly find child until no more children
- If multiple paths, follow longest or prompt

**Tests:**
- [x] Jump to top
- [x] Already at top
- [x] Branching stack (multiple tops)
- [x] Working directory updated correctly

---

### 7. `sc bottom`

Jump to first branch above trunk.

```bash
sc bottom  # First branch above trunk
```

**Implementation:**
- Walk down via `Shortcake-Parent` until parent is trunk
- Checkout that branch

**Tests:**
- [x] Jump to bottom
- [x] Already at bottom
- [x] On trunk (error - not tracked)
- [x] Working directory updated correctly

---

## Checklist

- [x] Implement `sc up`
- [x] Implement `sc down`
- [x] Implement `sc top`
- [x] Implement `sc bottom`

## Notes

- Uses `switch_branch()` (via `porcelain.switch()`) to properly update working directory
- `set_head_to_branch()` exists for `create` command where staged changes must be preserved

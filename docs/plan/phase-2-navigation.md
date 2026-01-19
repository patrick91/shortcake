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
- [ ] Move up in simple stack
- [ ] At top (warning)
- [ ] Multiple children (prompt)

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
- [ ] Move down in simple stack
- [ ] At bottom (warning)
- [ ] On untracked branch (error or warning)

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
- [ ] Jump to top
- [ ] Already at top
- [ ] Branching stack (multiple tops)

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
- [ ] Jump to bottom
- [ ] Already at bottom
- [ ] On trunk (warning)

---

## Checklist

- [ ] Implement `sc up`
- [ ] Implement `sc down`
- [ ] Implement `sc top`
- [ ] Implement `sc bottom`

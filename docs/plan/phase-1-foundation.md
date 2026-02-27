# Phase 1: Foundation

Core commands to track and visualize branches.

## Commands

### 1. `sc adopt`

Track an existing branch by adding the trailer to its first commit.

```bash
sc adopt [branch]              # Adopt current or specified branch
sc adopt [branch] --parent X   # Explicit parent
```

**Implementation:**
- Find first commit on branch (relative to detected/specified parent)
- Amend that commit to add `Shortcake-Parent: <parent>` trailer
- Error if already tracked

**Tests:**
- [x] Adopt from main
- [x] Adopt from feature branch
- [x] Adopt with explicit --parent
- [x] Already tracked (error)
- [x] On trunk branch (error)

---

### 2. `sc ls`

List all tracked branches as a tree.

```bash
sc ls              # All tracked branches
```

**Output:**
```
◯ stack_1_branch_C
│
◯ stack_1_branch_B
│
◯ stack_1_branch_A
│
│ ◯ stack_2_branch_B
│ │
│ ◯ stack_2_branch_A
│ │
│ │ ◯ stack_3_branch_A
│ │ │
◉─┴─┘ main (current)
```

- Shows ALL tracked branches, grouped by stack
- Current branch marked with `◉` and `(current)`
- Other branches marked with `◯`
- Vertical lines connect branches in same stack
- A stack is 2+ branches (single tracked branch shown but not "stacked")

**Implementation:**
- Scan all branches for `Shortcake-Parent` trailers
- Build tree structure (trunk → children → grandchildren)
- Render with box-drawing characters

**Tests:**
- [x] Empty (no tracked branches)
- [x] Single tracked branch (not a stack)
- [x] One stack with multiple branches
- [x] Multiple independent stacks (parallel stacks)
- [x] Current branch highlighting
- [x] Orphan branch (parent deleted) shows warning
- [x] Circular reference detection

---

### 3. `sc create`

Create new branch with tracking.

```bash
sc create -m "feat: message"   # Create with message
sc create                      # Interactive (later)
```

**Implementation:**
- Get current branch as parent
- Generate branch name from message
- Create branch at current HEAD
- Create commit with trailer

**Tests:**
- [x] Create from main
- [x] Create from feature (stacking)
- [x] With staged changes
- [x] Branch name generation
- [x] Special characters in message

---

## Checklist

- [x] Add dulwich + pytest to dependencies
- [x] Create test fixtures
- [x] Implement `sc adopt`
- [x] Implement `sc ls`
- [x] Implement `sc create`

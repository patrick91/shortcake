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
- [ ] Adopt from main
- [ ] Adopt from feature branch
- [ ] Adopt with explicit --parent
- [ ] Already tracked (error)
- [ ] On trunk branch (error)

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
◉─┴─┴─ main (current)
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
- [ ] Empty (no tracked branches)
- [ ] Single tracked branch (not a stack)
- [ ] One stack with multiple branches
- [ ] Multiple independent stacks
- [ ] Current branch highlighting

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
- [ ] Create from main
- [ ] Create from feature (stacking)
- [ ] With staged changes
- [ ] Branch name generation
- [ ] Special characters in message

---

## Checklist

- [ ] Add dulwich + pytest + rich-toolkit to dependencies
- [ ] Create test fixtures
- [ ] Implement `sc adopt`
- [ ] Implement `sc ls`
- [ ] Implement `sc create`

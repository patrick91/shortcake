# Navigation Commands

Shortcake provides a set of commands for navigating through your branch stacks, making it easy to move between related branches without remembering branch names.

## Commands Overview

The navigation commands allow you to move through your branch stack in different directions:

- `sc up` - Move up the stack to a child branch (toward the tip, away from main)
- `sc down` - Move down the stack to the parent branch (toward main)
- `sc top` - Jump to the top of the stack (furthest from main)
- `sc bottom` - Jump to the bottom of the stack (closest to main)
- `sc checkout` - Switch to any branch by name, PR number, or interactively

## Command Details

### `sc up`

Move up the stack to a child branch (toward tip, away from main).

**Usage:**
```bash
sc up
```

**Behavior:**
- If the current branch has **one child**: Automatically switches to that child branch
- If the current branch has **multiple children**: Lists all children and asks you to manually checkout one
- If the current branch has **no children**: Displays a message that you're already at the top

**Algorithm:**
1. Get the current branch
2. Find all child branches (branches that have this branch as their parent)
3. Handle based on number of children:
   - 0 children: Display "already at top" message
   - 1 child: Checkout the child branch
   - Multiple children: List them for manual selection

**Example:**
```bash
# Stack: main -> feature-1 -> feature-2
$ git checkout feature-1
$ sc up
Switched to feature-2
```

### `sc down`

Move down the stack to the parent branch (toward main).

**Usage:**
```bash
sc down
```

**Behavior:**
- Switches to the parent branch of the current branch
- If already on trunk (main/master): Displays message
- If the branch has no parent metadata: Shows an error

**Algorithm:**
1. Get the current branch
2. Check if already on trunk branch (main/master) - exit if true
3. Read the branch metadata to find the parent
4. Checkout the parent branch

**Example:**
```bash
# Stack: main -> feature-1 -> feature-2
$ git checkout feature-2
$ sc down
Switched to feature-1
```

### `sc top`

Move to the top of the stack (furthest from main).

**Usage:**
```bash
sc top
```

**Behavior:**
- Walks up the stack following child branches until reaching a leaf node
- If multiple children are encountered: Stops at that branch
- If already at the top: Displays message

**Algorithm:**
1. Start at the current branch
2. Loop:
   - Get children of the current branch
   - If no children: This is the top, break
   - If multiple children: Stop here (ambiguous path)
   - If one child: Move to that child and continue
3. Checkout the top branch if different from starting branch

**Example:**
```bash
# Stack: main -> feature-1 -> feature-2 -> feature-3
$ git checkout feature-1
$ sc top
Switched to feature-3
```

### `sc bottom`

Move to the bottom of the stack (closest to main).

**Usage:**
```bash
sc bottom
```

**Behavior:**
- Walks down the stack following parent branches until reaching trunk or the first non-shortcake branch
- If already on trunk: Displays message
- If already at the bottom: Displays message

**Algorithm:**
1. Get the current branch
2. Check if already on trunk branch (main/master) - exit if true
3. Loop:
   - Get the parent of the current branch
   - If no parent or parent is trunk: This is the bottom, break
   - Move to the parent and continue
4. Checkout the bottom branch if different from starting branch

**Example:**
```bash
# Stack: main -> feature-1 -> feature-2 -> feature-3
$ git checkout feature-3
$ sc bottom
Switched to feature-1
```

### `sc checkout`

Switch to a branch by name, PR number, or interactively select from all managed branches.

**Usage:**
```bash
sc checkout                    # Interactive mode
sc checkout feature-1          # By branch name
sc checkout 123               # By PR number
```

**Options:**
- `target` (optional): Branch name or PR number

**Behavior:**
- **No argument**: Shows an interactive menu of all shortcake-managed branches with filtering
- **Numeric argument**: Looks up the branch by PR number
- **Text argument**: Treats as a branch name and checks out that branch

**Algorithm:**

**Interactive mode** (no argument):
1. Get all branch metadata
2. Build a list of options showing:
   - Branch name
   - PR number (if available)
   - "(current)" indicator
3. Display interactive menu with filtering support
4. Checkout the selected branch

**PR number mode** (numeric argument):
1. Parse the argument as an integer
2. Search all branch metadata for a matching PR number
3. If found: Checkout that branch
4. If not found: Display error

**Branch name mode** (text argument):
1. Check if the branch exists
2. If exists: Checkout the branch
3. If not exists: Display error

**Examples:**
```bash
# Interactive selection
$ sc checkout
Select a branch:
> feature-1 #42 (current)
  feature-2 #43
  feature-3

# Switch by branch name
$ sc checkout feature-2
Switched to feature-2

# Switch by PR number
$ sc checkout 43
Switched to feature-2 (PR #43)
```

## Error Handling

All navigation commands include robust error handling:

### Uncommitted Changes

If you have uncommitted changes that would be overwritten:
```
Error: You have uncommitted changes that would be overwritten.
Please commit or stash your changes before switching branches.
```

### Missing Parent

If a branch has no parent metadata:
```
Error: Branch 'feature-1' has no parent (not managed by shortcake)
```

### Branch Not Found

If trying to checkout a non-existent branch:
```
Error: Branch 'feature-x' does not exist
```

### PR Number Not Found

If trying to checkout by PR number that doesn't exist:
```
Error: No branch found for PR #999
```

## Implementation Notes

- All commands use the `_safe_checkout()` helper function which provides user-friendly error messages for common checkout failures
- The parent-child relationship is tracked in branch metadata (stored in `.git/shortcake/branches/<branch-name>.json`)
- The interactive branch picker supports filtering for quick selection in repositories with many branches
- Commands that walk the stack (up/down/top/bottom) follow the metadata relationships rather than git history

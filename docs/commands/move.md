# Move Command

The `move` command allows you to change a branch's parent and optionally rebase it onto the new parent. This is useful for reorganizing your branch stack when dependencies change.

## Purpose

When working with stacked branches, you may need to move a branch from one parent to another. For example:

- A feature branch was originally based on `main` but now needs to be based on another feature branch
- You want to reorganize your branch hierarchy
- Dependencies between branches have changed

The `move` command updates both the metadata (parent tracking) and physically rebases the branch onto its new parent.

## Options

### `--onto`, `-o`
Specifies the new parent branch. If not provided, an interactive menu will appear allowing you to select from all available branches.

**Type:** `string` (optional)

**Example:**
```bash
sc move --onto main
sc move -o feature-1
```

### `branch` (positional argument)
The branch to move. If not specified, defaults to the current branch.

**Type:** `string` (optional)

**Example:**
```bash
sc move feature-2 --onto main
```

### `--no-rebase`
Only update the metadata without performing a rebase. Use this when you've already manually rebased or want to defer the rebase operation.

**Type:** `boolean` (default: `false`)

**Example:**
```bash
sc move --onto feature-1 --no-rebase
```

## Algorithm

The move command follows this step-by-step process:

### 1. Validation Phase

1. **Initialize Git repository** - Verify we're in a valid git repository
2. **Determine branch to move** - Use provided branch or current branch
3. **Validate branch exists** - Ensure the branch to move exists locally
4. **Check for trunk branch** - Prevent moving `main` or `master` branches
5. **Verify branch is tracked** - Ensure the branch has shortcake metadata (has a parent)
   - If not tracked, exit with error suggesting to run `sc adopt` first

### 2. Parent Selection

1. **Interactive mode** (if `--onto` not provided):
   - List all branches except the one being moved
   - Mark the current parent with " (current parent)" suffix
   - Show filterable menu for selection
   - Cancel if no selection made
2. **Direct mode** (if `--onto` provided):
   - Use the specified parent branch

### 3. Additional Validation

1. **Validate new parent exists** - Ensure the target parent branch exists
2. **Prevent self-reference** - Cannot move a branch onto itself
3. **Check if already the parent** - Exit early if already using this parent

### 4. Metadata-Only Mode (`--no-rebase`)

If `--no-rebase` flag is set:

1. Update branch metadata with new parent
2. Store the current SHA of the new parent as `parent_revision`
3. Display success message indicating metadata-only update
4. Exit

### 5. Rebase Mode (default)

1. **Retrieve old parent revision**:
   - Use stored `parent_revision` from metadata
   - If not available, fallback to merge-base between branch and old parent
2. **Checkout branch** - Switch to the branch if not already on it
3. **Perform rebase**:
   - Execute `git rebase --onto <new-parent> <old-parent-revision> <branch>`
   - This rebases only the commits unique to this branch
4. **Handle conflicts**:
   - If conflicts occur, display instructions for resolution
   - Update metadata even during conflict (so parent is correct after resolution)
   - Exit with error code
5. **Update metadata** (on success):
   - Set new parent
   - Store current SHA of new parent as `parent_revision`
6. **Display success message**

## Examples

### Interactive Mode

Move the current branch and interactively select the new parent:

```bash
sc move
```

This will show a filterable menu of all available branches (excluding the current branch).

### Move Current Branch to Main

```bash
sc move --onto main
```

Moves the current branch to be based on `main` and rebases it.

### Move Specific Branch

```bash
sc move feature-2 --onto feature-1
```

Moves `feature-2` to be based on `feature-1` instead of its current parent.

### Metadata-Only Update

```bash
sc move --onto main --no-rebase
```

Updates the parent metadata without performing a rebase. Useful when you've manually rebased already.

### Complete Example

Starting with this branch structure:

```
main:        A---B---C
              \
feature-1:     D---E
                \
feature-2:       F---G
```

Running:
```bash
sc move feature-2 --onto main
```

Results in:
```
main:        A---B---C
              \       \
feature-1:     D---E   F'---G'
```

Where `F'` and `G'` are the rebased commits from `feature-2`, now based on `main` instead of `feature-1`.

## How It Works: Rebase Onto

The command uses `git rebase --onto` which has the signature:

```bash
git rebase --onto <new-base> <old-base> <branch>
```

This rebases the commits between `<old-base>` and `<branch>` onto `<new-base>`. By using the stored `parent_revision` (or merge-base with the old parent), shortcake ensures only the commits unique to the branch being moved are rebased.

**Example:**

```
Original:
  A---B---C (main)
   \
    D---E (feature-1)
     \
      F---G (feature-2)

Command: sc move feature-2 --onto main

Internally executes:
  git rebase --onto main <merge-base of feature-2 and feature-1> feature-2

Result:
  A---B---C (main)
   \       \
    D---E   F'---G' (feature-2, now based on main)
```

## Error Handling

The move command handles several error cases:

1. **Not a git repository** - Exits with error
2. **Branch doesn't exist** - Exits with error
3. **Trying to move trunk branch** - Cannot move `main`/`master`
4. **Branch not tracked by shortcake** - Must run `sc adopt` first
5. **New parent doesn't exist** - Exits with error
6. **Self-reference** - Cannot move branch onto itself
7. **Rebase conflicts** - Provides clear instructions for resolution

## Related Commands

- `sc adopt` - Start tracking a branch with shortcake
- `sc restack` - Update all branches in a stack when parent changes
- `sc sync` - Sync branch metadata with remote changes

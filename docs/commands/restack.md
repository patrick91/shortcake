# Restack Command

The `restack` command rebases your stacked branches onto their updated parents, ensuring all branches in the stack are based on their parent's latest commit.

## Purpose

When working with stacked branches, parent branches often get updated (either locally or via remote changes). The `restack` command automatically detects which branches need rebasing and efficiently restacks them in topological order.

## Options

```bash
sc restack [OPTIONS]
```

### Available Options

- `--dry-run`, `-n` - Preview what would be done without making any changes
- `--continue` - Continue after resolving rebase conflicts
- `--abort` - Abort the current rebase operation
- `--debug` - Show debug information about restack decisions

## How It Works

### Algorithm Overview

The restack command follows this sequence:

1. **Validation**
   - Verify not on trunk branch (main/master)
   - Check current branch is managed by shortcake (has parent metadata)
   - Ensure no rebase is already in progress
   - Check for uncommitted changes (must commit or stash first)

2. **Fetch Remote Updates**
   - Fetch from `origin` if remote exists
   - Fast-forward local trunk branch (`main`/`master`) to match `origin/main` if behind
   - Fast-forward any branches in the stack that are behind their remote counterparts

3. **Build Restack List**
   - Walk up from current branch to trunk to get ancestor branches
   - Walk down from current branch to get all descendant branches
   - Result: complete stack from trunk to current branch, plus all descendants
   - Order: topological (parents before children)

4. **Detect What Needs Restacking**
   - For each branch, compare stored `parent_revision` with parent's current HEAD
   - If they match, branch is up to date (skip)
   - If they differ, branch needs rebasing

5. **Rebase Branches**
   - For each branch that needs restacking (in topological order):
     - Use `git rebase --onto <parent> <parent_revision> <branch>`
     - This rebases only the commits unique to the branch
     - Update metadata with new `parent_revision`

6. **Return to Original Branch**
   - Checkout the original branch after all rebases complete

### Parent Revision Tracking

Shortcake uses the same approach as Graphite/Charcoal for detecting when a branch needs restacking:

- Each branch stores a `parent_revision` in its metadata
- This is the SHA where the branch was originally based (or last rebased)
- When parent changes (new commits, rebased, etc.), the parent's HEAD differs from stored `parent_revision`
- This triggers a restack

**Why not use merge-base?**

Merge-base can be incorrect when the parent itself has been rebased. Using the stored `parent_revision` ensures we rebase from the exact point where the branch diverged.

### Remote Ref Resolution

For trunk branches (main/master), the command uses `origin/main` or `origin/master` as the rebase target. This ensures branches are rebased onto the latest remote version, not just the local copy.

### Conflict Resolution

If a rebase encounters conflicts:

1. The command stops and shows conflict resolution instructions
2. User manually resolves conflicts and stages files (`git add`)
3. User runs `sc restack --continue` to complete the rebase
4. Metadata is updated when `--continue` succeeds

## Examples

### Basic Restack

Restack the current branch and all its descendants:

```bash
sc restack
```

**Output:**
```
Fetching from origin...
Updated main to latest

Checking 3 branch(es)...
  feature-1 does not need to be restacked
  feature-2 → origin/main... done
  feature-3 → feature-2... done

Restack complete! Rebased 2 branch(es).
```

### Preview Changes (Dry Run)

See what would be restacked without making changes:

```bash
sc restack --dry-run
```

**Output:**
```
Would fetch from origin...

Would check the following branches:
  feature-1 → origin/main (up to date)
  feature-2 → origin/main (needs restack)
  feature-3 → feature-2 (needs restack)
```

### Handling Conflicts

When conflicts occur:

```bash
$ sc restack
Fetching from origin...

Checking 2 branch(es)...
  feature-1 → origin/main... CONFLICT

Error: Rebase failed with conflicts

To resolve:
  1. Fix the conflicts in the affected files
  2. Stage the resolved files: git add <files>
  3. Continue the restack: sc restack --continue

Or abort with:
  sc restack --abort
```

After resolving conflicts:

```bash
# Fix conflicts in files
$ git add file1.py file2.py

# Continue the restack
$ sc restack --continue
Rebase continued successfully
```

### Debug Mode

See detailed information about restack decisions:

```bash
sc restack --debug
```

**Output:**
```
Fetching from origin...

Checking 2 branch(es)...
    DEBUG: branch=feature-1, parent=origin/main
    DEBUG: stored_parent_rev=abc123...
    DEBUG: parent_commit=def456...
    DEBUG: needs_restack=True
  Rebasing feature-1 onto origin/main... done
```

## Stack Behavior

The restack command operates on a specific set of branches:

### What Gets Restacked

1. **Ancestors:** All branches from trunk up to current branch
2. **Current:** The current branch itself
3. **Descendants:** All children, grandchildren, etc. of current branch

### Example Stack

```
main
  ├── feature-A
  │     └── feature-B (current)
  │           ├── feature-C
  │           └── feature-D
  └── feature-X
```

Running `sc restack` from `feature-B` will check and restack:
- `feature-A` (ancestor)
- `feature-B` (current)
- `feature-C` (descendant)
- `feature-D` (descendant)

**Note:** `feature-X` is not included because it's not in the current branch's lineage.

## Performance Optimizations

### Skip Unnecessary Rebases

Only branches that actually need restacking are rebased. Branches where `parent_revision` matches the parent's current HEAD are skipped.

### Fast-Forward Updates

Before rebasing, the command fast-forwards branches that are behind their remote counterparts. This handles cases where:
- Branch was updated via GitHub UI
- Branch was updated on another machine
- Parent was pushed by another developer

### Topological Ordering

Branches are always rebased in topological order (parents before children). This ensures:
- Parent updates are applied before rebasing children
- No branch is rebased multiple times
- Metadata updates cascade correctly through the stack

## Common Scenarios

### After Updating Main

```bash
# Update main from remote
git checkout main
git pull

# Return to feature branch and restack
git checkout feature-1
sc restack
```

### After Parent Branch Gets Rebased

If a parent branch in your stack gets rebased or updated:

```bash
# Parent branch (feature-1) was rebased
git checkout feature-2  # Child of feature-1
sc restack             # Rebases feature-2 onto new feature-1
```

### After Remote Changes

If your branch or its parents were updated remotely:

```bash
sc restack  # Fetches, fast-forwards, and rebases as needed
```

## Edge Cases

### Missing Parent Branch

If a parent branch no longer exists (usually because it was merged):

```bash
$ sc restack
Error: Parent branch 'feature-old' no longer exists. This usually means it was merged. Run 'sc sync' to update parent references.
```

**Solution:** Run `sc sync` to detect merged branches and update parent relationships.

### Legacy Branches

Branches created before `parent_revision` tracking will fall back to using `merge-base`:

```bash
merge_base = git.get_merge_base(branch, parent)
git.rebase_onto(parent, merge_base, branch)
```

This is less accurate but allows restacking older branches.

## Related Commands

- `sc sync` - Detect merged branches and clean up the stack
- `sc split` - Split commits while preserving stack structure
- `sc create` - Create a new branch with parent tracking

## Implementation Details

The restack logic is implemented in `/Users/patrick/github/patrick91/shortcake/shortcake/commands/restack.py`:

Key functions:
- `_needs_restack()` - Determines if a branch needs rebasing by comparing stored `parent_revision` with parent's current HEAD
- `_get_stack_from_current()` - Walks up to find ancestor branches
- `_get_descendant_branches()` - Walks down to find all descendants
- `_get_remote_ref()` - Resolves trunk branches to their remote refs (e.g., `origin/main`)

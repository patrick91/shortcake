# Sync Command

The `sync` command automatically maintains stacked branches by detecting merged parent branches and rebasing their children onto the appropriate base. It handles the common workflow where parent branches are merged into trunk, requiring child branches to be rebased.

## Purpose

When working with stacked branches, parent branches often get merged into the main branch (trunk). The `sync` command:

1. Detects which branches have been merged into trunk (using [squash merge detection](../squash-merge-detection.md))
2. Identifies child branches that need to be rebased
3. Rebases child branches onto their new parent (walking up the chain to find non-merged parents)
4. Updates branch metadata to reflect new parent relationships
5. Cleans up merged branches by deleting them

This automation saves significant time compared to manually rebasing each branch in a stack.

## Options

```bash
sc sync [OPTIONS]
```

### Flags

| Flag | Short | Description |
|------|-------|-------------|
| `--dry-run` | `-n` | Preview what would happen without making any changes |
| `--continue` | | Continue a sync operation after resolving rebase conflicts |
| `--abort` | | Abort the current rebase operation |

## Algorithm

The sync command follows these steps:

### 1. Pre-flight Checks

- Checks for existing rebase in progress (exits if found, unless using `--continue` or `--abort`)
- Verifies repository state and identifies the main branch

### 2. Fetch Remote Updates

If an `origin` remote exists:
- Fetches latest changes from origin
- Fast-forwards local branches that are behind their remote counterparts
- Updates metadata (`parent_revision`) for fast-forwarded branches

### 3. Detect Merged Branches

For each shortcake-tracked branch:
- Checks if it has been merged into `origin/main` (or local `main` if no remote)
- Uses three detection methods (see [squash merge detection](../squash-merge-detection.md)):
  1. Ancestor check (regular/rebase merges)
  2. Tree comparison (file state matching)
  3. Cherry-based detection (most reliable for squash merges)

### 4. Identify Branches Needing Rebase

For each non-merged branch:
- Checks if its parent was merged or no longer exists
- If yes, walks up the parent chain to find a new parent:
  - Skips over merged parents
  - Stops at the first non-merged parent or main branch
  - Uses `origin/main` as the rebase target when rebasing onto trunk

### 5. Topological Sorting

Sorts branches to rebase in topological order (parents before children) to ensure:
- Parent branches are rebased first
- Child branches rebase onto up-to-date parents
- Dependencies are respected

### 6. Perform Rebases

For each branch in topological order:
```bash
git rebase --onto <new_parent> <old_parent_sha> <branch>
```

Where:
- `new_parent`: The target branch (either a non-merged parent or `origin/main`)
- `old_parent_sha`: The commit SHA where the branch originally diverged
- `branch`: The branch being rebased

If a conflict occurs:
- The rebase is paused
- User must resolve conflicts manually
- Run `sc sync --continue` to resume or `sc sync --abort` to cancel

### 7. Update Metadata

For each successfully rebased branch:
- Updates the `parent` field to the new parent branch name
- Updates the `parent_revision` field to the new parent's commit SHA
- Ensures trunk branches reference local names (e.g., `main` not `origin/main`)

### 8. Clean Up Merged Branches

For each merged branch:
- Deletes the local branch using `git branch -d`
- Removes shortcake metadata for the branch
- Warns if deletion fails (e.g., branch checked out elsewhere)

### 9. Restore Working State

- If the original branch was merged, switches to main
- Otherwise, attempts to return to the original branch

## Examples

### Basic Usage

Sync all branches after merging a parent:

```bash
sc sync
```

Output:
```
Fetching from origin...
Detected merged branches:
  ✓ feature-part-1

Rebasing branches:
  • feature-part-2 onto origin/main... done
  • feature-part-3 onto feature-part-2... done

Updating branch parents:
  • feature-part-2: parent → main
  • feature-part-3: parent → feature-part-2

Cleaning up merged branches:
  • Deleted: feature-part-1

Sync complete!
```

### Preview Changes (Dry Run)

See what would happen without making changes:

```bash
sc sync --dry-run
```

Output:
```
Would fetch from origin...
Detected merged branches:
  ✓ feature-part-1

Would rebase the following branches:
  • feature-part-2: feature-part-1 → origin/main
  • feature-part-3: feature-part-1 → feature-part-2

Would update branch parents:
  • feature-part-2: feature-part-1 → main
  • feature-part-3: feature-part-1 → feature-part-2

Would delete merged branches:
  • feature-part-1
```

### Handling Conflicts

If a rebase encounters conflicts:

```bash
sc sync
```

Output:
```
Rebasing branches:
  • feature-part-2 onto origin/main... CONFLICT
Error: Rebase failed with conflicts

Resolve the conflicts, then run:
  sc sync --continue

Or abort with:
  sc sync --abort
```

Resolve conflicts manually:
```bash
# Fix conflicts in your editor
git add <resolved-files>
sc sync --continue
```

### Aborting a Sync

If you need to cancel a sync operation:

```bash
sc sync --abort
```

## Common Scenarios

### Scenario 1: Linear Stack with Merged Parent

**Before:**
```
main:       A---B---C (contains feature-1 via squash merge)
              \
feature-1:     D---E
                    \
feature-2:           F---G
```

**After `sc sync`:**
```
main:       A---B---C
                     \
feature-2:            F'---G' (rebased onto main)

(feature-1 deleted)
```

### Scenario 2: Multi-level Stack with Middle Branch Merged

**Before:**
```
main:       A---B---C (contains feature-2 via squash merge)
              \
feature-1:     D---E
                    \
feature-2:           F---G
                            \
feature-3:                   H---I
```

**After `sc sync`:**
```
main:       A---B---C
              \      \
feature-1:     D---E  \
                       \
feature-3:              H'---I' (rebased onto main, skipping merged feature-2)

(feature-2 deleted)
```

### Scenario 3: Fast-Forward from Remote

If a collaborator pushed updates to your branch:

**Before:**
```
Local:   feature-1 at commit E
Remote:  origin/feature-1 at commit F (ahead of E)
```

**After `sc sync`:**
```
Fetching from origin...
Fast-forwarded 1 branch(es) to match remote:
  • feature-1

Local:   feature-1 at commit F (matches remote)
```

## Technical Details

### Parent Revision Tracking

The sync command maintains a `parent_revision` field in branch metadata that stores the commit SHA where a branch diverged from its parent. This is crucial for:

1. **Rebase Operations**: Using `git rebase --onto` requires knowing the exact divergence point
2. **Orphaned Branches**: If a parent branch is deleted, the stored `parent_revision` allows rebasing to continue
3. **Fast-Forward Updates**: After fast-forwarding from remote, the new merge-base is calculated and stored

### Remote Branch Handling

When a remote is available:
- The sync command uses `origin/main` as the merge detection target (more up-to-date)
- Rebases onto `origin/main` when rebasing onto trunk
- Stores local branch names in metadata (e.g., `main` not `origin/main`)
- References `origin/<branch>` for trunk branches when calculating parent revisions

### Error Handling

The command handles various error conditions:
- **Uncommitted changes**: Exits with instructions to stash or commit
- **Rebase conflicts**: Pauses and provides continue/abort instructions
- **Missing parent**: Uses merge-base as fallback for rebase point
- **Remote fetch failures**: Warns but continues with local state
- **Branch deletion failures**: Warns but continues syncing other branches

## See Also

- [Squash Merge Detection](../squash-merge-detection.md) - How merged branches are detected
- `sc restack` - Manually rebase a single branch and its children
- `sc create` - Create new stacked branches

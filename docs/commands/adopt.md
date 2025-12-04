# Adopt Command

The `adopt` command adds shortcake tracking to an existing git branch, allowing you to manage it as part of your stacked branches workflow.

## Purpose

When you have existing git branches that weren't created with shortcake, the `adopt` command integrates them into shortcake's tracking system. This is useful when:
- You have pre-existing feature branches you want to manage with shortcake
- You created a branch using plain git and want to convert it to a shortcake-tracked branch
- You need to establish or update parent-child relationships between branches

## Options

- `[BRANCH]` - Branch name to adopt (optional, defaults to current branch)
- `--parent`, `-p` - Specify parent branch explicitly (optional, auto-detected if not provided)
- `--dry-run`, `-n` - Preview what would be adopted without making changes
- `--force`, `-f` - Re-adopt an already tracked branch, updating its parent

## Algorithm

The adopt command follows this flow:

### 1. Validation

- Verifies the target branch exists
- Checks that the branch is not a trunk branch (main/master)
- If the branch is already tracked and `--force` is not used, exits with an error

### 2. Parent Detection

If no parent is explicitly specified with `--parent`, the command automatically detects the best parent using `find_best_parent()`:

1. Gets all branches in the repository
2. Finds all branches that are ancestors of the target branch
3. Calculates the distance (number of commits) between each ancestor and the target
4. Sorts candidates by distance (closest first)
5. Prefers non-trunk branches over trunk branches
6. Falls back to main/master if no other suitable parent is found

The algorithm skips:
- The branch itself
- Branches pointing to the same commit (distance = 0)

### 3. Parent Validation

- Verifies the parent branch exists (whether auto-detected or manually specified)
- For trunk branches (main/master), uses `origin/<branch>` as the parent reference if the origin remote exists

### 4. Metadata Update

If not in dry-run mode:
- Stores the parent branch name in the branch metadata
- Stores the parent's current commit SHA as `parent_revision`
- This revision is used by other commands (like `restack`) to detect when the parent has changed

### 5. Output

Reports the adoption with branch name and parent information.

## Examples

### Adopt current branch with auto-detection

```bash
sc adopt
```

This adopts the current branch, automatically detecting its parent from git history.

### Adopt a specific branch

```bash
sc adopt feature-1
```

Adopts the branch `feature-1` with automatic parent detection.

### Adopt with explicit parent

```bash
sc adopt feature-2 -p feature-1
```

Adopts `feature-2` and explicitly sets `feature-1` as its parent.

### Preview without adopting

```bash
sc adopt --dry-run
```

Shows what would happen (including auto-detected parent) without actually adopting the branch.

### Update an existing branch's parent

```bash
sc adopt feature-1 -p main --force
```

Re-adopts `feature-1`, changing its parent to `main`. Useful when you want to restructure your branch stack.

## Implementation Details

The adopt command is implemented in `shortcake/commands/adopt.py` and uses:
- `GitRepo` for git operations (branch existence checks, ancestor checks, commit counting)
- `get_branch_metadata()` to check if a branch is already tracked
- `update_branch_metadata()` to store the parent and parent_revision
- `find_best_parent()` algorithm for intelligent parent detection

The metadata is stored in git config, allowing shortcake to track branch relationships independently of git's internal branch structure.

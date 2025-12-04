# Get Command

The `get` command fetches a branch and its entire stack from the remote repository and adopts them locally with shortcake tracking. This is useful for collaborating on stacked branches or picking up someone else's work.

## Command Syntax

```bash
sc get [TARGET] [OPTIONS]
```

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `TARGET` | - | Branch name or PR number to fetch (interactive mode if omitted) |
| `--mine` | `-m` | Only show PRs authored by you in interactive mode |
| `--downstack` | `-d` | Only fetch downstack branches (don't sync upstack) |
| `--force` | `-f` | Overwrite local branches with remote versions even if they diverge |

## Algorithm

The `get` command follows these steps to fetch and adopt a branch stack:

### 1. Target Resolution

**Interactive Mode** (no target provided):
- Fetches all open pull requests from GitHub
- If `--mine` is specified, filters to PRs authored by the current user
- Displays an interactive menu to select a PR
- Extracts the head branch name from the selected PR

**PR Number Mode** (target is numeric):
- Uses GitHub API to resolve the PR number to a branch name

**Branch Name Mode** (target is a string):
- Uses the provided branch name directly

### 2. Remote Fetch

```bash
git fetch origin
```

Fetches the latest refs and commits from the remote repository.

### 3. Main Branch Update

- Detects the main branch (main or master)
- If local main is behind remote main, fast-forwards it to match
- If currently on main, performs a merge with `--ff-only`
- If on another branch, updates the ref directly

### 4. Stack Discovery

The command analyzes the commit history to find all intermediate branches between main and the target branch:

1. Lists all remote branches from `origin`
2. For each remote branch:
   - Checks if it's an ancestor of the target branch
   - Checks if main is an ancestor of it (ensuring it's between main and target)
   - Calculates distance from main using commit count
3. Sorts branches by distance from main (closest first)
4. Returns ordered list: `[branch1, branch2, ..., target_branch]`

**Example:**
```
main ← branch-1 ← branch-2 ← branch-3 (target)
```

The stack would be: `[branch-1, branch-2, branch-3]`

### 5. Conflict Detection

Before modifying local branches (unless `--force` is used):
- Checks each branch in the stack that exists locally
- Compares local SHA with remote SHA
- If they differ and local is not an ancestor of remote (i.e., local has diverged):
  - Adds to conflicts list
- If conflicts exist, aborts and prompts user to use `--force` or resolve manually

### 6. Branch Creation and Adoption

For each branch in the stack (from bottom to top):

1. **Determine Parent**:
   - First branch in stack → parent is main
   - Subsequent branches → parent is the previous branch in stack

2. **Check if Already Up-to-Date**:
   - If metadata shows correct parent and local SHA matches remote SHA
   - Skip update and mark as already up to date

3. **Create or Update Local Branch**:
   - If branch doesn't exist locally: create it pointing to remote commit
   - If branch exists: update ref to match remote commit

4. **Calculate Parent Revision**:
   - Uses merge-base between `origin/{branch}` and parent ref
   - This establishes the divergence point for restack operations

5. **Update Metadata**:
   - Stores parent branch name
   - Stores parent_revision (merge-base SHA)
   - This enables shortcake tracking for the branch

### 7. Checkout Target Branch

- Switches to the target branch if not already on it
- Reports if checkout fails but doesn't abort

### 8. Restack Hint

- Checks if the bottom branch's parent_revision matches current main SHA
- If they differ, suggests running `restack` to update the stack to latest main

## Examples

### Interactive PR Selection

```bash
# Select from all open PRs
sc get

# Select from your own PRs only
sc get --mine
```

### Fetch by Branch Name

```bash
# Fetch feature-branch and its stack
sc get feature-branch
```

### Fetch by PR Number

```bash
# Fetch PR #123 and its stack
sc get 123
```

### Force Overwrite Diverged Branches

```bash
# Overwrite local changes with remote versions
sc get feature-branch --force
```

## Use Cases

### Collaborating on Stacked PRs

When a teammate has created a stack of PRs and you want to check them out:

```bash
sc get --mine
# Or if you know the branch name:
sc get their-feature-branch
```

All intermediate branches will be fetched and tracked automatically.

### Switching Machines

When working from a different machine and you want to continue work on a stack:

```bash
sc get my-feature-branch
```

The entire stack will be reconstructed locally with proper parent relationships.

### Reviewing PRs

When reviewing a PR that's part of a stack:

```bash
# Interactive selection
sc get

# Or by PR number
sc get 456
```

You'll get the full context of the stack, not just the single PR.

## Post-Fetch Workflow

After using `get`, you typically want to:

1. **Restack** (if branches are based on old main):
   ```bash
   sc restack
   ```

2. **Make changes** to the branch as needed

3. **Sync** when done to push changes and update the stack:
   ```bash
   sc sync
   ```

## Notes

- The command requires a remote named `origin` to be configured
- GitHub API access is required for interactive mode and PR number resolution
- The `--downstack` flag is defined but not currently used in the implementation
- Local branches are created/updated without performing checkouts (except the final target)
- The command preserves the exact commit SHAs from remote, not creating new commits

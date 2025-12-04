# Submit Command

The `submit` command pushes branches to the remote repository and creates or updates pull requests on GitHub. It handles stacked PRs by managing parent-child relationships and automatically updates PR descriptions with stack information.

## Command Syntax

```bash
sc submit [OPTIONS]
```

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--draft` | `-d` | Create pull requests as drafts |
| `--current` | `-c` | Only submit the current branch (ignore parents and children) |
| `--stack` | `-s` | Submit all branches in the stack (parents + current + children) |
| `--dry-run` | `-n` | Show what would be done without making any changes |
| `--force` | `-f` | Force push branches (override remote changes) and update PR descriptions |

## Default Behavior

By default (no flags), `submit` operates on the **downstack** - it submits all branches from the trunk (main) up to and including the current branch. This ensures parent branches are pushed first, so GitHub displays correct diffs for stacked PRs.

## Examples

```bash
# Submit parents + current branch (default)
sc submit

# Submit the entire stack (parents + current + children)
sc submit --stack

# Submit only the current branch
sc submit --current

# Create PRs as drafts
sc submit --draft

# Preview what would happen without making changes
sc submit --dry-run

# Force push all branches and update all PR descriptions
sc submit --force
```

## Algorithm

The submit command follows these steps:

### 1. Validation

1. Verify the current directory is a git repository
2. Check that the current branch is not the trunk (main/master)
3. Verify the branch is managed by shortcake (has parent metadata)
4. Ensure `origin` remote is configured
5. Extract GitHub repository owner and name from the remote URL

### 2. Branch Collection

Depending on the flags provided, collect branches to submit:

- **Default (no flags)**: Walk up from current branch to trunk, collecting all parent branches plus current branch (downstack)
- **`--current`**: Only the current branch
- **`--stack`**: Walk up to get parents, then walk down to get all children (full stack)

For each branch, store:
- Branch name
- Parent branch name
- Commit message (first line, used as PR title)
- Existing PR number and URL (if any)

### 3. Pre-flight Checks

1. Check for uncommitted changes (abort if found)
2. For each branch to submit, check if it needs restacking:
   - Determine the rebase target (use remote ref for trunk branches if available)
   - Compare branch metadata's `parent_revision` with current parent state
   - If restack needed, perform the rebase:
     ```
     git rebase --onto <new-parent> <parent_revision> <branch>
     ```
   - Update `parent_revision` in metadata after successful rebase
   - Handle conflicts by providing clear instructions to resolve manually

### 4. Branch Pushing

For each branch in the collection:

1. Compare local and remote commit SHAs to determine if push is needed
2. If branch needs pushing or `--force` is set:
   - Push to origin (with `--force-with-lease` by default, `--force` if flag set)
3. Handle push failures with clear error messages

### 5. PR Creation/Update

For each branch:

1. Determine the correct base branch:
   - If parent is a shortcake-managed branch: use parent as base
   - Otherwise: use parent (trunk)

2. Check if PR already exists:
   - If branch has stored `pr_number` in metadata, fetch that PR
   - Otherwise, query GitHub API for PRs from this branch

3. Create or update PR:
   - **New PR**: Create with commit message as title, empty body, and correct base
   - **Existing PR**: Update base branch if it changed
   - Mark as draft if `--draft` flag is set

4. Update branch metadata with PR number and URL

### 6. Stack Description Updates

After all PRs are created/updated:

1. Collect the **full stack** (all parents + current + all descendants)
2. Fetch PR states (open/closed/merged) for all branches in stack
3. For each branch in the full stack with an open PR:
   - Generate stack description showing all PRs in the stack
   - Update or insert the stack section in the PR body
   - Mark current PR with arrow (⬅), merged PRs with checkmark (✅)

**Stack Description Format:**

```markdown
<!-- shortcake stack start -->
## Stack
- #123 ⬅  (current branch)
- #122
- ~~#121~~ ✅  (merged)
- main
<!-- shortcake stack end -->
```

The markers (`<!-- shortcake stack start -->` and `<!-- shortcake stack end -->`) allow the command to:
- Replace existing stack sections on subsequent runs
- Preserve user-written PR description content
- Automatically update stack status as PRs are merged

### 7. Summary

Display a summary of all submitted PRs with their URLs.

## How Branch Collection Works

### Walking Up the Stack (`_get_stack_branches`)

Starting from the current branch, walks up to the trunk by following parent metadata:

```
feature-c (current)
    ↑
feature-b (parent)
    ↑
feature-a (parent)
    ↑
main (trunk - no parent metadata, stops here)
```

Returns: `[feature-a, feature-b, feature-c]` (bottom to top)

### Walking Down the Stack (`_get_descendant_branches`)

Starting from a branch, uses breadth-first traversal to find all children:

```
feature-b (start)
    ↓
feature-c (child)
    ↓
feature-d (child of feature-c)
```

Returns: `[feature-c, feature-d]` (topological order)

## Restacking Logic

Before pushing, the command checks if each branch needs restacking:

1. Get the branch's stored `parent_revision` (the commit SHA it was last rebased onto)
2. Get the current parent commit SHA (use remote ref for trunk, local ref for branches)
3. Compare: if different, restack is needed
4. Perform rebase: `git rebase --onto <current-parent> <parent_revision> <branch>`
5. Update metadata with new `parent_revision`

This ensures branches are always up-to-date with their parents before creating/updating PRs.

## PR Body Management

The command preserves user-written PR descriptions while managing stack information:

1. **First submission**: Stack section is prepended to the PR body
2. **Subsequent submissions**: Stack section is replaced using HTML comment markers
3. **User content**: Any text outside the markers is preserved
4. **Force flag**: Updates all PRs in the stack, not just newly submitted ones

## Error Handling

The command handles various error scenarios:

- **Uncommitted changes**: Aborts with instructions to commit or stash
- **Rebase conflicts**: Provides step-by-step resolution instructions
- **Push failures**: Displays git error and exits
- **GitHub API errors**: Shows error message and exits
- **No remote**: Detects and reports missing origin remote
- **Not on shortcake branch**: Prompts to use `adopt` command first

## Implementation Details

### Key Functions

- **`_get_stack_branches(git, start_branch)`**: Walks up from current branch to trunk, returns ordered list
- **`_get_descendant_branches(git, branch)`**: Walks down to find all children, returns topological order
- **`_generate_stack_description(branches, current_branch, main_branch, pr_states)`**: Creates markdown stack visualization
- **`_update_pr_body_with_stack(existing_body, stack_description)`**: Inserts or replaces stack section using markers

### Data Structure

The `BranchSubmitInfo` dataclass encapsulates branch information:

```python
@dataclass
class BranchSubmitInfo:
    name: str              # Branch name
    parent: str            # Parent branch name
    commit_message: str    # First line of commit (PR title)
    pr_number: int | None  # Existing PR number
    pr_url: str | None     # Existing PR URL
```

### Metadata Updates

The command updates branch metadata in two scenarios:

1. After successful rebase: updates `parent_revision`
2. After PR creation/update: updates `pr_number` and `pr_url`

This metadata persistence allows subsequent `submit` calls to be idempotent and efficient.

## Integration with Other Commands

- **`restack`**: Submit uses the same restacking logic before pushing
- **`sync`**: After syncing merged branches, submit creates PRs for the remaining stack
- **`adopt`**: Branches must be adopted before they can be submitted
- **`create`**: New branches created with `create` can immediately be submitted

## Performance Considerations

- **Dry run mode**: Use `--dry-run` to preview actions without API calls or git operations
- **Current flag**: Use `--current` to skip parent branches if they're already submitted
- **Force flag**: Rebuilds all stack descriptions, slower but ensures consistency
- **Smart pushing**: Only pushes branches that have changed (compares SHAs)
- **Base updates**: Only updates PR base if it actually changed

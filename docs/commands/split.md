# Split Command

The `split` command helps you break up a large branch into smaller, focused branches organized as a stack. This is useful when you've made many changes on a single branch and want to create smaller, reviewable PRs.

## Command Description

Split takes an existing shortcake-managed branch and divides its changes into multiple stacked branches. You can interactively select which changes go into each new branch, creating a clean stack where each branch builds on the previous one.

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--by-hunk` | `-h` | Start an interactive split session by selecting hunks |
| `--continue` | - | Continue the split by creating a branch from staged changes |
| `--abort` | - | Abort the current split operation and restore original state |
| `--no-verify` | `-n` | Skip pre-commit and commit-msg hooks when creating commits |

## Algorithm

### Starting a Split (`--by-hunk`)

1. **Validation:**
   - Verify not on main/master branch
   - Verify branch is managed by shortcake (has parent metadata)
   - Check for state file to ensure no split already in progress

2. **Warning about children:**
   - If the branch has child branches, warn user they'll need manual updates
   - Prompt for confirmation to continue

3. **Save state:**
   - Store original branch name, commit SHA, parent branch, and metadata
   - Store parent revision (where branch diverged from parent)
   - Store original commit message (for potential reuse)
   - Save to `.git/shortcake-split-state.json`

4. **Reset changes:**
   - Perform soft reset to parent revision (undoes commits but keeps changes)
   - Unstage all changes with `git reset HEAD`
   - All file changes are now unstaged and ready for selection

5. **Wait for user:**
   - Display instructions for staging changes and continuing

### Continuing a Split (`--continue`)

1. **Validation:**
   - Check that state file exists
   - Verify there are staged changes to commit

2. **Branch naming:**
   - For the first branch: Ask if user wants to reuse the original branch name
     - If yes: Use original branch name and offer original commit message
     - If no: Prompt for new branch name
   - For subsequent branches: Always prompt for new branch name
   - If no name provided: Generate from commit message
   - If name already exists: Append counter suffix

3. **Create commit:**
   - Commit staged changes with provided message
   - Use `--no-verify` flag if specified

4. **Create/rename branch:**
   - Rename current temporary branch to the target name
   - Determine parent branch:
     - First branch in split: Parent is original branch's parent
     - Subsequent branches: Parent is previous created branch
   - Update branch metadata with parent and parent_revision

5. **Handle remaining changes:**
   - Check for unstaged changes or untracked files
   - If changes remain:
     - Create new temporary branch (`split-wip-N`)
     - Prompt user to stage next set of changes
   - If no changes remain:
     - Call `_finish_split()` to complete the operation

### Finishing a Split

1. **Cleanup temporary branches:**
   - Checkout the last created branch
   - Delete all `split-wip-*` branches

2. **Update child branches:**
   - If original branch name was reused: Children point to that branch
   - If original branch name was not used:
     - Warn that existing PR will be orphaned
     - Update children to point to last branch in stack

3. **Complete:**
   - Delete state file
   - Display summary of created branches and their parents

### Aborting a Split (`--abort`)

1. **Restore state:**
   - Load state from `.git/shortcake-split-state.json`
   - Checkout original branch
   - Hard reset to original commit
   - Restore original metadata

2. **Cleanup:**
   - Delete state file
   - Display confirmation message

## Usage Examples

### Basic workflow

Start a split on a branch with multiple changes:

```bash
# Start the split
sc split --by-hunk

# Output:
# Split started for branch 'feature-branch' (5 commit(s))
#
# All changes are now unstaged. To split:
#   1. Stage changes for the first branch:
#      git add -p           # Interactive hunk selection
#      git add <files>      # Or add specific files
#
#   2. Create the branch:
#      shortcake split --continue
#
#   3. Repeat until all changes are committed
#
# To abort: shortcake split --abort
```

Stage changes for the first branch:

```bash
# Interactively select hunks
git add -p

# Or add specific files
git add src/auth.py src/models.py
```

Create the first branch:

```bash
sc split --continue

# Prompts:
# Use original branch 'feature-branch'? [Y/n]: n
# Branch name: auth-changes
# Commit message: Add authentication logic
#
# Output:
# Created branch: auth-changes
#
# Remaining changes detected.
# Stage changes for the next branch, then run: shortcake split --continue
```

Continue with remaining changes:

```bash
# Stage next set of changes
git add src/api.py

# Create next branch
sc split --continue

# Prompts:
# Branch name: api-updates
# Commit message: Update API endpoints
#
# Output:
# Created branch: api-updates
#
# Split complete! Created branches:
#   • auth-changes (parent: main)
#   • api-updates (parent: auth-changes)
```

### Reusing the original branch name

If you want to keep the original branch name (and PR if it exists):

```bash
sc split --by-hunk

# Stage first set of changes
git add src/auth.py

sc split --continue

# Prompts:
# Use original branch 'feature-branch'? [Y/n]: y
# Commit message [Original commit message]: Add authentication logic
#
# Output:
# Created branch: feature-branch
```

This preserves the original branch name for the first split, keeping any existing PR intact.

### Aborting a split

If you change your mind or make a mistake:

```bash
sc split --abort

# Output:
# Split aborted, restored original state
```

This performs a hard reset to the original commit and restores all metadata.

### Skipping commit hooks

To skip pre-commit and commit-msg hooks during the split:

```bash
sc split --by-hunk

# Stage changes
git add src/

sc split --continue --no-verify
```

## State Management

The split command maintains state in `.git/shortcake-split-state.json`:

```json
{
  "original_branch": "feature-branch",
  "original_commit": "abc123...",
  "original_parent": "main",
  "original_notes": {"parent": "main", "parent_revision": "def456..."},
  "original_message": "Original commit message",
  "children": ["child-branch"],
  "created_branches": ["auth-changes", "api-updates"],
  "parent_revision": "def456...",
  "original_branch_used": true
}
```

This state allows the command to:
- Resume after interruption
- Track which branches have been created
- Restore original state on abort
- Know whether the original branch name has been reused
- Update child branch relationships correctly

## Branch Relationships

The split command creates a stack where each new branch builds on the previous:

```
Before split:
main --- A --- B --- C --- D --- E (feature-branch)

After split:
main --- A --- B (part-1)
              \
               C --- D (part-2)
                    \
                     E (part-3)
```

Each branch's metadata tracks its parent and parent_revision, maintaining the stack structure that shortcake uses for syncing and restacking.

## Handling Child Branches

If the branch being split has children, the split command:
1. Warns the user before starting
2. Updates all children to point to the final branch in the stack
3. If the original branch name was reused, children point to that branch
4. If the original branch name was not used, warns about orphaned PRs

## Implementation Details

### Temporary Branches

During the split process, temporary branches named `split-wip-N` are created to hold remaining changes between `--continue` calls. These are automatically cleaned up when the split completes or is aborted.

### Commit Message Handling

- If a commit message is provided via prompt, it's used directly
- If left empty, git opens the default editor for composing the message
- The original commit message is offered as a default when reusing the original branch name

### Branch Name Generation

If no branch name is provided, the command generates one from the commit message using the same logic as the `create` command.

### Uniqueness

Branch names are automatically made unique by appending a counter suffix if a conflict is detected (e.g., `auth-changes-1`, `auth-changes-2`).

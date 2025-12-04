# Edit Command

The `edit` command (also available as `modify`) allows you to make changes to your current stack by amending existing commits or creating new ones. It's designed to streamline the workflow of iterating on code changes.

## Purpose

When working on a stack of changes, you often need to:
- Make quick fixes to the current commit
- Add new commits to the stack
- Update commit messages

The `edit` command handles all these cases with a simple interface that integrates with pre-commit hooks.

## Options

### `--message / -m <text>`
Create a new commit with the specified message instead of amending the current commit.

**Default:** None (amends the current commit)

### `--reword / -r`
Edit only the commit message without requiring staged changes. Opens your editor to modify the message.

**Default:** False

### `--no-verify / -n`
Skip pre-commit and commit-msg hooks.

**Default:** False

## Usage

### Basic Amendment
Amend the current commit with staged changes:

```bash
# Make changes
git add .

# Amend the commit (reuses previous message)
sc edit
```

### Create New Commit
Add a new commit to the stack:

```bash
# Make changes
git add .

# Create new commit
sc edit -m "Add validation logic"
```

### Reword Commit Message
Edit just the commit message:

```bash
# Opens editor to modify the message
sc edit --reword
```

### Skip Hooks
Bypass pre-commit hooks (use sparingly):

```bash
git add .
sc edit --no-verify
```

## Algorithm

The `edit` command follows this flow:

### 1. Reword Mode (`--reword`)

If the `--reword` flag is set:
1. Call `git commit --amend` with edit mode to open the editor
2. Display the updated commit message
3. Exit (no staged changes required)

### 2. Staged Changes Check

If not in reword mode:
1. Check if there are any staged changes
2. If no staged changes exist:
   - Display error message
   - Suggest using `git add` or `--reword` flag
   - Exit with error code 1

### 3. Create New Commit (`--message` provided)

If a message is provided:
1. Create a new commit with the given message
2. Apply `--no-verify` flag if set
3. Display confirmation: "Created commit: {message}"

### 4. Amend Existing Commit (default)

If no message is provided:
1. Amend the current commit with staged changes
2. Reuse the existing commit message (no editor opens)
3. Apply `--no-verify` flag if set
4. Display confirmation: "Successfully amended the commit"

**Note:** Branch metadata (parent relationships, etc.) is stored by branch name in JSON, so it's automatically preserved during amends without manual save/restore.

### 5. Hook Failure Handling

If pre-commit hooks fail or modify files:
1. Display a blank line for readability
2. Show error message indicating hooks failed or modified files
3. Provide guidance: "Review the changes, stage them, and try again."
4. Exit with error code 1

## Implementation Details

The `edit` and `modify` commands are aliases that both call the internal `_do_edit()` function with the same parameters. This provides flexibility for users familiar with different workflows.

The command integrates with:
- **Pre-commit hooks:** Runs by default, can be skipped with `--no-verify`
- **Commit-msg hooks:** Runs by default, can be skipped with `--no-verify`
- **GitRepo class:** Uses the internal git wrapper for all operations

## Common Workflows

### Iterative Development
```bash
# Initial commit
git add .
sc edit -m "Add feature"

# Realize you need to fix something
# Make changes...
git add .
sc edit  # Amend the commit

# Add another change
# Make changes...
git add .
sc edit -m "Add tests"
```

### Fix Typo in Message
```bash
sc edit --reword
# Editor opens, fix typo, save and exit
```

### Quick Fix with Hook Bypass
```bash
# Emergency fix, skip formatting hooks
git add .
sc edit --no-verify
```

## Error Handling

The command provides clear error messages for common issues:

- **No staged changes:** Reminds you to use `git add` and suggests `--reword` for message-only edits
- **Hook failures:** Shows hook output and explains that files may have been modified
- **Git errors:** Displays the underlying git error message

## See Also

- `sc restack` - Rebase your stack after editing commits
- `sc sync` - Sync your stack with the trunk branch
- `sc split` - Split a commit into multiple commits

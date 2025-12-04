# Create Command

The `create` command creates a new branch with a commit, automatically generating the branch name from the commit message. It's the primary way to add new changes to your stack.

## Usage

```bash
sc create [OPTIONS]
```

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--no-verify` | `-n` | Skip pre-commit and commit-msg hooks |
| `--gitmoji` | `--gm` | Select a gitmoji to prefix the commit message |
| `--claude` | `-c` | Use Claude AI to generate the commit message from staged changes |
| `--insert` | `-i` | Insert the new branch in the middle of the stack, updating children to point to it |

## Basic Workflow

1. Stage your changes: `git add <files>`
2. Run `sc create`
3. Write your commit message in the opened editor
4. The branch is automatically created with a name derived from the commit message

## Branch Name Generation

Branch names are automatically generated from commit messages using these rules:

1. Convert to lowercase
2. Replace spaces with hyphens
3. Remove special characters (or keep emojis if configured)
4. Collapse consecutive hyphens
5. Limit to 50 characters maximum

**Example:**
- Commit: `Add user authentication feature`
- Branch: `add-user-authentication-feature`

**Emoji handling:**
Control whether emojis are kept in branch names using the config:
```bash
sc config set keep_emoji true   # Keep emojis in branch names
sc config set keep_emoji false  # Remove emojis (default)
```

## Algorithm Flow

### Standard Create Flow

1. **Validation**
   - Get the current branch (this will be the parent)
   - Check if git repository is valid

2. **Create Temporary Branch**
   - Generate a temporary branch name: `temp-shortcake-{timestamp}`
   - Create and checkout the temporary branch
   - This allows the commit to be created before we know the final branch name

3. **Create Commit**
   - Open the configured editor for the commit message (or use generated message if `--claude` is used)
   - If commit is aborted or fails, clean up the temporary branch and exit
   - If `--no-verify` is specified, skip git hooks

4. **Generate Final Branch Name**
   - Extract the commit message from the just-created commit
   - Generate a valid branch name using the branch name generation rules
   - Rename temporary branch to the final name

5. **Store Metadata**
   - Record the parent branch in metadata
   - Store `parent_revision` (the SHA of the parent branch tip)
   - For trunk branches (main/master), use `origin/{branch}` as the reference if available

6. **Output**
   - Display the created branch name
   - Display the commit message

### Insert Mode Flow (--insert flag)

When using `--insert`, the command performs additional steps after the standard flow:

7. **Update Child Branches**
   - Get all children of the original (parent) branch
   - Filter out the newly created branch
   - For each child branch:
     - Update its parent metadata to point to the new branch
     - Display the update to the user

8. **Restack Reminder**
   - Inform the user to run `restack` to rebase child branches onto the new branch

**Example stack transformation:**

Before `--insert`:
```
main
  └── feature-1
        └── feature-2
```

After `sc create --insert` (on feature-1):
```
main
  └── feature-1
        └── new-feature (new)
              └── feature-2 (parent updated)
```

Run `sc restack` to rebase feature-2:
```
main
  └── feature-1
        └── new-feature
              └── feature-2 (rebased)
```

## Claude Integration (--claude flag)

The `--claude` flag uses the Claude CLI to generate commit messages from staged changes.

**Requirements:**
- Claude CLI must be installed and authenticated
- Install from: https://claude.ai/code

**How it works:**

1. **Check for Staged Changes**
   - Verify there are staged changes to analyze
   - Exit with error if nothing is staged

2. **Verify Claude CLI**
   - Check if `claude` command is available in PATH
   - Check common installation locations (`~/.claude/local/claude`)
   - Exit with error if not found

3. **Generate Message**
   - Get the staged diff using `git diff --staged`
   - Send the diff to Claude with a prompt requesting a concise commit message
   - Rules sent to Claude:
     - First line max 72 characters
     - Use imperative mood
     - Be specific but concise
     - If `--gitmoji` is also used, include a gitmoji prefix

4. **Review and Edit**
   - Display the generated message
   - Pre-fill the editor with the generated message
   - User can review, edit, or discard before committing

**Example:**

```bash
# Stage changes
git add src/auth.py

# Generate commit message with Claude
sc create --claude

# Output:
# Generating commit message with Claude...
# Generated: Add JWT token validation to authentication
# (editor opens with pre-filled message)
```

**Combining with gitmoji:**

```bash
sc create --claude --gitmoji
# Claude will generate a message with a gitmoji prefix like:
# ✨ Add JWT token validation to authentication
```

## Gitmoji Integration (--gitmoji flag)

The `--gitmoji` flag allows you to select an emoji from the gitmoji convention to prefix your commit message.

**How it works:**

1. Opens an interactive picker with common gitmoji options
2. Selected emoji is used as a prefix in the editor
3. User writes the rest of the commit message

**Note:** When combined with `--claude`, Claude generates the complete message with emoji, so manual gitmoji selection is skipped.

## Examples

### Basic Usage

```bash
# Stage your changes
git add src/features/auth.py

# Create a new branch and commit
sc create
# Opens editor, you write: "Add user authentication"
# Creates branch: add-user-authentication
```

### Skip Git Hooks

```bash
# Useful when you want to bypass pre-commit hooks
sc create --no-verify
```

### Using Gitmoji

```bash
git add README.md
sc create --gitmoji
# Select 📝 from the picker
# Write: "Update installation instructions"
# Creates commit: "📝 Update installation instructions"
```

### AI-Generated Commit Messages

```bash
git add src/api.py tests/test_api.py
sc create --claude
# Claude analyzes the diff and suggests:
# "Add rate limiting to API endpoints"
# Review/edit in editor, then save
```

### Insert in Middle of Stack

```bash
# Current stack: main -> feature-1 -> feature-2
# You're on feature-1 and want to add a commit between feature-1 and feature-2

git add src/utils.py
sc create --insert
# Write commit message: "Add helper utilities"
# New stack: main -> feature-1 -> add-helper-utilities -> feature-2
# Run 'sc restack' to rebase feature-2
```

### Combining Options

```bash
# Use Claude to generate a gitmoji-prefixed message, skip hooks
sc create --claude --gitmoji --no-verify
```

## Error Handling

The command handles various error scenarios:

**No staged changes (with --claude):**
```
Error: No staged changes. Use 'git add' to stage files first.
```

**Commit aborted:**
```
Error: Commit aborted or failed. No changes were made.
```
- The temporary branch is automatically cleaned up
- You're returned to your original branch

**Empty commit message:**
```
Error: Commit message cannot be empty
```

**Claude CLI not found:**
```
Error: Claude CLI not found. Install it from: https://claude.ai/code
```

**Invalid branch name:**
```
Error: Could not generate a valid branch name from the commit message
```

## Implementation Notes

### Temporary Branch Strategy

The command uses a temporary branch strategy to handle the chicken-and-egg problem: we need the commit message to generate the branch name, but we need a branch to create the commit on.

1. Create temporary branch with timestamp: `temp-shortcake-1234567890`
2. Create the commit on the temporary branch
3. Read the commit message
4. Rename the branch to the final name

**Benefits:**
- Atomic operation - if commit fails, nothing changes
- Clean error handling - easy to rollback
- No orphaned commits

### Parent Tracking

The command stores metadata for stack management:

```python
update_branch_metadata(
    branch_name,
    parent=original_branch,
    parent_revision=git.get_commit_sha(parent_ref)
)
```

- `parent`: The branch this was created from
- `parent_revision`: The SHA of the parent at creation time
- This enables `restack` to detect when rebasing is needed

### Claude CLI Integration

The integration supports multiple installation methods:

1. Check PATH for `claude` command
2. Check common installation location: `~/.claude/local/claude`
3. Fallback to `claude` command

Timeout is set to 60 seconds to prevent hanging on large diffs.

## Related Commands

- `restack`: Rebase branches after using `--insert`
- `sync`: Push branches and sync with remote
- `split`: Split a commit into multiple branches

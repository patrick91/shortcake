# ls Command

The `ls` command lists all shortcake-managed branches in a visual tree structure, showing their parent-child relationships and metadata.

## Purpose

`ls` provides a clear overview of all branches being tracked by shortcake, displaying:
- Branch hierarchy (parent-child relationships)
- Current branch indicator
- Pull request information (if available)
- Latest commit information
- Time since last commit

This helps you visualize your stack structure and understand the state of all managed branches at a glance.

## Usage

```bash
sc ls
```

The command takes no options or arguments.

## Output Format

The output displays branches in a vertical tree structure:

```
│ ◉ feature-branch-tip #123 (current)
│ │  24 hours ago
│ │  a1b2c3d - Add new feature implementation
│ │
├─┘
│ ◯ feature-branch-base
│ │  2 days ago
│ │  e4f5g6h - Initial setup for feature
│ │
├─┘
│
◯ main
```

### Symbols

- `◉` - Current branch marker
- `◯` - Non-current branch marker
- `│` - Vertical connector showing parent-child relationship
- `├─┘` - Stack merge indicator (shows where a stack merges back into its parent)
- `#123` - Pull request number (clickable link when terminal supports it)

### Information Displayed

For each branch:
1. Branch name (in cyan if current, blue otherwise)
2. PR number and URL (if available)
3. Relative time since last commit (e.g., "24 hours ago", "2 days ago")
4. Latest commit hash (abbreviated to 7 characters) and message (truncated to 50 characters)

## Algorithm

The `ls` command follows this flow:

### 1. Initialize Git Repository

```python
git = GitRepo()
```

Gets a handle to the current git repository. Exits with error if not in a valid git repository.

### 2. Retrieve Shortcake-Managed Branches

```python
branches = _get_shortcake_branches(git)
```

- Retrieves all branch metadata stored in git notes by shortcake
- For each tracked branch:
  - Gets the current branch name to mark it in the output
  - Retrieves branch metadata (parent, PR number, PR URL)
  - Fetches commit information:
    - Latest commit SHA (shortened to 7 characters)
    - Commit message
    - Commit date
  - Creates a `BranchDisplayInfo` object with all this data

If no shortcake-managed branches exist, displays a helpful message suggesting to use `create` or `adopt` commands.

### 3. Build Tree Visualization

```python
tree_lines = _build_tree_lines(branches, git)
```

This is the core visualization logic:

#### a. Build Data Structures

- Creates a map of branch names to branch objects
- Builds a parent-child relationship map (which children belong to each parent)
- Sorts children alphabetically for consistent output

#### b. Identify Root Parent (Trunk)

Finds the trunk branch by looking for a parent that exists in metadata but is not itself tracked by shortcake (typically `main` or `master`).

#### c. Render Stacks Top-to-Bottom

For each stack of branches:

1. **Find Stack Tip**: Starting from a trunk child, follow parent-child relationships to find the topmost branch
2. **Collect Stack Branches**: Walk from tip down to trunk, collecting all branches
3. **Render Each Branch**:
   - Branch line with marker (◉ or ◯), name, and PR info
   - Relative time line (e.g., "24 hours ago")
   - Commit info line (hash and message)
   - Spacing line
4. **Add Stack Delimiter**: `├─┘` to show where the stack merges back

#### d. Add Trunk at Bottom

Displays the trunk branch (e.g., `main`) at the bottom of the tree.

### 4. Display Output

```python
for line in tree_lines:
    console.print(line)
```

Prints each line using Rich console for colored, formatted output with support for clickable PR links.

## Time Formatting

Commit dates are converted to human-readable relative times:

- Less than 1 minute: "just now"
- Less than 1 hour: "X minutes ago"
- Less than 1 day: "X hours ago"
- Less than 1 week: "X days ago"
- Less than 1 month: "X weeks ago"
- 1 month or more: "X months ago"

## Examples

### Single Stack

```bash
$ sc ls
│ ◉ fix-bug-123 #456 (current)
│ │  2 hours ago
│ │  abc1234 - Fix null pointer exception in handler
│ │
├─┘
│ ◯ refactor-api
│ │  1 day ago
│ │  def5678 - Refactor API endpoint structure
│ │
├─┘
│
◯ main
```

Shows a two-branch stack (`refactor-api` → `fix-bug-123`) on top of `main`.

### Multiple Stacks

```bash
$ sc ls
│ ◯ feature-a-final
│ │  3 days ago
│ │  ghi9012 - Final touches on feature A
│ │
├─┘
│ ◉ feature-b-wip (current)
│ │  1 hour ago
│ │  jkl3456 - Work in progress on feature B
│ │
├─┘
│
◯ main
```

Shows two separate stacks both branching from `main`.

### No Managed Branches

```bash
$ sc ls
No shortcake-managed branches found
Use 'sc create' to create a new stack or 'sc adopt' to track existing branches
```

Displays when shortcake hasn't tracked any branches yet.

## Technical Details

### Branch Detection

Only branches with shortcake metadata (stored in git notes) are displayed. Regular git branches without shortcake tracking are ignored.

### Parent Resolution

The parent relationship is determined from shortcake metadata, not from git's merge-base. This allows shortcake to maintain logical stack relationships even after rebases or other git operations.

### Error Handling

If a branch in metadata no longer exists in the repository (e.g., deleted locally), the command gracefully handles the `GitError` and continues processing other branches.

## See Also

- `create` - Create a new branch and add it to shortcake tracking
- `adopt` - Track an existing branch with shortcake
- `sync` - Synchronize and clean up branch stacks

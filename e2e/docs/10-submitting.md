# Submitting Pull Requests

## The `sc submit` Command

The `sc submit` command pushes branches and creates/updates GitHub Pull Requests for the current stack. It:

1. Pushes all branches in the stack to origin
2. Creates PRs for branches that don't have them
3. Updates PR bases when parents change
4. Adds stack visualization to PR descriptions

## Prerequisites

Before using `sc submit`, you need:
- A GitHub token (via `gh auth login` or `GH_TOKEN` environment variable)
- An origin remote pointing to GitHub

## Basic Usage

### Setup: Create a Stack

```console
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
```

### Dry Run

Use `--dry-run` to preview what would be submitted:

```console
$ sc submit --dry-run
Would submit 1 branch(es):
  add-feature
```

## Command Options

- `--draft` / `-d`: Create draft PRs
- `--dry-run` / `-n`: Preview without making changes

## Stack Visualization

When submitting a stack, each PR description is automatically updated with a stack visualization:

```markdown
<!-- shortcake:start -->
## Stack

- #45 (`feature-c`)
- **#44** (`feature-b`) <-- this PR
- #43 (`feature-a`)
<!-- shortcake:end -->

[Your original PR description]
```

The visualization shows:
- All PRs in the stack
- Which PR you're currently viewing (marked with `**bold**`)
- The parent-child relationships

## Error Handling

### No GitHub Token

If no token is found, submit will show an error:

```
Error: No GitHub token found. Run 'gh auth login' or set GH_TOKEN environment variable.
```

### Untracked Branch

If the current branch is not tracked by shortcake:

```
Error: Branch 'my-branch' is not tracked by shortcake. Use 'sc adopt' to track it first.
```

### No Remote

If no origin remote is configured:

```
Error: No origin remote configured
```

## Token Resolution

`sc submit` looks for a GitHub token in this order:

1. `GH_TOKEN` environment variable
2. `GITHUB_TOKEN` environment variable
3. `~/.config/gh/hosts.yml` (gh CLI config)
4. `gh auth token` command output

## Notes

- PRs are created with the first line of the branch's HEAD commit as the title
- When a PR already exists, only the base and description are updated
- Stack visualization is preserved - your original PR description is kept
- Force push is used to update branches (be careful with shared branches)

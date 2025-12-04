# Shortcake Documentation

Shortcake is a CLI for managing stacked pull requests.

## Commands

| Command | Description |
|---------|-------------|
| [create](commands/create.md) | Create a new branch with a commit in the stack |
| [edit](commands/edit.md) | Edit the current stack by amending or creating commits |
| [split](commands/split.md) | Split a branch into multiple smaller branches |
| [submit](commands/submit.md) | Push branches and create/update PRs on GitHub |
| [sync](commands/sync.md) | Sync branches after parent branches are merged |
| [restack](commands/restack.md) | Rebase stacked branches onto updated parents |
| [ls](commands/ls.md) | List all shortcake-managed branches |
| [nav](commands/nav.md) | Navigation commands (up, down, top, bottom, checkout) |
| [get](commands/get.md) | Fetch a branch and its stack from remote |
| [adopt](commands/adopt.md) | Adopt existing branches into shortcake tracking |
| [move](commands/move.md) | Move a branch to a different parent |
| [config](commands/config.md) | Manage shortcake configuration |

## Technical Details

| Topic | Description |
|-------|-------------|
| [Squash Merge Detection](squash-merge-detection.md) | How shortcake detects squash-merged branches |

## Workflow Overview

### Creating a Stack

```bash
# Create the first branch
git add .
sc create

# Create a second branch on top
git add .
sc create

# View your stack
sc ls
```

### Submitting PRs

```bash
# Submit the entire stack
sc submit

# Submit only the current branch
sc submit --current
```

### Syncing After Merges

```bash
# After a parent PR is merged on GitHub
sc sync
```

### Navigating the Stack

```bash
sc up       # Move to child branch
sc down     # Move to parent branch
sc top      # Move to top of stack
sc bottom   # Move to bottom of stack
```

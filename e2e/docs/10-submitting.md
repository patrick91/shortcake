# Submitting Pull Requests

## The `sc submit` Command

The `sc submit` command pushes branches and creates/updates GitHub Pull Requests for the current stack. It:

1. Pushes all branches in the stack to origin
2. Creates PRs for branches that don't have them
3. Updates PR bases when parents change
4. Adds stack visualization to PR descriptions

## Prerequisites

Before using `sc submit`, you need:
- A GitHub token (via `gh auth login` or `GH_TOKEN` environment variable), unless using `--stealth`
- An origin remote

## Dry Run

Use `--dry-run` to preview what would be submitted:

```console
$ # github: setup-mock-with-remote
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ sc submit --dry-run
Would submit 1 branch(es):
  add-feature (create new PR)
```

## Creating a New PR

```console
$ # reset-to-main
$ # github: setup-mock-with-remote
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ sc submit
Pushing 'add-feature'...
  Creating PR for 'add-feature'...
  Created PR #1: https://github.com/test/repo/pull/1

Created 1 PR(s)
```

## Creating a Draft PR

```console
$ # reset-to-main
$ # github: reset-state
$ echo "draft code" > draft.py && git add draft.py
$ sc create -m "Draft feature"
Created branch 'draft-feature' from 'main'
$ sc submit --draft
Pushing 'draft-feature'...
  Creating PR for 'draft-feature'...
  Created PR #1: https://github.com/test/repo/pull/1

Created 1 PR(s)
```

## Updating an Existing PR

When a PR already exists for a branch, submit updates it instead of creating a new one:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ # github: add-pr add-feature 42 main
$ sc submit
Pushing 'add-feature'...

Updated 1 PR(s)
```

## Submitting a Stack of PRs

When you have stacked branches, submit creates PRs for the entire stack:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ sc submit
Pushing 'feature-a'...
  Creating PR for 'feature-a'...
  Created PR #1: https://github.com/test/repo/pull/1
Pushing 'feature-b'...
  Creating PR for 'feature-b'...
  Created PR #2: https://github.com/test/repo/pull/2

Created 2 PR(s)
```

## Skipping Merged PRs

Branches with merged PRs are skipped during submit:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ # github: add-pr feature-a 10 main
$ # github: merge-pr 10
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ sc submit
Pushing 'feature-a'...
  Skipping 'feature-a' - already has a merged PR. Run 'sc sync' to clean up merged branches.
Pushing 'feature-b'...
  Creating PR for 'feature-b'...
  Created PR #11: https://github.com/test/repo/pull/11

Created 1 PR(s)
```

## Viewing PR Info in Branch List

After submitting, `sc ls` shows PR numbers for each branch:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ sc submit
Pushing 'add-feature'...
  Creating PR for 'add-feature'...
  Created PR #1: https://github.com/test/repo/pull/1

Created 1 PR(s)
$ sc ls
◉ add-feature #1 (current)
│ Add feature
│
◯ main
  Initial commit
```

For draft PRs, the draft status is shown:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "draft code" > draft.py && git add draft.py
$ sc create -m "Draft feature"
Created branch 'draft-feature' from 'main'
$ sc submit --draft
Pushing 'draft-feature'...
  Creating PR for 'draft-feature'...
  Created PR #1: https://github.com/test/repo/pull/1

Created 1 PR(s)
$ sc ls
◉ draft-feature #1 draft (current)
│ Draft feature
│
◯ main
  Initial commit
```

For stacked PRs, all PR numbers are shown:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ sc submit
Pushing 'feature-a'...
  Creating PR for 'feature-a'...
  Created PR #1: https://github.com/test/repo/pull/1
Pushing 'feature-b'...
  Creating PR for 'feature-b'...
  Created PR #2: https://github.com/test/repo/pull/2

Created 2 PR(s)
$ sc ls
◉ feature-b #2 (current)
│ Feature B
│
◯ feature-a #1
│ Feature A
│
◯ main
  Initial commit
```

## Stack Visualization

When submitting a stack, each PR description is automatically updated with a stack visualization:

```markdown
<!-- shortcake:start -->
## Stack [🍰](https://shortcake.patrick.wtf)

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
- A 🍰 link back to [shortcake](https://shortcake.patrick.wtf) on the heading

## Restacking Before Submit

Submit automatically restacks branches before pushing to ensure they're up-to-date with their parents:

```console
$ # reset-to-main
$ # github: reset-state
$ # github: setup-mock-with-remote
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ git checkout feature-a > /dev/null 2>&1
$ echo "updated a" >> a.py && git add a.py && git commit -m "update feature a" > /dev/null 2>&1
$ git checkout feature-b > /dev/null 2>&1
$ sc submit
Rebasing 'feature-b' onto 'feature-a'...
Restacked feature-b.
Pushing 'feature-a'...
  Creating PR for 'feature-a'...
  Created PR #1: https://github.com/test/repo/pull/1
Pushing 'feature-b'...
  Creating PR for 'feature-b'...
  Created PR #2: https://github.com/test/repo/pull/2

Created 2 PR(s)
```

## Force Push

Use `--force` to push without `--force-with-lease` (bypasses remote safety checks):

```console
$ # reset-to-main
$ # github: reset-state
$ # github: setup-mock-with-remote
$ echo "force feature" > force.py && git add force.py
$ sc create -m "Force feature"
Created branch 'force-feature' from 'main'
$ sc submit --force
Pushing 'force-feature'...
  Creating PR for 'force-feature'...
  Created PR #1: https://github.com/test/repo/pull/1

Created 1 PR(s)
```

## Submitting After Parent Branch Merged and Deleted

When a parent branch's PR was merged and the branch was deleted locally, submit resolves the parent to the merge target:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ # github: add-pr feature-a 1 main
$ # github: add-pr feature-b 2 feature-a
$ # github: merge-pr 1
$ git checkout main > /dev/null 2>&1
$ git merge feature-a --ff-only > /dev/null
$ git branch -D feature-a > /dev/null 2>&1
$ git checkout feature-b > /dev/null 2>&1
$ sc submit
Parent 'feature-a' was merged into 'main', using as base.
Pushing 'feature-b'...
  Updating PR #2 base: feature-a -> main

Updated 1 PR(s)
```

## Error Handling

### Auth Failure

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ # github: error-auth
$ sc submit
Error: GitHub authentication failed. Re-run 'gh auth login' or check your token.
```

### Rate Limit

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ # github: error-rate-limit
$ sc submit
Error: GitHub API rate limit exceeded. Please wait and try again.
```

### Untracked Branch

```console
$ # reset-to-main
$ # github: reset-state
$ sc submit
Error: Branch 'main' is not tracked by shortcake. Use 'sc adopt' to track it first.
```

## Command Options

- `--draft` / `-d`: Create draft PRs
- `--dry-run` / `-n`: Preview without making changes
- `--force` / `-f`: Force push, ignoring remote changes
- `--stealth`: Push branches without creating or updating PRs

## Token Resolution

Unless `--stealth` is set, `sc submit` looks for a GitHub token in this order:

1. `GH_TOKEN` environment variable
2. `GITHUB_TOKEN` environment variable
3. `~/.config/gh/hosts.yml` (gh CLI config)
4. `gh auth token` command output

## Notes

- PRs are created with the first line of the branch's HEAD commit as the title
- When a PR already exists, only the base and description are updated
- `--stealth` skips all PR creation, PR updates, and stack-description sync
- Stack visualization is preserved - your original PR description is kept
- Uses `--force-with-lease` to safely update branches (prevents overwriting others' work)

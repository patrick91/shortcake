# Submitting Pull Requests

## The `sc submit` Command

The `sc submit` command pushes the current branch and its downstack ancestors, then creates or updates their GitHub Pull Requests. Pass `--stack` to include upstack branches too. It:

1. Pushes branches from the bottom of the stack through the current branch, or the whole stack with `--stack`, to origin
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
Submit plan:

  ◯ main         (base)
  │
  ◉ add-feature  create PR

● 1 selected

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
Submit plan:

  ◯ main         (base)
  │
  ◉ add-feature  create PR

● 1 selected

Submitting 1 branch to test/repo

  ● add-feature  #1

✓ 1 PR created

  Top of stack  #1
  https://github.com/test/repo/pull/1
```

## Creating a Draft PR

```console
$ # reset-to-main
$ # github: reset-state
$ echo "draft code" > draft.py && git add draft.py
$ sc create -m "Draft feature"
Created branch 'draft-feature' from 'main'
$ sc submit --draft
Submit plan:

  ◯ main           (base)
  │
  ◉ draft-feature  create PR

● 1 selected

Submitting 1 branch to test/repo · draft

  ● draft-feature  #1

✓ 1 draft PR created

  Top of stack  #1
  https://github.com/test/repo/pull/1
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
Submit plan:

  ◯ main         (base)
  │
  ◉ add-feature  update PR #42

● 1 selected

Submitting 1 branch to test/repo

  ● add-feature  #42

✓ 1 updated

  Top of stack  #42
  https://github.com/test/repo/pull/42
```

## Submitting Through the Current Diff

Plain `sc submit` includes every ancestor required as a PR base, but leaves
branches above the current one untouched:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ echo "feature c" > c.py && git add c.py
$ sc create -m "Feature C"
Created branch 'feature-c' from 'feature-b'
$ git checkout feature-b > /dev/null 2>&1
$ sc submit
Submit plan:

  ◯ main        (base)
  │
  ● feature-a   create PR
  │
  ◉ feature-b   create PR
  │
  ◯ feature-c   not submitted

● 2 selected · ○ 1 upstack branch not selected

Submitting 2 branches to test/repo

  ● feature-a   #1
  ● feature-b   #2

✓ 2 PRs created

  1 upstack branch not submitted · sc submit --stack for the whole stack
```

## Submitting a Stack of PRs

When you have stacked branches, `--stack` creates PRs for the entire stack:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ sc submit --stack
Submit plan:

  ◯ main        (base)
  │
  ● feature-a   create PR
  │
  ◉ feature-b   create PR

● 2 selected

Submitting 2 branches to test/repo

  ● feature-a   #1
  ● feature-b   #2

✓ 2 PRs created

  Top of stack  #2
  https://github.com/test/repo/pull/2
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
$ sc submit --stack
Submit plan:

  ◯ main        (base)
  │
  ● feature-a   merged
  │
  ◉ feature-b   create PR

● 2 selected

Submitting 2 branches to test/repo

  ◌ feature-a   merged
  ● feature-b   #11

✓ 1 PR created · 1 merged

  Top of stack  #11
  https://github.com/test/repo/pull/11
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
Submit plan:

  ◯ main         (base)
  │
  ◉ add-feature  create PR

● 1 selected

Submitting 1 branch to test/repo

  ● add-feature  #1

✓ 1 PR created

  Top of stack  #1
  https://github.com/test/repo/pull/1
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
Submit plan:

  ◯ main           (base)
  │
  ◉ draft-feature  create PR

● 1 selected

Submitting 1 branch to test/repo · draft

  ● draft-feature  #1

✓ 1 draft PR created

  Top of stack  #1
  https://github.com/test/repo/pull/1
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
$ sc submit --stack
Submit plan:

  ◯ main        (base)
  │
  ● feature-a   create PR
  │
  ◉ feature-b   create PR

● 2 selected

Submitting 2 branches to test/repo

  ● feature-a   #1
  ● feature-b   #2

✓ 2 PRs created

  Top of stack  #2
  https://github.com/test/repo/pull/2
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
$ sc submit --stack
Submit plan:

  ◯ main        (base)
  │
  ● feature-a   create PR
  │
  ◉ feature-b   create PR

● 2 selected

Restacking 1 branch

  ● feature-b   rebased

✓ 1 branch restacked
Submitting 2 branches to test/repo

  ● feature-a   #1
  ● feature-b   #2

✓ 2 PRs created

  Top of stack  #2
  https://github.com/test/repo/pull/2
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
Submit plan:

  ◯ main           (base)
  │
  ◉ force-feature  create PR

● 1 selected

Submitting 1 branch to test/repo

  ● force-feature  #1

✓ 1 PR created

  Top of stack  #1
  https://github.com/test/repo/pull/1
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
Submit plan:

  ◯ feature-a   (base)
  │
  ◉ feature-b   update PR #2

● 1 selected

Submitting 1 branch to test/repo

  ● feature-b   #2 base→main

✓ 1 updated

  Top of stack  #2
  https://github.com/test/repo/pull/2
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
- `--stack`: Submit every branch in the current stack
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

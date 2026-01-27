# Submit with Mock GitHub API

These tests use a mock GitHub API server to test the full submit flow without
requiring a real GitHub token.

## Creating a New PR

```console
$ # github: setup-mock-with-remote
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ sc submit
Pushing 'add-feature'...
  Creating PR for 'add-feature'...
  Created PR #1: https://github.com/<OWNER>/<REPO>/pull/1

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
  Created PR #1: https://github.com/<OWNER>/<REPO>/pull/1

Created 1 PR(s)
```

## Updating an Existing PR

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
  Created PR #1: https://github.com/<OWNER>/<REPO>/pull/1
Pushing 'feature-b'...
  Creating PR for 'feature-b'...
  Created PR #2: https://github.com/<OWNER>/<REPO>/pull/2

Created 2 PR(s)
```

## Skipping Merged PRs

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
  Created PR #11: https://github.com/<OWNER>/<REPO>/pull/11

Created 1 PR(s)
```

## Error Handling: Auth Failure

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

## Error Handling: Rate Limit

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

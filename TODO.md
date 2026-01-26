# TODO / Known Issues

Issues discovered during testing of `sc submit`.

## High Priority

### `sc adopt` doesn't support re-parenting
When a branch is already tracked, `sc adopt -p <new-parent>` fails with "Branch is already tracked".

**Expected behavior:** Support `--force` flag or a separate `sc reparent` command to change the parent of an already-tracked branch.

## Medium Priority

### GitHub server messages still appear sometimes
The "Create a pull request" hint from GitHub still appears in some cases during push. Need to investigate if `ls_remote` or other operations are outputting to stderr.

### Stack visualization shows "(no PR)" for merged branches
When a branch has a merged PR, the stack visualization in PR bodies shows "(no PR)" instead of showing the merged PR number or indicating it was merged.

## Low Priority

### No feedback when branch is skipped due to merged PR
When `sc submit` skips a branch because it has a merged PR, it only prints a message. Could be clearer about what action to take (delete the branch, run sync, etc.).

## Completed

- [x] `get_pr_for_branch` only returns open PRs (ignore closed)
- [x] Handle `httpx.RequestError` (network errors) gracefully
- [x] Support `ssh://git@github.com/...` URL format
- [x] Fix `ls_remote` called with wrong arguments (URL not repo)
- [x] Add `has_merged_pr()` to skip branches with merged PRs
- [x] Add tests for `push_branch` force-with-lease logic
- [x] Use respx for GitHub API tests
- [x] Suppress GitHub server messages during push
- [x] `sc sync` detects squash-merged branches (via `is_squash_merged()`)
- [x] Fix test fixtures (`switch_branch` helper) to properly reset index when switching branches

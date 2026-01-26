# TODO / Known Issues

Issues discovered during testing of `sc submit`.

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
- [x] `sc adopt --force` for re-parenting already-tracked branches
- [x] Add `quiet=True` to `ls_remote` call to suppress server messages
- [x] Stack visualization shows merged PR numbers (with "merged" indicator)
- [x] Better feedback when `sc submit` skips merged branches (suggests `sc sync`)

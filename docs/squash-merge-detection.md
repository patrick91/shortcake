# Squash Merge Detection

Shortcake can detect when branches have been squash-merged into the trunk branch, even though squash merges create entirely new commits with different SHAs.

## The Problem

When GitHub (or other platforms) squash-merge a PR:
1. All commits from the feature branch are combined into a single new commit
2. This new commit has a different SHA than any commit on the feature branch
3. The feature branch is **not** an ancestor of the trunk branch
4. Traditional `git branch --merged` doesn't detect it

## Detection Methods

Shortcake uses three methods to detect merged branches, tried in order:

### 1. Ancestor Check (Fast)

```
git merge-base --is-ancestor <branch> <target>
```

This catches regular merges and rebase merges where the branch commits are directly in the target's history.

### 2. Tree Comparison (Fast)

Compares the file state (tree) of the branch against the target. If all files changed by the branch have identical content in the target, the branch is considered merged.

**Limitation:** This can fail if the target has additional changes to the same files, or if the squash commit modified content.

### 3. Cherry-Based Detection (Reliable)

This is the most reliable method, adapted from [Graphite/Charcoal](https://github.com/danerwilliams/charcoal).

**Algorithm:**

1. Get the merge-base between the branch and target
2. Get all commits from merge-base to branch tip
3. For each commit:
   - Create a synthetic "test commit" using `git commit-tree` that represents the cumulative changes up to that point
   - Use `git cherry` to check if equivalent changes exist in the target
4. If ALL commits are detected as having their changes in the target, the branch is squash-merged

**How it works:**

`git cherry` compares patches (the actual changes), not commit SHAs. When it outputs a line starting with `-`, it means an equivalent patch exists in the upstream branch.

```bash
# git cherry output:
# - <sha>  = commit's changes ARE in upstream (merged)
# + <sha>  = commit's changes are NOT in upstream
```

**Example:**

```
main:     A---B---C---S (squash commit containing D+E+F changes)
              \
feature:       D---E---F
```

Even though S has a different SHA than D, E, or F, `git cherry` can detect that S contains equivalent changes to D+E+F.

## Implementation

The detection is implemented in `shortcake/git.py`:

```python
def is_squash_merged(self, branch: str, target: str) -> bool:
    """Check if branch has been squash-merged into target using git cherry."""
    merge_base = self.get_merge_base(branch, target)
    branch_commits = self.get_commit_range(merge_base, branch)

    current_base = merge_base
    for commit in branch_commits:
        # Create test commit with cumulative tree
        test_commit = self.commit_tree(f"{commit}^{{tree}}", current_base, "_")

        # Check if equivalent changes exist in target
        cherry_output = self.cherry(target, test_commit, current_base)
        if cherry_output and cherry_output.startswith("-"):
            current_base = commit  # Advance - this commit is merged

    return current_base == self.get_commit_sha(branch)
```

## Usage

The `sync` command automatically uses all three detection methods:

```bash
# Sync will detect squash-merged branches and clean them up
sc sync

# Preview what would happen
sc sync --dry-run
```

## References

- [Graphite CLI (Charcoal fork)](https://github.com/danerwilliams/charcoal) - Original implementation of the cherry-based algorithm
- [git-cherry documentation](https://git-scm.com/docs/git-cherry) - How git cherry detects equivalent commits
- [git-commit-tree documentation](https://git-scm.com/docs/git-commit-tree) - Creating commit objects for testing

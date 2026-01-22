#!/usr/bin/env bash
# Test: modify -m creates new commit

setup_repo
create_tracked_branch "feature" "main"

# Stage changes and create new commit
echo "new content" > new_file.txt
git add new_file.txt

run_sc modify -m "Add new file"
assert_success
assert_output_contains "Created commit"
assert_output_contains "feature"

# Verify commit was created (not amended)
[ "$(git log --oneline | wc -l)" -ge 3 ] || {
    echo "Expected at least 3 commits"
    exit 1
}

cleanup_repo

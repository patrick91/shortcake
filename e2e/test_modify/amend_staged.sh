#!/usr/bin/env bash
# Test: modify with staged changes amends commit

setup_repo
create_tracked_branch "feature" "main"

# Get initial commit count
initial_count=$(git log --oneline | wc -l)

# Stage changes
echo "modified" > modified.txt
git add modified.txt

run_sc modify
assert_success
assert_output_contains "Amended commit"
assert_output_contains "feature"

# Verify commit count hasn't increased (was amended)
final_count=$(git log --oneline | wc -l)
[ "$final_count" -eq "$initial_count" ] || {
    echo "Expected $initial_count commits, got $final_count (should have amended)"
    exit 1
}

cleanup_repo

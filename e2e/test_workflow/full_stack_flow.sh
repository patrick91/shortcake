#!/usr/bin/env bash
# Test: complete workflow - adopt, navigate, modify

setup_repo

# Create and adopt first branch
create_untracked_branch "first-feature"
run_sc adopt
assert_success

# Add another commit
echo "more work" > more.txt
git add more.txt
run_sc modify -m "More work on first"
assert_success

# Create and adopt second branch (stacked)
create_untracked_branch "second-feature"
run_sc adopt --parent first-feature
assert_success

# Navigate down to parent
run_sc down
assert_success
assert_branch "first-feature"

# Navigate back up
run_sc up
assert_success
assert_branch "second-feature"

# View the stack
run_sc ls
assert_success
assert_output_contains "main"
assert_output_contains "first-feature"
assert_output_contains "second-feature"

# Go to bottom
run_sc bottom
assert_success
assert_branch "first-feature"

# Go to top
run_sc top
assert_success
assert_branch "second-feature"

cleanup_repo

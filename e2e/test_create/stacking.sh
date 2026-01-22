#!/usr/bin/env bash
# Test: create stacks branches correctly

setup_repo

# Create first branch
stage_changes
run_sc create -m "First feature"
assert_success
assert_branch "first-feature"

# Create second branch (stacked on first)
echo "second" > second.txt
git add second.txt
run_sc create -m "Second feature"
assert_success
assert_branch "second-feature"
assert_parent_trailer HEAD "first-feature"

cleanup_repo

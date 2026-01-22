#!/usr/bin/env bash
# Test: restack works after main is updated

setup_repo

# Create and adopt a stack
create_untracked_branch "feature-one"
run_sc adopt
assert_success

create_untracked_branch "feature-two"
run_sc adopt --parent feature-one
assert_success

# Update main to create something to restack
git checkout main >/dev/null 2>&1
echo "main updated" > main_update.txt
git add main_update.txt
git commit -m "Update main" >/dev/null 2>&1

git checkout feature-two >/dev/null 2>&1

# Check restack dry run - now there's something to do
run_sc restack --dry-run
assert_success
assert_output_contains "Would"

cleanup_repo

#!/usr/bin/env bash
# Test: restack --dry-run shows plan when restack needed

setup_repo

# Create a stack
create_untracked_branch "feature"
run_sc adopt
assert_success

# Move main forward to create something to restack
git checkout main >/dev/null 2>&1
echo "new on main" > main_update.txt
git add main_update.txt
git commit -m "Update main" >/dev/null 2>&1

git checkout feature >/dev/null 2>&1

run_sc restack --dry-run
assert_success
assert_output_contains "Would"

cleanup_repo

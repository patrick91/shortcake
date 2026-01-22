#!/usr/bin/env bash
# Test: adopt a branch by name (not current)

setup_repo

# Create untracked branch
create_untracked_branch "feature"

# Go back to main
git checkout main >/dev/null 2>&1

# Adopt feature by name
run_sc adopt feature
assert_success
assert_output_contains "Adopted"
assert_output_contains "feature"
assert_has_trailer feature

cleanup_repo

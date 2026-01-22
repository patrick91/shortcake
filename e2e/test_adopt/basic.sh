#!/usr/bin/env bash
# Test: basic adopt of current branch

setup_repo

# Create a branch with a commit (no trailer)
create_untracked_branch "feature"

run_sc adopt
assert_success
assert_output_contains "Adopted"
assert_output_contains "feature"
assert_has_trailer HEAD

cleanup_repo

#!/usr/bin/env bash
# Test: bottom jumps to first branch above trunk

setup_repo

create_untracked_branch "branch_a"
run_sc adopt
assert_success

create_untracked_branch "branch_b"
run_sc adopt --parent branch_a
assert_success

create_untracked_branch "branch_c"
run_sc adopt --parent branch_b
assert_success

# Start at top
run_sc bottom
assert_success
assert_output_contains "Switched to"
assert_output_contains "branch_a"
assert_branch "branch_a"

cleanup_repo

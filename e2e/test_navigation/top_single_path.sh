#!/usr/bin/env bash
# Test: top jumps to leaf branch

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

git checkout main >/dev/null 2>&1

run_sc top
assert_success
assert_output_contains "Switched to"
assert_output_contains "branch_c"
assert_branch "branch_c"

cleanup_repo

#!/usr/bin/env bash
# Test: ls shows stack tree structure

setup_repo
create_tracked_branch "branch_a" "main"
create_tracked_branch "branch_b" "branch_a"

run_sc ls
assert_success
assert_output_contains "main"
assert_output_contains "branch_a"
assert_output_contains "branch_b"

cleanup_repo

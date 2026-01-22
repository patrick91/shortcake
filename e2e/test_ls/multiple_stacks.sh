#!/usr/bin/env bash
# Test: ls shows multiple stacks from main

setup_repo
create_tracked_branch "stack1_a" "main"
create_tracked_branch "stack1_b" "stack1_a"
git checkout main >/dev/null 2>&1
create_tracked_branch "stack2_a" "main"

run_sc ls
assert_success
assert_output_contains "main"
assert_output_contains "stack1_a"
assert_output_contains "stack1_b"
assert_output_contains "stack2_a"

cleanup_repo

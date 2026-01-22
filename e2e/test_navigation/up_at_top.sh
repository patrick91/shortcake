#!/usr/bin/env bash
# Test: up at top of stack shows message

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

# Already on feature (top of stack)
run_sc up
assert_success
assert_output_contains "top of stack"
assert_branch "feature"

cleanup_repo

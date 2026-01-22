#!/usr/bin/env bash
# Test: top when already at top shows message

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

# Already at top
run_sc top
assert_success
assert_output_contains "top of stack"
assert_branch "feature"

cleanup_repo

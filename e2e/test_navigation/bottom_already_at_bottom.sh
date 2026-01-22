#!/usr/bin/env bash
# Test: bottom when already at bottom shows message

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

# Already at bottom
run_sc bottom
assert_success
assert_output_contains "bottom of stack"
assert_branch "feature"

cleanup_repo

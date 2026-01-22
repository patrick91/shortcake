#!/usr/bin/env bash
# Test: abort with no restack in progress

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

run_sc abort
assert_failure
assert_output_contains "No restack"

cleanup_repo

#!/usr/bin/env bash
# Test: error when branch is already tracked

setup_repo
create_tracked_branch "feature" "main"

run_sc adopt
assert_failure
assert_output_contains "already tracked"

cleanup_repo

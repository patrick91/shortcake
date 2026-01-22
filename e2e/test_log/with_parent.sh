#!/usr/bin/env bash
# Test: log shows parent branch

setup_repo
create_tracked_branch "feature" "main"

run_sc log
assert_success
# Check for the parent marker (circle symbol)
assert_output_contains "main"

cleanup_repo

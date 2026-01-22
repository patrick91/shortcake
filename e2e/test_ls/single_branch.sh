#!/usr/bin/env bash
# Test: ls shows single tracked branch

setup_repo
create_tracked_branch "feature" "main"

run_sc ls
assert_success
assert_output_contains "feature"
assert_output_contains "main"

cleanup_repo

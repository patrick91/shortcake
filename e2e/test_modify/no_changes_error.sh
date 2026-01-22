#!/usr/bin/env bash
# Test: error when no staged changes

setup_repo
create_tracked_branch "feature" "main"

# No staged changes
run_sc modify
assert_failure
assert_output_contains "No staged changes"

cleanup_repo

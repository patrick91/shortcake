#!/usr/bin/env bash
# Test: log shows commits on branch

setup_repo
create_tracked_branch "feature" "main"
add_commit "Second commit"

run_sc log
assert_success
assert_output_contains "feature"
assert_output_contains "main"
assert_output_contains "feat: feature"

cleanup_repo

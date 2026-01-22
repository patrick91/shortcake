#!/usr/bin/env bash
# Test: log shows commits on untracked branch

setup_repo
create_untracked_branch "feature"

run_sc log
assert_success
assert_output_contains "feature"
assert_output_contains "feat: feature"

cleanup_repo

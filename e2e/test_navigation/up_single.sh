#!/usr/bin/env bash
# Test: up navigates to single child

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

git checkout main >/dev/null 2>&1

run_sc up
assert_success
assert_output_contains "Switched to"
assert_output_contains "feature"
assert_branch "feature"

cleanup_repo

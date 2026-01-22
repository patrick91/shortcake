#!/usr/bin/env bash
# Test: down navigates to parent

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

run_sc down
assert_success
assert_output_contains "Switched to"
assert_output_contains "main"
assert_branch "main"

cleanup_repo

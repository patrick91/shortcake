#!/usr/bin/env bash
# Test: down on untracked branch errors

setup_repo

create_untracked_branch "feature"

run_sc down
assert_failure
assert_output_contains "not tracked"

cleanup_repo

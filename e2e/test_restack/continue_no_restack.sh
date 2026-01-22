#!/usr/bin/env bash
# Test: continue with no restack in progress

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

run_sc continue
assert_failure
assert_output_contains "No restack"

cleanup_repo

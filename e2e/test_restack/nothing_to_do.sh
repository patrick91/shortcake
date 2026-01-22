#!/usr/bin/env bash
# Test: restack when nothing to do

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

run_sc restack
assert_success
assert_output_contains "up to date"

cleanup_repo

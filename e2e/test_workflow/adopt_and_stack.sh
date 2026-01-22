#!/usr/bin/env bash
# Test: adopt existing branch then stack on it

setup_repo

# Create untracked branch
create_untracked_branch "existing"

# Adopt it
run_sc adopt
assert_success
assert_tracked "existing"

# Create new branch stacked on top
create_untracked_branch "stacked"
run_sc adopt --parent existing
assert_success

# Verify structure with ls
run_sc ls
assert_success
assert_output_contains "main"
assert_output_contains "existing"
assert_output_contains "stacked"

cleanup_repo

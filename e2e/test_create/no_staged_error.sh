#!/usr/bin/env bash
# Test: error when no staged changes

setup_repo

# No staged changes
run_sc create -m "This should fail"
assert_failure
assert_output_contains "No staged changes"
assert_output_contains "--allow-empty"

cleanup_repo

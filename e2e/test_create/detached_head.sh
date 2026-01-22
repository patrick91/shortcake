#!/usr/bin/env bash
# Test: error when in detached HEAD state

setup_repo

git checkout --detach HEAD >/dev/null 2>&1
stage_changes

run_sc create -m "Should fail"
assert_failure
assert_output_contains "detached HEAD"

cleanup_repo

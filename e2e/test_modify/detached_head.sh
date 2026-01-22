#!/usr/bin/env bash
# Test: error when in detached HEAD state

setup_repo

git checkout --detach HEAD >/dev/null 2>&1

run_sc modify
assert_failure
assert_output_contains "detached HEAD"

cleanup_repo

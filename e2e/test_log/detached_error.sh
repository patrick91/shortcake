#!/usr/bin/env bash
# Test: log errors in detached HEAD state

setup_repo

git checkout --detach HEAD >/dev/null 2>&1

run_sc log
assert_failure
assert_output_contains "detached HEAD"

cleanup_repo

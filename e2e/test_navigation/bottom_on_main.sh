#!/usr/bin/env bash
# Test: bottom on main (trunk) fails - not tracked

setup_repo

run_sc bottom
assert_failure
assert_output_contains "not tracked"
assert_branch "main"

cleanup_repo

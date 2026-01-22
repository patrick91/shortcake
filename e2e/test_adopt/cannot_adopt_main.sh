#!/usr/bin/env bash
# Test: error when trying to adopt main branch

setup_repo

run_sc adopt main
assert_failure
assert_output_contains "Cannot adopt"

cleanup_repo

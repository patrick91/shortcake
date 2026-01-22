#!/usr/bin/env bash
# Test: ls when no tracked branches exist

setup_repo

run_sc ls
assert_success
assert_output_contains "No tracked branches"

cleanup_repo

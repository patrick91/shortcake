#!/usr/bin/env bash
# Test: restack on main (trunk) says up to date

setup_repo

run_sc restack
assert_success
assert_output_contains "up to date"

cleanup_repo

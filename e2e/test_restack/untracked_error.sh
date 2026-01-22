#!/usr/bin/env bash
# Test: restack on untracked branch says up to date

setup_repo

create_untracked_branch "feature"

# Untracked branches have no stack to restack
run_sc restack
assert_success
assert_output_contains "up to date"

cleanup_repo

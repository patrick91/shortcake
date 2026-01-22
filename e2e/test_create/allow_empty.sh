#!/usr/bin/env bash
# Test: create with --allow-empty allows empty commits

setup_repo

# No staged changes
run_sc create -m "Empty commit" --allow-empty
assert_success
assert_output_contains "Created branch"
assert_branch "empty-commit"
assert_has_trailer HEAD

cleanup_repo

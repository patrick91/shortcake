#!/usr/bin/env bash
# Test: create branch with -m message

setup_repo

stage_changes

run_sc create -m "Add new feature"
assert_success
assert_output_contains "Created branch"
assert_output_contains "add-new-feature"
assert_output_contains "main"
assert_branch "add-new-feature"
assert_has_trailer HEAD
assert_parent_trailer HEAD "main"

cleanup_repo

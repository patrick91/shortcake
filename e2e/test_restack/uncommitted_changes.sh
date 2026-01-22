#!/usr/bin/env bash
# Test: restack fails with uncommitted changes

setup_repo

create_untracked_branch "feature"
run_sc adopt
assert_success

# Make uncommitted changes
echo "uncommitted" > uncommitted.txt
git add uncommitted.txt

run_sc restack
assert_failure
assert_output_contains "uncommitted"

cleanup_repo

#!/usr/bin/env bash
# Test: adopt with explicit --parent flag

setup_repo

# Create a base branch
create_untracked_branch "base"

# Create feature branch from base
create_untracked_branch "feature"

# Adopt with explicit parent
run_sc adopt --parent base
assert_success
assert_output_contains "Adopted"
assert_parent_trailer HEAD "base"

cleanup_repo

#!/usr/bin/env bash
# Test: modify preserves Shortcake-Parent trailer

setup_repo
create_tracked_branch "feature" "main"

# Stage and modify
echo "new" > new.txt
git add new.txt

run_sc modify -m "New commit"
assert_success

# Verify trailer is preserved
assert_has_trailer HEAD
assert_parent_trailer HEAD "main"

cleanup_repo

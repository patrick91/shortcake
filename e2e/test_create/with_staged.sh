#!/usr/bin/env bash
# Test: create includes staged changes in commit

setup_repo

# Create and stage a specific file
echo "my content" > my_feature.txt
git add my_feature.txt

run_sc create -m "Add my feature"
assert_success
assert_branch "add-my-feature"

# Verify the file is in the commit
git show --name-only HEAD | grep -q "my_feature.txt" || {
    echo "Staged file not in commit"
    exit 1
}

cleanup_repo

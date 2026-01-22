#!/usr/bin/env bash
# E2E test helpers for Shortcake
set -euo pipefail

# Test state
TEST_REPO=""
LAST_OUTPUT=""
LAST_EXIT_CODE=0

# Setup fresh repo
setup_repo() {
    TEST_REPO=$(mktemp -d)
    cd "$TEST_REPO"
    git init -b main >/dev/null 2>&1
    git config user.email "test@example.com"
    git config user.name "Test User"
    echo "# Test" > README.md
    git add README.md
    git commit -m "Initial commit" >/dev/null 2>&1
}

cleanup_repo() {
    if [ -n "$TEST_REPO" ] && [ -d "$TEST_REPO" ]; then
        rm -rf "$TEST_REPO"
    fi
}

# Run sc, capture output
run_sc() {
    set +e
    LAST_OUTPUT=$(sc "$@" 2>&1)
    LAST_EXIT_CODE=$?
    set -e
}

# Assertions
assert_success() {
    if [ $LAST_EXIT_CODE -ne 0 ]; then
        echo "Expected success, got exit code $LAST_EXIT_CODE"
        echo "Output: $LAST_OUTPUT"
        return 1
    fi
}

assert_failure() {
    if [ $LAST_EXIT_CODE -eq 0 ]; then
        echo "Expected failure, got success"
        echo "Output: $LAST_OUTPUT"
        return 1
    fi
}

assert_output_contains() {
    if [[ "$LAST_OUTPUT" != *"$1"* ]]; then
        echo "Output missing: $1"
        echo "Got: $LAST_OUTPUT"
        return 1
    fi
}

assert_output_not_contains() {
    if [[ "$LAST_OUTPUT" == *"$1"* ]]; then
        echo "Output should not contain: $1"
        echo "Got: $LAST_OUTPUT"
        return 1
    fi
}

assert_branch() {
    local actual
    actual=$(git branch --show-current 2>/dev/null || echo "")
    if [ "$actual" != "$1" ]; then
        echo "Expected branch '$1', got '$actual'"
        return 1
    fi
}

assert_has_trailer() {
    local ref="${1:-HEAD}"
    if ! git log -1 --format=%B "$ref" | grep -q "Shortcake-Parent:"; then
        echo "Missing Shortcake-Parent trailer on $ref"
        git log -1 --format=%B "$ref"
        return 1
    fi
}

assert_no_trailer() {
    local ref="${1:-HEAD}"
    if git log -1 --format=%B "$ref" | grep -q "Shortcake-Parent:"; then
        echo "Unexpected Shortcake-Parent trailer on $ref"
        return 1
    fi
}

assert_parent_trailer() {
    local ref="${1:-HEAD}"
    local expected_parent="$2"
    local actual
    actual=$(git log -1 --format=%B "$ref" | grep "Shortcake-Parent:" | sed 's/Shortcake-Parent: //')
    if [ "$actual" != "$expected_parent" ]; then
        echo "Expected parent trailer '$expected_parent', got '$actual'"
        return 1
    fi
}

assert_tracked() {
    local branch="$1"
    if ! git log -1 --format=%B "$branch" | grep -q "Shortcake-Parent:"; then
        echo "Branch '$branch' is not tracked (missing Shortcake-Parent trailer)"
        git log -1 --format=%B "$branch"
        return 1
    fi
}

# Git helpers
create_tracked_branch() {
    local name="$1"
    local parent="$2"
    git checkout -b "$name" >/dev/null 2>&1
    echo "$name" > "${name}.txt"
    git add "${name}.txt"
    git commit -m "feat: $name

Shortcake-Parent: $parent" >/dev/null 2>&1
}

create_untracked_branch() {
    local name="$1"
    git checkout -b "$name" >/dev/null 2>&1
    echo "$name" > "${name}.txt"
    git add "${name}.txt"
    git commit -m "feat: $name" >/dev/null 2>&1
}

add_commit() {
    local msg="${1:-Update}"
    local filename
    filename=$(echo "$msg" | tr ' ' '_' | tr '[:upper:]' '[:lower:]')
    echo "$msg" > "${filename}.txt"
    git add "${filename}.txt"
    git commit -m "$msg" >/dev/null 2>&1
}

stage_changes() {
    echo "staged content $(date +%s)" > "staged_file_$(date +%s).txt"
    git add .
}

# Snapshot output (for debugging)
snapshot_state() {
    echo "=== SNAPSHOT ==="
    echo "exit_code: $LAST_EXIT_CODE"
    echo "output: $LAST_OUTPUT"
    echo "branch: $(git branch --show-current 2>/dev/null || echo 'detached')"
    echo "branches: $(git branch --format='%(refname:short)' | tr '\n' ' ')"
}

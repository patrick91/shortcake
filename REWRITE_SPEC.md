# Shortcake Rewrite Specification

## Executive Summary

This document specifies a ground-up rewrite of **shortcake**, a CLI tool for managing stacked pull requests. The rewrite addresses architectural issues, known bugs, and design improvements identified through code analysis.

---

## 1. Current State Analysis

### 1.1 What Shortcake Does

Shortcake is a CLI for managing **stacked PRs** - a workflow where multiple PRs build on each other sequentially. It automates:

- Creating branches with automatic naming from commit messages
- Tracking parent-child relationships between branches
- Rebasing branches when parents are updated or merged
- Pushing branches and creating/updating PRs on GitHub
- Syncing after merges (including squash merge detection)
- Navigating between branches in a stack

### 1.2 Current Architecture

```
shortcake/
├── cli.py                 # Main Typer app entry point
├── git.py                 # GitRepo wrapper (~1150 lines)
├── github.py              # GitHub API client
├── metadata.py            # Branch metadata in .git/shortcake.json
├── config.py              # User config (~/.config/shortcake/)
├── gitmoji.py             # Gitmoji picker
├── output.py              # Console output helpers
└── commands/              # Individual commands
    ├── create.py, edit.py, adopt.py, ls.py, sync.py
    ├── submit.py, restack.py, split.py, move.py
    ├── nav.py, get.py, config.py, version.py
```

### 1.3 Identified Issues

#### Bugs & Code Quality Issues

| Issue | Location | Severity |
|-------|----------|----------|
| DEBUG output left in production code | `restack.py:80-115` | Medium |
| Python version mismatch: `requires-python = ">=3.14"` but `target-version = "py312"` | `pyproject.toml` | Medium |
| Tests use git notes but production code uses JSON file for metadata | `tests/*.py` vs `metadata.py` | High |
| Module-level global singleton pattern for MetadataStore | `metadata.py:224-233` | Medium |
| Large monolithic files hard to maintain | `git.py` (1157 lines), `sync.py` (730 lines) | Medium |

#### Architectural Issues

1. **Mixed git abstraction levels**: Uses both GitPython and raw subprocess calls inconsistently
2. **Code duplication**: Merge detection logic duplicated in `sync.py` and `restack.py`
3. **Tight coupling**: Commands directly import from each other (e.g., `submit.py` imports from `restack.py`)
4. **No separation of concerns**: Business logic mixed with CLI presentation
5. **Error handling inconsistency**: Some functions return None on error, others raise exceptions

#### Missing Features

1. No simple `status` command showing current stack state
2. No undo/rollback functionality
3. Limited conflict resolution guidance
4. No branch deletion command (`untrack`/`delete`)
5. No way to view stack without tracked branches (visual tree)

---

## 2. Rewrite Goals

### 2.1 Primary Goals

1. **Correctness**: Fix all identified bugs
2. **Maintainability**: Smaller, focused modules with clear responsibilities
3. **Testability**: Dependency injection, no global state
4. **Reliability**: Consistent error handling with recovery guidance

### 2.2 Non-Goals

1. Adding major new features (focus on stability first)
2. Supporting Git hosting providers other than GitHub
3. Supporting monorepo workflows (multiple stacks per repo)

---

## 3. New Architecture

### 3.1 Module Structure

```
shortcake/
├── __init__.py              # Version, CLI name detection
├── cli.py                   # Typer app, command registration only
│
├── core/                    # Core domain logic (no CLI dependencies)
│   ├── __init__.py
│   ├── stack.py             # Stack operations, branch relationships
│   ├── merge_detection.py   # Squash/regular merge detection
│   └── rebase.py            # Rebase orchestration
│
├── adapters/                # External system interfaces
│   ├── __init__.py
│   ├── git/
│   │   ├── __init__.py
│   │   ├── repo.py          # GitRepo class (read operations)
│   │   ├── mutations.py     # Write operations (commit, rebase, push)
│   │   └── queries.py       # Query operations (ancestry, diff)
│   ├── github.py            # GitHub API client
│   └── storage.py           # Metadata storage abstraction
│
├── commands/                # CLI commands (thin layer over core)
│   ├── __init__.py
│   ├── create.py
│   ├── edit.py
│   ├── adopt.py
│   ├── ls.py
│   ├── status.py            # NEW: Simple status view
│   ├── sync.py
│   ├── submit.py
│   ├── restack.py
│   ├── delete.py            # NEW: Untrack/delete branch
│   ├── split.py
│   ├── move.py
│   ├── nav.py
│   ├── get.py
│   └── config.py
│
├── config.py                # User configuration
├── ui/                      # UI components
│   ├── __init__.py
│   ├── output.py            # Console output, colors
│   ├── prompts.py           # Interactive prompts
│   └── tree.py              # Stack tree rendering
│
└── gitmoji.py               # Gitmoji data and picker
```

### 3.2 Key Design Principles

#### 3.2.1 Dependency Injection

```python
# BAD (current): Global singleton
_default_store: MetadataStore | None = None

def _get_store() -> MetadataStore:
    global _default_store
    if _default_store is None:
        _default_store = MetadataStore()
    return _default_store

# GOOD (new): Explicit injection
class StackManager:
    def __init__(self, git: GitRepo, storage: MetadataStorage):
        self.git = git
        self.storage = storage
```

#### 3.2.2 Pure Core Logic

```python
# Core logic is pure functions operating on data, not I/O
def find_branches_needing_restack(
    branches: list[BranchInfo],
    parent_commits: dict[str, str],
) -> list[str]:
    """Pure function - no git calls, no side effects."""
    return [
        b.name for b in branches
        if b.parent_revision != parent_commits.get(b.parent)
    ]
```

#### 3.2.3 Consistent Error Handling

```python
# Define clear error hierarchy
class ShortcakeError(Exception):
    """Base exception for all shortcake errors."""
    pass

class GitError(ShortcakeError):
    """Git operation failed."""
    pass

class ConflictError(GitError):
    """Rebase conflict detected."""
    def __init__(self, branch: str, resolution_steps: list[str]):
        self.branch = branch
        self.resolution_steps = resolution_steps

# Commands catch and display appropriately
```

### 3.3 Data Models

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class BranchInfo:
    """Immutable branch information."""
    name: str
    parent: str | None
    parent_revision: str | None
    pr_number: int | None = None
    pr_url: str | None = None
    commit_sha: str | None = None
    commit_message: str | None = None
    commit_date: datetime | None = None

@dataclass(frozen=True)
class StackInfo:
    """Information about a complete stack."""
    branches: list[BranchInfo]  # Ordered from trunk to tip
    trunk_branch: str
    current_branch: str

@dataclass
class RestackPlan:
    """Plan for restacking branches."""
    branches_to_rebase: list[tuple[str, str, str]]  # (branch, old_base, new_base)
    branches_up_to_date: list[str]
    merged_branches: list[str]

@dataclass
class SyncResult:
    """Result of a sync operation."""
    deleted_branches: list[str]
    rebased_branches: list[str]
    fast_forwarded_branches: list[str]
    diverged_branches: list[tuple[str, int, int]]  # (name, local_ahead, remote_ahead)
```

---

## 4. Metadata Storage

### 4.1 Current Problems

1. Tests use git notes (`git notes --ref shortcake`) but code uses `.git/shortcake.json`
2. JSON file doesn't travel with the branch (lost on clone)
3. No validation of metadata integrity

### 4.2 New Design

Use **only** `.git/shortcake.json` with improved structure:

```json
{
  "version": 2,
  "branches": {
    "feature-1": {
      "parent": "main",
      "parent_revision": "abc123def456...",
      "pr_number": 42,
      "pr_url": "https://github.com/owner/repo/pull/42",
      "created_at": "2025-01-15T10:30:00Z"
    }
  }
}
```

### 4.3 Migration Strategy

1. On first run, detect version 1 format and migrate
2. Update tests to use JSON file consistently (remove git notes usage)
3. Add schema validation with clear error messages

---

## 5. Command Specifications

### 5.1 New Command: `status`

Simple overview of current stack state:

```
$ sc status

Stack: feature-3 → feature-2 → feature-1 → main

  ◉ feature-3     (current)
    └─ 1 commit ahead of feature-2
    └─ No PR yet

  ◯ feature-2     PR #124 (open)
    └─ Needs restack (parent updated)

  ◯ feature-1     PR #123 (merged ✓)
    └─ Ready to sync

  ◯ main          (up to date with origin)
```

### 5.2 New Command: `delete` / `untrack`

Remove tracking for a branch:

```bash
sc delete feature-1        # Untrack and delete local branch
sc delete feature-1 --keep # Untrack but keep local branch
sc untrack feature-1       # Alias for delete --keep
```

### 5.3 Improved Commands

#### `sync` Improvements

- Better output formatting showing what's happening
- Clear summary at the end
- Handle edge cases more gracefully:
  - Parent branch deleted remotely but not merged
  - PR closed without merging
  - Orphaned branches (parent no longer exists)

#### `restack` Improvements

- Remove DEBUG output
- Better conflict resolution instructions
- Show which commits will be replayed
- Support `--branch` flag to restack specific branch

#### `submit` Improvements

- Progress indicator for multi-branch stacks
- Better handling of PR update failures
- Option to skip PR body updates (`--no-stack-info`)

---

## 6. Git Adapter Redesign

### 6.1 Split by Responsibility

```python
# adapters/git/repo.py - Repository state queries
class GitRepo:
    def __init__(self, path: Path | None = None): ...

    # Branch queries
    def get_current_branch(self) -> str: ...
    def get_branches(self) -> list[str]: ...
    def branch_exists(self, name: str) -> bool: ...
    def get_main_branch(self) -> str: ...

    # Commit queries
    def get_commit_sha(self, ref: str) -> str: ...
    def get_commit_message(self, ref: str) -> str: ...

    # Relationship queries
    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...
    def get_merge_base(self, branch1: str, branch2: str) -> str | None: ...

# adapters/git/mutations.py - Repository modifications
class GitMutations:
    def __init__(self, repo: GitRepo): ...

    def create_branch(self, name: str, checkout: bool = True): ...
    def checkout_branch(self, name: str): ...
    def delete_branch(self, name: str, force: bool = True): ...
    def commit(self, message: str, amend: bool = False): ...
    def rebase_onto(self, new_base: str, old_base: str, branch: str): ...
    def push(self, remote: str, branch: str, force: bool = False): ...
```

### 6.2 Subprocess Preference

Use subprocess consistently instead of mixing with GitPython:

```python
# Prefer this pattern
def get_commit_sha(self, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        capture_output=True,
        text=True,
        cwd=self.working_dir,
    )
    if result.returncode != 0:
        raise GitError(f"Failed to resolve ref '{ref}': {result.stderr}")
    return result.stdout.strip()
```

Rationale:
- More predictable behavior
- Easier to test (can mock subprocess)
- Better error messages from git itself
- Avoids GitPython's sometimes-quirky behavior

---

## 7. Testing Strategy

### 7.1 Test Categories

1. **Unit Tests**: Pure functions in `core/`, mocked adapters
2. **Integration Tests**: Real git operations in temp repos
3. **E2E Tests**: Full CLI invocation with real repos

### 7.2 Test Fixtures

```python
# Consistent fixtures across all tests
@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    """Create an isolated git repository."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    # Create initial commit
    (tmp_path / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmp_path, check=True)
    return GitRepo(tmp_path)

@pytest.fixture
def storage(tmp_path: Path) -> MetadataStorage:
    """Create isolated metadata storage."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir(exist_ok=True)
    return MetadataStorage(git_dir)
```

### 7.3 Remove Git Notes from Tests

All tests should use the JSON metadata format, not git notes.

---

## 8. Configuration

### 8.1 Config Location

Follow XDG Base Directory Specification:
- `$XDG_CONFIG_HOME/shortcake/config.toml` (default: `~/.config/shortcake/config.toml`)

### 8.2 Config Schema

```toml
# ~/.config/shortcake/config.toml

# Branch name generation
keep_emoji = false          # Include emojis in generated branch names
max_branch_length = 50      # Maximum branch name length

# GitHub integration
github_host = "github.com"  # For GitHub Enterprise
create_draft_prs = false    # Create PRs as drafts by default

# Behavior
auto_fetch = true           # Fetch before sync/restack
auto_push = false           # Push after create (not implemented, future)
```

---

## 9. Implementation Plan

### Phase 1: Foundation (Core Infrastructure)

1. Set up new module structure
2. Implement `adapters/git/` with subprocess
3. Implement `adapters/storage.py` with v2 schema
4. Implement core data models
5. Write comprehensive unit tests for adapters

### Phase 2: Core Commands

1. Reimplement `create` command
2. Reimplement `ls` command with new tree rendering
3. Implement new `status` command
4. Reimplement `adopt` command
5. Reimplement `edit` command

### Phase 3: Stack Management

1. Implement `core/merge_detection.py`
2. Implement `core/rebase.py`
3. Reimplement `restack` command (without DEBUG output)
4. Reimplement `sync` command
5. Implement new `delete` command

### Phase 4: GitHub Integration

1. Reimplement `github.py` adapter
2. Reimplement `submit` command
3. Reimplement `get` command
4. Add PR body stack info improvements

### Phase 5: Navigation & Extras

1. Reimplement `nav` commands (up/down/top/bottom)
2. Reimplement `move` command
3. Reimplement `split` command
4. Reimplement `config` command

### Phase 6: Polish

1. Fix Python version requirements
2. Update documentation
3. Migration guide from v1
4. Performance testing
5. Release preparation

---

## 10. Migration Path

### 10.1 Metadata Migration

```python
def migrate_v1_to_v2(git_dir: Path) -> None:
    """Migrate shortcake.json from v1 to v2 format."""
    filepath = git_dir / "shortcake.json"
    if not filepath.exists():
        return

    data = json.loads(filepath.read_text())
    if data.get("version", 1) >= 2:
        return  # Already migrated

    # Add created_at timestamps (use file mtime as approximation)
    for branch_name, metadata in data.get("branches", {}).items():
        if "created_at" not in metadata:
            metadata["created_at"] = None  # Unknown for legacy branches

    data["version"] = 2
    filepath.write_text(json.dumps(data, indent=2) + "\n")
```

### 10.2 User Communication

On first run after upgrade:
```
Shortcake has been upgraded to v2. Your metadata has been migrated.

Changes in this version:
- Improved stack visualization with 'sc status'
- Better conflict resolution guidance
- New 'sc delete' command for removing branches

Run 'sc help' for documentation.
```

---

## 11. Success Criteria

1. **All existing tests pass** (after updating to use JSON storage)
2. **No DEBUG output in production code**
3. **Consistent error messages** with recovery guidance
4. **Test coverage >= 80%** for core modules
5. **No global mutable state** in core modules
6. **Clear separation** between CLI, core logic, and adapters
7. **All commands documented** with examples

---

## Appendix A: Files to Create

```
shortcake/
├── core/
│   ├── __init__.py
│   ├── stack.py
│   ├── merge_detection.py
│   └── rebase.py
├── adapters/
│   ├── __init__.py
│   ├── git/
│   │   ├── __init__.py
│   │   ├── repo.py
│   │   ├── mutations.py
│   │   └── queries.py
│   ├── github.py
│   └── storage.py
├── commands/
│   ├── status.py           # NEW
│   └── delete.py           # NEW
└── ui/
    ├── __init__.py
    ├── output.py
    ├── prompts.py
    └── tree.py
```

## Appendix B: Files to Delete/Refactor

| Current File | Action |
|--------------|--------|
| `shortcake/git.py` | Split into `adapters/git/` |
| `shortcake/metadata.py` | Move to `adapters/storage.py` |
| `shortcake/output.py` | Move to `ui/output.py` |
| `tests/helpers/git_helpers.py` | Update to remove git notes |

## Appendix C: Version Requirements

Fix the Python version inconsistency:

```toml
# pyproject.toml
[project]
requires-python = ">=3.12"  # Changed from 3.14

[tool.ruff]
target-version = "py312"    # Matches requires-python
```

---

*Document Version: 1.0*
*Created: 2025-01-15*

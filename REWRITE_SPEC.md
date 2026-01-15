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
6. **Leaky abstraction during conflicts**: When a conflict occurs during `sc restack` or `sc sync`, the tool suggests using raw git commands (`git add`, `git rebase --continue`) instead of shortcake commands, breaking the abstraction and confusing users

#### Missing Features

1. No simple `status` command showing current stack state
2. No undo/rollback functionality
3. Limited conflict resolution guidance
4. No branch deletion command (`untrack`/`delete`)
5. No way to view stack without tracked branches (visual tree)
6. No `sc add` command to stage files during conflict resolution
7. No unified `sc continue` / `sc abort` commands that work across all operations

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
│   ├── checkout.py          # NEW: Smart checkout (replaces get)
│   ├── config.py
│   ├── add.py               # NEW: Stage files (replaces git add)
│   ├── continue_.py         # NEW: Continue operation (unified)
│   ├── abort.py             # NEW: Abort operation (unified)
│   ├── commit.py            # NEW: Commit with metadata updates
│   ├── diff.py              # NEW: Show changes
│   ├── log.py               # NEW: Show branch commits
│   └── pull.py              # NEW: Multi-device sync (fetch + fast-forward)
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
4. Git notes break on amend/rebase (note stays on old SHA)

### 4.2 New Design: Commit Trailers (First Commit Only)

Store metadata in **commit trailers** in the **first commit** of each branch (the commit right after the branch point from parent):

```
feat: add user authentication

This implements OAuth2 login flow.

Shortcake-Parent: main
Shortcake-PR: 42
```

**Key simplification:** Only store what MUST sync. Compute the rest.

| Data | Stored in trailer? | Why |
|------|-------------------|-----|
| `Shortcake-Parent` (branch name) | ✅ Yes | Must sync between devices |
| `Shortcake-PR` (number) | ✅ Yes | Must sync between devices |
| `Parent-Rev` (SHA) | ❌ No | Compute via `git merge-base` |

**Why first commit (not tip):**

| Operation | Tip commit | First commit |
|-----------|------------|--------------|
| `git commit` (new commit) | ❌ Loses trailers | ✅ Unchanged |
| `git commit --amend` | ✅ Preserved | ✅ Unchanged |
| `git rebase` | ✅ Preserved | ✅ Preserved |
| `git rebase -i` (squash) | Complex | ✅ Messages merge |
| User drops commit | N/A | ⚠️ Loses metadata |

**Why commit trailers:**

| Storage Option | Amend-safe | Rebase-safe | Syncs | Squash-merge |
|---------------|------------|-------------|-------|--------------|
| `.git/shortcake.json` | ✅ | ✅ | ❌ | N/A |
| Git notes | ❌ | ❌ | ✅* | ❌ |
| **Commit trailers** | ✅ | ✅ | ✅ | ✅ |

### 4.3 How It Works

**Finding the first commit with trailers:**
```bash
# Get commits on branch not in parent, oldest first
git log --reverse --format="%H" <branch> ^<parent> | head -1

# Or search for trailer
git log --format="%(trailers:key=Shortcake-Parent,valueonly)" <branch> ^<parent> | grep -v '^$' | head -1
```

**On `sc create`:**
1. User provides commit message (or uses gitmoji picker)
2. Create branch from current position
3. Create commit with trailers: `Shortcake-Parent: <parent-branch>`

**On `sc commit`:**
1. Normal commit, no trailer changes
2. Trailers stay in first commit

**On `sc restack`:**
1. Rebase onto updated parent
2. No trailer changes needed - parent branch name unchanged
3. `Parent-Rev` computed dynamically via merge-base

**On `sc sync` (after parent merged):**
1. Detect parent branch was merged (e.g., `feature-1` → `main`)
2. Rebase child branches onto `main`
3. Amend first commit to update trailer: `Shortcake-Parent: main`

**On `sc checkout` (from remote):**
1. Fetch branch
2. Read trailers from first commit
3. Populate local cache

### 4.4 Local Cache for Performance

Keep `.git/shortcake.json` as a **read cache** to avoid git log on every command:

```json
{
  "version": 2,
  "cache": {
    "feature-1": {
      "first_commit_sha": "abc123...",
      "parent": "main",
      "pr_number": 42
    }
  }
}
```

Cache invalidated when first commit SHA changes (rare - only after rebase or amend of first commit).

**Source of truth is always the commit trailers.**

### 4.5 Edge Cases

**Branch with no commits yet:**
- Can't store trailers - require commit at `sc create` time
- `sc create` prompts for commit message, creates initial commit with trailers

**User deletes first commit (interactive rebase):**
- Metadata lost
- Next `sc` command detects missing trailer, warns user
- User can `sc adopt` to re-add tracking (amends current first commit)

**User reorders commits (interactive rebase):**
- Trailer might not be in "first" commit anymore
- Search all commits on branch for trailer, use first found
- Warn if multiple trailers found (shouldn't happen)

**Squash merge of parent:**
1. `feature-1` has `Shortcake-Parent: main`
2. `feature-2` has `Shortcake-Parent: feature-1`
3. `feature-1` gets squash-merged into `main`
4. `sc sync` detects merge, rebases `feature-2` onto `main`
5. Amends first commit: `Shortcake-Parent: main`

### 4.6 Migration Strategy

1. On first run, read existing `.git/shortcake.json`
2. For each tracked branch:
   - Find first commit on branch (after parent)
   - Amend to add trailers
3. Update cache format to v2
4. Warn user that first commits will be amended (requires force push)
5. Offer `--dry-run` to preview changes

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

### 5.3 New Workflow Commands (Replacing Raw Git)

A key design principle: **users should never need to drop down to raw git commands during shortcake operations**. Currently, when a conflict occurs during `sc restack`, the tool suggests:

```
# BAD (current behavior)
  1. Fix the conflicts in the affected files
  2. Stage the resolved files: git add <files>
  3. Continue the restack: sc restack --continue
```

This is confusing because it mixes abstractions. The rewrite introduces native commands:

#### `sc add` - Stage Files

```bash
sc add <files>              # Stage specific files
sc add .                    # Stage all changes
sc add -p                   # Interactive staging (patch mode)
```

Wrapper around `git add` that also:
- Validates we're in a shortcake operation (restack/sync in progress)
- Provides context-aware help

#### `sc continue` - Continue Operation

```bash
sc continue                 # Continue whatever operation is in progress
```

Unified command that detects and continues:
- `sc restack` in progress → runs `sc restack --continue`
- `sc sync` in progress → runs `sc sync --continue`
- `sc split` in progress → runs `sc split --continue`

#### `sc abort` - Abort Operation

```bash
sc abort                    # Abort whatever operation is in progress
```

Unified command that detects and aborts:
- `sc restack` in progress → runs `sc restack --abort`
- `sc sync` in progress → runs `sc sync --abort`
- `sc split` in progress → runs `sc split --abort`

#### `sc commit` - Create Commit

```bash
sc commit                   # Commit with gitmoji picker
sc commit -m "message"      # Commit with message
sc commit --amend           # Amend last commit
```

Wrapper around `git commit` that:
- Opens gitmoji picker if no message provided
- Updates shortcake metadata after commit
- Works during conflict resolution

#### `sc diff` - Show Changes

```bash
sc diff                     # Show unstaged changes
sc diff --staged            # Show staged changes
sc diff <branch>            # Diff against branch
```

#### `sc log` - Show Commit History

```bash
sc log                      # Show commits in current branch (not in parent)
sc log --all                # Show all commits
```

Shows only the commits that are part of the current branch's changes, not the full history.

#### Conflict Resolution Flow (New)

```
$ sc restack
  Rebasing feature-2 onto origin/main... CONFLICT

Conflict detected in src/utils.py

To resolve:
  1. Edit the conflicted files to resolve conflicts
  2. Stage resolved files: sc add <files>
  3. Continue: sc continue

Or abort: sc abort

$ sc add src/utils.py
Staged: src/utils.py

$ sc continue
Continuing restack...
  Rebasing feature-2 onto origin/main... done
  Rebasing feature-3 onto feature-2... done

Restack complete! Rebased 2 branch(es).
```

### 5.4 Improved Commands

#### `sync` Improvements

- Better output formatting showing what's happening
- Clear summary at the end
- Use `sc continue` / `sc abort` in conflict messages
- Handle edge cases more gracefully:
  - Parent branch deleted remotely but not merged
  - PR closed without merging
  - Orphaned branches (parent no longer exists)

#### `restack` Improvements

- Remove DEBUG output
- Use `sc add` / `sc continue` / `sc abort` in conflict messages
- Show which commits will be replayed
- Support `--branch` flag to restack specific branch

#### `submit` Improvements

- Progress indicator for multi-branch stacks
- Better handling of PR update failures
- Option to skip PR body updates (`--no-stack-info`)

#### Unified `checkout` / `co` (Replaces `get`)

Currently `get` fetches from remote and `checkout` would just switch locally. These should be unified into a single smart command:

```bash
sc checkout feature-1       # Smart checkout (see logic below)
sc co feature-1             # Alias
sc checkout 123             # Checkout by PR number
sc checkout                 # Interactive: pick from branches/PRs
sc checkout --mine          # Interactive: pick from your PRs
```

**Smart checkout logic:**

1. If branch exists locally and is tracked → just switch to it
2. If branch exists locally but not tracked → switch and offer to adopt
3. If branch doesn't exist locally but exists on remote → fetch, adopt stack, switch (current `get` behavior)
4. If PR number given → resolve to branch name, then apply above logic

This means:
- **Remove**: `get` command
- **Add**: `checkout` / `co` command with smart behavior

The user's mental model becomes simple: "I want to be on this branch" - the tool figures out the rest.

```
$ sc co feature-1
Switched to feature-1

$ sc co 123
Resolving PR #123...
  → Branch: someone-elses-feature
Fetching from origin...
Found 2 branch(es) in stack:
  • base-feature
  • someone-elses-feature
Setting up branches...
  ✓ base-feature (parent: main)
  ✓ someone-elses-feature (parent: base-feature)
Switched to someone-elses-feature
```

---

## 6. Multi-Device Workflow

### 6.1 How Commit Trailers Help

With commit trailers as metadata storage (see Section 4), multi-device workflow is much simpler:

| Scenario | With Trailers |
|----------|---------------|
| Created stack on device A, want to work on device B | `sc checkout feature-2` → trailers are in the commits, metadata loads automatically |
| Restacked on A, pushed, now working on B | `sc pull` → fast-forwards, trailers already updated |
| Want to see all my stacks from any device | Metadata travels with branches |

### 6.2 Remaining Challenges

Even with trailers, some friction remains:

1. **Local branches behind remote** - need to fast-forward before working
2. **Diverged branches** - restacked on one device, local changes on another
3. **Cache staleness** - local cache needs refresh after fetch

### 6.3 Multi-Device Command Behavior

| Command | Multi-device behavior |
|---------|----------------------|
| `sc checkout <branch>` | Fetch + infer stack + adopt if needed |
| `sc sync` | Fast-forward behind branches, warn on divergence, `--force` to reset |
| `sc restack` | Fetch first, detect if behind remote, suggest `sync` if needed |
| `sc ls` | Show locally tracked + optionally `--remote` to show remote branches |
| `sc submit` | Works fine (just pushes) |
| `sc create` | Works fine (creates locally) |

### 6.4 New: `sc pull` Command

Add a dedicated command for the "resume on another device" workflow:

```bash
sc pull                      # Pull latest for all tracked branches
sc pull --all                # Pull + adopt any new branches in your stacks
```

**What it does:**
1. Fetch from origin
2. Fast-forward all tracked branches that are behind
3. Auto-reset branches where local commits are rebased versions of remote (safe reset)
4. Warn (don't auto-fix) branches with unique local commits
5. Optionally discover and adopt new branches in existing stacks

**Example workflow:**
```
# On device A:
sc create                    # Create feature-1
sc create                    # Create feature-2
sc submit                    # Push both

# On device B (next day):
sc checkout feature-2        # Fetches and adopts the stack
# ... make changes ...
sc submit

# Back on device A:
sc pull                      # Fast-forwards feature-2 to include B's changes
```

### 6.5 Divergence Handling Improvements

When branches have diverged, provide clear guidance:

```
$ sc sync

⚠ 2 branch(es) have diverged from remote:
  • feature-1: 1 local commit, 3 remote commits
  • feature-2: 2 local commits, 3 remote commits

This usually means:
  • You restacked on another device and pushed
  • A teammate amended/rebased your branch

Options:
  1. sc sync --force         # Reset to remote (discard local commits)
  2. sc diff feature-1 origin/feature-1   # Review differences first
  3. Keep local and manually reconcile

Run 'sc sync --force' to reset all diverged branches to remote.
Run 'sc sync --force feature-1' to reset only feature-1.
```

---

## 7. Git Adapter Redesign

### 7.1 Split by Responsibility

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

### 7.2 Subprocess Preference

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

## 8. Testing Strategy

### 8.1 Test Categories

1. **Unit Tests**: Pure functions in `core/`, mocked adapters
2. **Integration Tests**: Real git operations in temp repos
3. **E2E Tests**: Full CLI invocation with real repos

### 8.2 Test Fixtures

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

### 8.3 Test Metadata via Trailers

Tests should use commit trailers as the source of truth:

```python
def get_branch_metadata(repo_path: Path, branch: str) -> dict:
    """Read metadata from tip commit trailers."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Shortcake-Parent,valueonly)", branch],
        cwd=repo_path, capture_output=True, text=True
    )
    parent = result.stdout.strip() or None
    # ... parse other trailers
    return {"parent": parent, ...}

def set_branch_metadata(repo_path: Path, branch: str, parent: str):
    """Add trailers to tip commit via amend."""
    subprocess.run(
        ["git", "commit", "--amend", "--no-edit",
         "--trailer", f"Shortcake-Parent:{parent}"],
        cwd=repo_path, check=True
    )
```

---

## 9. Configuration

### 9.1 Config Location

Follow XDG Base Directory Specification:
- `$XDG_CONFIG_HOME/shortcake/config.toml` (default: `~/.config/shortcake/config.toml`)

### 9.2 Config Schema

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

## 10. Implementation Plan

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

### Phase 3: Workflow Commands (Git Wrappers)

1. Implement `add` command (wraps git add)
2. Implement `commit` command (wraps git commit + metadata)
3. Implement `continue` command (unified continue)
4. Implement `abort` command (unified abort)
5. Implement `diff` command
6. Implement `log` command
7. Implement `pull` command (multi-device workflow)

### Phase 4: Stack Management

1. Implement `core/merge_detection.py`
2. Implement `core/rebase.py`
3. Reimplement `restack` command (using new workflow commands in messages)
4. Reimplement `sync` command (using new workflow commands in messages)
5. Implement new `delete` command

### Phase 5: GitHub Integration

1. Reimplement `github.py` adapter
2. Reimplement `submit` command
3. Implement `checkout` command (replaces `get`, with smart local/remote logic)
4. Add PR body stack info improvements

### Phase 6: Navigation & Extras

1. Reimplement `nav` commands (up/down/top/bottom)
2. Reimplement `move` command
3. Reimplement `split` command
4. Reimplement `config` command

### Phase 7: Polish

1. Fix Python version requirements
2. Update documentation
3. Migration guide from v1
4. Performance testing
5. Release preparation

---

## 11. Migration Path

### 11.1 Metadata Migration

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

### 11.2 User Communication

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

## 12. Success Criteria

1. **All existing tests pass** (after updating to use commit trailers)
2. **No DEBUG output in production code**
3. **Consistent error messages** with recovery guidance
4. **Test coverage >= 80%** for core modules
5. **No global mutable state** in core modules
6. **Clear separation** between CLI, core logic, and adapters
7. **All commands documented** with examples
8. **No raw git commands in user-facing messages** - all conflict resolution guidance uses `sc` commands (`sc add`, `sc continue`, `sc abort`)

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
│   ├── status.py           # NEW: Stack overview
│   ├── delete.py           # NEW: Untrack/delete branch
│   ├── checkout.py         # NEW: Smart checkout (replaces get)
│   ├── add.py              # NEW: Stage files (replaces git add)
│   ├── continue_.py        # NEW: Unified continue
│   ├── abort.py            # NEW: Unified abort
│   ├── commit.py           # NEW: Commit with metadata
│   ├── diff.py             # NEW: Show changes
│   ├── log.py              # NEW: Show branch commits
│   └── pull.py             # NEW: Multi-device sync
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
| `shortcake/commands/get.py` | Replace with `checkout.py` (unified smart checkout) |
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

*Document Version: 1.5*
*Created: 2025-01-15*
*Updated: 2025-01-15 - Added workflow commands (sc add, sc continue, sc abort, etc.)*
*Updated: 2025-01-15 - Unified get/checkout into smart `sc checkout` / `sc co`*
*Updated: 2025-01-15 - Added multi-device workflow section and `sc pull` command*
*Updated: 2025-01-15 - Simplified trailer design: first commit only, compute parent-rev dynamically*

# Shortcake Implementation Roadmap

Complete implementation plan for the shortcake rewrite, organized by command with dependencies and specifications.

---

## Overview

### Implementation Phases

```
Phase 0: Infrastructure (Week 1)
├── adapters/git/repo.py
├── adapters/git/mutations.py
├── adapters/storage.py
└── core data models

Phase 1: Foundation Commands (Week 2)
├── 01. sc adopt      ← First command
├── 02. sc ls
└── 03. sc create

Phase 2: Workflow Commands (Week 3)
├── 04. sc commit
├── 05. sc add
├── 06. sc status
└── 07. sc log

Phase 3: Stack Operations (Week 4-5)
├── 08. sc restack
├── 09. sc continue / sc abort
├── 10. sc sync
└── 11. sc delete

Phase 4: Navigation & GitHub (Week 6)
├── 12. sc checkout (replaces get)
├── 13. sc up / sc down / sc top / sc bottom
├── 14. sc submit
└── 15. sc pull

Phase 5: Advanced (Week 7)
├── 16. sc move
├── 17. sc split
├── 18. sc edit
├── 19. sc diff
└── 20. sc config
```

---

## Phase 0: Infrastructure

### 0.1 Git Adapter (`adapters/git/repo.py`)

**Purpose:** Read-only git operations

```python
class GitRepo:
    def __init__(self, path: Path | None = None): ...

    # Branch operations
    def get_current_branch(self) -> str: ...
    def branch_exists(self, name: str) -> bool: ...
    def get_branches(self) -> list[str]: ...
    def is_trunk_branch(self, name: str) -> bool: ...
    def get_main_branch(self) -> str: ...

    # Commit operations
    def get_commit_sha(self, ref: str) -> str: ...
    def get_commit_message(self, ref: str) -> str: ...
    def get_parent_commit(self, ref: str) -> str: ...

    # Ancestry operations
    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...
    def get_merge_base(self, ref1: str, ref2: str) -> str | None: ...
    def get_commits_between(self, base: str, head: str) -> list[str]: ...
    def get_first_commit_on_branch(self, branch: str, parent: str) -> str | None: ...

    # Remote operations
    def has_remote(self, name: str) -> bool: ...
    def get_remote_url(self, name: str) -> str: ...

    # State checks
    def is_rebase_in_progress(self) -> bool: ...
    def has_uncommitted_changes(self) -> bool: ...
```

**Tests required:**
- All methods with real git repos
- Error handling for missing refs
- Edge cases (detached HEAD, empty repo)

---

### 0.2 Git Mutations (`adapters/git/mutations.py`)

**Purpose:** Write operations on git

```python
class GitMutations:
    def __init__(self, repo: GitRepo): ...

    # Branch operations
    def create_branch(self, name: str, start_point: str | None = None) -> None: ...
    def checkout_branch(self, name: str) -> None: ...
    def delete_branch(self, name: str, force: bool = False) -> None: ...

    # Commit operations
    def commit(self, message: str, trailers: dict[str, str] | None = None) -> str: ...
    def amend(self, message: str | None = None, trailers: dict[str, str] | None = None) -> str: ...
    def add_files(self, *paths: str) -> None: ...

    # Rebase operations
    def rebase(self, onto: str) -> None: ...
    def rebase_onto(self, newbase: str, upstream: str, branch: str) -> None: ...
    def rebase_continue(self) -> None: ...
    def rebase_abort(self) -> None: ...

    # Remote operations
    def fetch(self, remote: str = "origin") -> None: ...
    def push(self, remote: str, branch: str, force: bool = False) -> None: ...

    # Trailer operations
    def add_trailer_to_commit(self, ref: str, key: str, value: str) -> None: ...
```

**Tests required:**
- Each mutation with verification
- Error handling (conflicts, missing branches)
- Rollback scenarios

---

### 0.3 Storage Adapter (`adapters/storage.py`)

**Purpose:** Trailer and cache operations

```python
# Trailer operations (source of truth)
def read_trailer(repo: GitRepo, commit: str, key: str) -> str | None: ...
def has_trailer(repo: GitRepo, commit: str, key: str) -> bool: ...
def find_commit_with_trailer(repo: GitRepo, branch: str, parent: str, key: str) -> str | None: ...

# Cache operations (performance optimization)
def read_cache(repo: GitRepo) -> dict: ...
def write_cache(repo: GitRepo, data: dict) -> None: ...
def get_cached_branch(repo: GitRepo, branch: str) -> dict | None: ...
def update_cached_branch(repo: GitRepo, branch: str, parent: str, pr_number: int | None = None) -> None: ...
def invalidate_cache(repo: GitRepo, branch: str) -> None: ...

# High-level operations
def get_branch_metadata(repo: GitRepo, branch: str) -> BranchMetadata | None: ...
def get_all_tracked_branches(repo: GitRepo) -> list[BranchMetadata]: ...
```

**Tests required:**
- Trailer read/write roundtrip
- Cache invalidation logic
- Missing trailer handling

---

### 0.4 Core Data Models (`core/models.py`)

```python
@dataclass(frozen=True)
class BranchMetadata:
    name: str
    parent: str
    pr_number: int | None = None
    first_commit_sha: str | None = None

@dataclass(frozen=True)
class StackInfo:
    branches: list[BranchMetadata]  # Ordered from trunk to tip
    trunk: str
    current: str

@dataclass
class RebaseState:
    in_progress: bool
    branch: str | None = None
    onto: str | None = None

class ShortcakeError(Exception): ...
class GitError(ShortcakeError): ...
class ConflictError(GitError): ...
class NotTrackedError(ShortcakeError): ...
```

---

## Phase 1: Foundation Commands

### 01. `sc adopt`

**Detailed plan:** `docs/implementation/01-adopt.md`

**Summary:**
- Adds tracking to existing branch
- Amends first commit with `Shortcake-Parent` trailer
- Auto-detects parent or accepts `--parent`

**Interface:**
```bash
sc adopt [branch] [--parent <branch>] [--force]
```

**Dependencies:** Phase 0 infrastructure

**Tests:** 15+ test cases covering all scenarios

---

### 02. `sc ls`

**Purpose:** List tracked branches in stack format

**Interface:**
```bash
sc ls                    # List current stack
sc ls --all              # List all tracked branches
sc ls --json             # JSON output for scripting
```

**Output:**
```
Stack: feature-2 → feature-1 → main

  ◉ feature-2     (current)      PR #124
  ◯ feature-1                    PR #123
  ◯ main          (trunk)
```

**Implementation:**
```python
@app.command()
def ls(
    all_branches: bool = typer.Option(False, "--all", "-a"),
    json_output: bool = typer.Option(False, "--json"),
):
    repo = GitRepo()

    if all_branches:
        branches = storage.get_all_tracked_branches(repo)
    else:
        current = repo.get_current_branch()
        branches = get_stack_for_branch(repo, current)

    if json_output:
        output.print_json([b.to_dict() for b in branches])
    else:
        output.print_stack_tree(branches, repo.get_current_branch())
```

**Dependencies:**
- `sc adopt` (to have tracked branches)
- Storage adapter (to read trailers)

**Tests:**
- Empty repo (no tracked branches)
- Single branch stack
- Multi-branch stack
- Detached HEAD handling
- JSON output format
- `--all` with multiple independent stacks

---

### 03. `sc create`

**Purpose:** Create new branch with tracking

**Interface:**
```bash
sc create                      # Interactive: gitmoji picker
sc create -m "feat: message"   # With message
sc create --no-commit          # Branch only, no commit (NOT RECOMMENDED)
```

**Flow:**
1. Check for uncommitted changes (error if any)
2. Get commit message (interactive or -m)
3. Create new branch from current position
4. Create commit with `Shortcake-Parent: <current-branch>` trailer
5. Update cache

**Implementation:**
```python
@app.command()
def create(
    message: str | None = typer.Option(None, "-m", "--message"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    if repo.has_uncommitted_changes():
        # Stage and include in commit, or error
        pass

    # Get current branch as parent
    parent = repo.get_current_branch()

    # Get message
    if message is None:
        message = gitmoji_picker()  # Interactive

    # Generate branch name from message
    branch_name = generate_branch_name(message)

    # Create branch
    mutations.create_branch(branch_name)

    # Create commit with trailer
    mutations.commit(message, trailers={"Shortcake-Parent": parent})

    # Update cache
    storage.update_cached_branch(repo, branch_name, parent)

    output.success(f"Created branch '{branch_name}'")
```

**Dependencies:**
- `sc adopt` (shared trailer logic)
- `sc ls` (to verify creation)
- Gitmoji picker

**Tests:**
- Create from main
- Create from feature branch (stacking)
- Create with staged changes
- Create with -m flag
- Branch name generation
- Special characters in message
- Very long messages (truncation)

---

## Phase 2: Workflow Commands

### 04. `sc commit`

**Purpose:** Create commit (wrapper around git commit)

**Interface:**
```bash
sc commit                    # Interactive: gitmoji picker
sc commit -m "message"       # With message
sc commit --amend            # Amend last commit
sc commit -a                 # Stage all and commit
```

**Implementation:**
```python
@app.command()
def commit(
    message: str | None = typer.Option(None, "-m"),
    amend: bool = typer.Option(False, "--amend"),
    all_changes: bool = typer.Option(False, "-a", "--all"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    if all_changes:
        mutations.add_files(".")

    if message is None and not amend:
        message = gitmoji_picker()

    if amend:
        mutations.amend(message)
    else:
        mutations.commit(message)

    output.success("Committed")
```

**Key behavior:**
- Does NOT add trailers (trailers only on first commit)
- Works during conflict resolution
- Updates cache if amending first commit

**Dependencies:** Phase 0 infrastructure

**Tests:**
- Normal commit
- Amend commit
- Commit with staged changes
- Commit all (-a)
- Commit during rebase (conflict resolution)

---

### 05. `sc add`

**Purpose:** Stage files (wrapper around git add)

**Interface:**
```bash
sc add <files...>            # Stage specific files
sc add .                     # Stage all
sc add -p                    # Interactive patch mode
```

**Implementation:**
```python
@app.command()
def add(
    files: list[str] = typer.Argument(...),
    patch: bool = typer.Option(False, "-p", "--patch"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    if patch:
        # Run interactive git add -p
        subprocess.run(["git", "add", "-p"], cwd=repo.path)
    else:
        mutations.add_files(*files)

    output.success(f"Staged: {', '.join(files)}")
```

**Dependencies:** Phase 0 infrastructure

**Tests:**
- Add single file
- Add multiple files
- Add all (.)
- Add during conflict resolution
- Add non-existent file (error)

---

### 06. `sc status`

**Purpose:** Detailed stack status

**Interface:**
```bash
sc status                    # Full status
sc status --short            # Compact output
```

**Output:**
```
Stack: feature-3 → feature-2 → feature-1 → main

  ◉ feature-3     (current)
    ├─ 2 commits ahead of feature-2
    ├─ No PR yet
    └─ Uncommitted changes: 2 files

  ◯ feature-2     PR #124 (open)
    ├─ Up to date with parent
    └─ Behind remote: 1 commit

  ◯ feature-1     PR #123 (merged ✓)
    └─ Ready to sync

  ◯ main
    └─ Up to date with origin/main
```

**Implementation:**
```python
@app.command()
def status(short: bool = typer.Option(False, "-s", "--short")):
    repo = GitRepo()
    current = repo.get_current_branch()
    stack = get_stack_for_branch(repo, current)

    for branch in stack:
        info = gather_branch_info(repo, branch)
        # - commits ahead of parent
        # - PR status (if any)
        # - behind remote?
        # - uncommitted changes?
        output.print_branch_status(info, is_current=(branch.name == current))
```

**Dependencies:**
- `sc ls` (stack traversal)
- GitHub adapter (for PR status)

**Tests:**
- Stack with no PRs
- Stack with open PRs
- Stack with merged PR
- Uncommitted changes
- Behind remote
- Diverged from remote

---

### 07. `sc log`

**Purpose:** Show commits on current branch (not full history)

**Interface:**
```bash
sc log                       # Commits on this branch only
sc log --all                 # Full git log
sc log -n 5                  # Last 5 commits
```

**Output:**
```
Commits on feature-2 (parent: feature-1):

  abc123 feat: add login form
  def456 feat: add validation
```

**Implementation:**
```python
@app.command()
def log(
    all_commits: bool = typer.Option(False, "--all"),
    num: int = typer.Option(None, "-n"),
):
    repo = GitRepo()

    if all_commits:
        subprocess.run(["git", "log"] + ([f"-n{num}"] if num else []))
        return

    branch = repo.get_current_branch()
    metadata = storage.get_branch_metadata(repo, branch)

    if metadata is None:
        output.warning("Branch not tracked by shortcake")
        subprocess.run(["git", "log"] + ([f"-n{num}"] if num else []))
        return

    # Show only commits on this branch
    commits = repo.get_commits_between(metadata.parent, branch)
    output.print_commits(commits)
```

**Dependencies:**
- Storage adapter
- `sc adopt` (to have tracked branches)

**Tests:**
- Log on tracked branch
- Log on untracked branch (fallback)
- Log with -n limit
- Log --all

---

## Phase 3: Stack Operations

### 08. `sc restack`

**Purpose:** Rebase branches onto updated parents

**Interface:**
```bash
sc restack                   # Restack current stack
sc restack --dry-run         # Show what would happen
sc restack --continue        # Continue after conflict
sc restack --abort           # Abort restack
```

**Flow:**
1. Fetch from origin
2. Find all branches in stack needing rebase
3. Rebase each in order (parent first)
4. On conflict: stop and guide user
5. Update cache after completion

**Implementation:**
```python
@app.command()
def restack(
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
    continue_rebase: bool = typer.Option(False, "--continue"),
    abort: bool = typer.Option(False, "--abort"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    if abort:
        mutations.rebase_abort()
        return

    if continue_rebase:
        mutations.rebase_continue()
        # Check if more branches to restack
        continue_remaining_restack(repo)
        return

    # Fetch
    if repo.has_remote("origin"):
        mutations.fetch("origin")

    # Get stack
    stack = get_stack_for_branch(repo, repo.get_current_branch())

    # Find branches needing restack
    needs_restack = []
    for branch in stack:
        parent_sha = repo.get_commit_sha(branch.parent)
        merge_base = repo.get_merge_base(branch.name, branch.parent)
        if merge_base != parent_sha:
            needs_restack.append(branch)

    if not needs_restack:
        output.success("All branches up to date")
        return

    if dry_run:
        for b in needs_restack:
            output.info(f"Would rebase {b.name} onto {b.parent}")
        return

    # Rebase each
    for branch in needs_restack:
        try:
            output.info(f"Rebasing {branch.name} onto {branch.parent}...")
            mutations.rebase_onto(branch.parent, get_old_base(branch), branch.name)
        except ConflictError:
            output.error("Conflict detected")
            output.info("Resolve conflicts, then run: sc continue")
            output.info("Or abort with: sc abort")
            raise typer.Exit(1)
```

**Dependencies:**
- `sc ls` (stack traversal)
- `sc add`, `sc continue`, `sc abort` (conflict resolution)

**Tests:**
- Restack when up to date
- Restack single branch
- Restack multi-branch stack
- Restack with conflict
- Continue after conflict
- Abort restack
- Dry run
- Restack with uncommitted changes (error)

---

### 09. `sc continue` / `sc abort`

**Purpose:** Continue or abort in-progress operations

**Interface:**
```bash
sc continue                  # Continue whatever is in progress
sc abort                     # Abort whatever is in progress
```

**Implementation:**
```python
@app.command()
def continue_op():
    repo = GitRepo()

    if repo.is_rebase_in_progress():
        # Check which sc command started it
        state = load_operation_state(repo)
        if state.command == "restack":
            restack(continue_rebase=True)
        elif state.command == "sync":
            sync(continue_op=True)
        else:
            # Generic rebase continue
            GitMutations(repo).rebase_continue()
    else:
        output.error("No operation in progress")
        raise typer.Exit(1)

@app.command()
def abort():
    repo = GitRepo()

    if repo.is_rebase_in_progress():
        GitMutations(repo).rebase_abort()
        clear_operation_state(repo)
        output.success("Operation aborted")
    else:
        output.error("No operation in progress")
        raise typer.Exit(1)
```

**Dependencies:**
- State tracking for multi-branch operations
- `sc restack`, `sc sync`

**Tests:**
- Continue restack
- Continue sync
- Abort restack
- Abort when nothing in progress

---

### 10. `sc sync`

**Purpose:** Clean up after merges, sync with remote

**Interface:**
```bash
sc sync                      # Full sync
sc sync --dry-run            # Preview
sc sync --force              # Force reset diverged branches
```

**Flow:**
1. Fetch from origin
2. Fast-forward main
3. Detect merged branches (regular + squash merge)
4. For each merged branch:
   - Update children's parent to merged branch's parent
   - Rebase children onto new parent
   - Delete merged branch
5. Fast-forward/reset tracked branches to match remote

**Implementation:**
```python
@app.command()
def sync(
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    # Fetch
    mutations.fetch("origin")

    # Fast-forward main
    fast_forward_main(repo, mutations)

    # Get all tracked branches
    branches = storage.get_all_tracked_branches(repo)

    # Detect merged
    merged = detect_merged_branches(repo, branches)

    # Process merged branches
    for branch in merged:
        if dry_run:
            output.info(f"Would delete merged branch: {branch.name}")
            continue

        # Update children
        children = get_children(branches, branch.name)
        for child in children:
            new_parent = branch.parent  # Merged into this
            update_branch_parent(repo, mutations, child, new_parent)

        # Delete branch
        mutations.delete_branch(branch.name)
        storage.invalidate_cache(repo, branch.name)

    # Fast-forward/reset remaining branches
    for branch in branches:
        if branch in merged:
            continue
        sync_branch_with_remote(repo, mutations, branch, force)
```

**Dependencies:**
- `sc restack` (rebase logic)
- Squash merge detection
- GitHub adapter (PR status check)

**Tests:**
- Sync with nothing to do
- Sync after regular merge
- Sync after squash merge
- Sync with deep stack
- Sync with multiple merged branches
- Sync with diverged branch (warning)
- Sync --force (reset diverged)
- Sync with branch in worktree

---

### 11. `sc delete`

**Purpose:** Remove branch tracking, optionally delete branch

**Interface:**
```bash
sc delete <branch>           # Untrack and delete
sc delete <branch> --keep    # Untrack but keep branch
sc untrack <branch>          # Alias for --keep
```

**Implementation:**
```python
@app.command()
def delete(
    branch: str = typer.Argument(...),
    keep: bool = typer.Option(False, "--keep"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    # Check branch exists and is tracked
    metadata = storage.get_branch_metadata(repo, branch)
    if metadata is None:
        output.error(f"Branch '{branch}' is not tracked")
        raise typer.Exit(1)

    # Update children to point to this branch's parent
    children = get_children_of_branch(repo, branch)
    for child in children:
        update_branch_parent(repo, mutations, child, metadata.parent)
        output.info(f"Updated {child}'s parent: {branch} → {metadata.parent}")

    # Invalidate cache
    storage.invalidate_cache(repo, branch)

    # Delete branch (unless --keep)
    if not keep:
        if repo.get_current_branch() == branch:
            mutations.checkout_branch(metadata.parent)
        mutations.delete_branch(branch, force=True)
        output.success(f"Deleted branch '{branch}'")
    else:
        output.success(f"Untracked branch '{branch}'")
```

**Dependencies:**
- Storage adapter
- `sc ls` (to find children)

**Tests:**
- Delete tracked branch
- Delete with children (updates their parent)
- Delete current branch (checkout parent first)
- Delete with --keep
- Delete untracked branch (error)

---

## Phase 4: Navigation & GitHub

### 12. `sc checkout` (replaces `sc get`)

**Purpose:** Smart checkout - works for local and remote branches

**Interface:**
```bash
sc checkout <branch>         # Smart checkout
sc checkout <pr-number>      # Checkout by PR
sc checkout                  # Interactive picker
sc checkout --mine           # Filter to your PRs
sc co <branch>               # Alias
```

**Flow:**
1. If branch exists locally + tracked → switch
2. If branch exists locally + not tracked → switch, offer adopt
3. If branch only on remote → fetch, infer stack, adopt all, switch
4. If PR number → resolve to branch, then above

**Implementation:**
```python
@app.command()
def checkout(
    target: str | None = typer.Argument(None),
    mine: bool = typer.Option(False, "--mine"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    # Interactive mode
    if target is None:
        target = interactive_branch_picker(repo, mine_only=mine)

    # PR number?
    if target.isdigit():
        target = resolve_pr_to_branch(repo, int(target))

    # Local branch?
    if repo.branch_exists(target):
        mutations.checkout_branch(target)

        # Tracked?
        if storage.get_branch_metadata(repo, target) is None:
            if typer.confirm(f"Track '{target}' with shortcake?"):
                adopt(target)
        return

    # Remote only - fetch and adopt stack
    mutations.fetch("origin")

    if not repo.branch_exists(f"origin/{target}"):
        output.error(f"Branch '{target}' not found locally or on remote")
        raise typer.Exit(1)

    # Infer stack from remote
    stack = infer_stack_from_remote(repo, target)

    # Create local branches and adopt
    for branch_info in stack:
        create_local_from_remote(repo, mutations, branch_info)
        adopt(branch_info.name, parent=branch_info.parent)

    # Checkout target
    mutations.checkout_branch(target)
    output.success(f"Checked out '{target}'")
```

**Dependencies:**
- `sc adopt` (to track branches)
- GitHub adapter (for PR resolution)
- Stack inference logic

**Tests:**
- Checkout local tracked branch
- Checkout local untracked branch
- Checkout from remote
- Checkout by PR number
- Checkout with stack inference
- Interactive mode

---

### 13. `sc up` / `sc down` / `sc top` / `sc bottom`

**Purpose:** Navigate within stack

**Interface:**
```bash
sc up                        # Go to child branch
sc down                      # Go to parent branch
sc top                       # Go to top of stack
sc bottom                    # Go to bottom of stack (just above trunk)
```

**Implementation:**
```python
@app.command()
def up():
    """Move to child branch."""
    repo = GitRepo()
    current = repo.get_current_branch()
    children = get_children_of_branch(repo, current)

    if not children:
        output.warning("Already at top of stack")
        return

    if len(children) == 1:
        GitMutations(repo).checkout_branch(children[0])
    else:
        # Multiple children - prompt
        choice = prompt_branch_choice(children)
        GitMutations(repo).checkout_branch(choice)

@app.command()
def down():
    """Move to parent branch."""
    repo = GitRepo()
    current = repo.get_current_branch()
    metadata = storage.get_branch_metadata(repo, current)

    if metadata is None or repo.is_trunk_branch(metadata.parent):
        output.warning("Already at bottom of stack")
        return

    GitMutations(repo).checkout_branch(metadata.parent)
```

**Dependencies:**
- Storage adapter
- `sc ls` (stack structure)

**Tests:**
- Navigate up/down simple stack
- Navigate at top (warning)
- Navigate at bottom (warning)
- Navigate with multiple children (prompt)
- top/bottom commands

---

### 14. `sc submit`

**Purpose:** Push and create/update PRs

**Interface:**
```bash
sc submit                    # Submit current stack
sc submit --draft            # Create as draft PRs
sc submit --no-edit          # Don't update PR descriptions
```

**Flow:**
1. For each branch in stack (bottom to top):
   - Push to remote
   - Create PR if none exists
   - Update PR base branch if needed
   - Update PR description with stack info

**Implementation:**
```python
@app.command()
def submit(
    draft: bool = typer.Option(False, "--draft"),
    no_edit: bool = typer.Option(False, "--no-edit"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)
    github = GitHubClient()

    stack = get_stack_for_branch(repo, repo.get_current_branch())

    for branch in stack:
        output.info(f"Submitting {branch.name}...")

        # Push
        mutations.push("origin", branch.name, force=True)

        # Get or create PR
        pr = github.get_pr_for_branch(branch.name)

        if pr is None:
            pr = github.create_pr(
                head=branch.name,
                base=branch.parent,
                title=get_pr_title(repo, branch),
                draft=draft,
            )
            output.success(f"Created PR #{pr.number}")

            # Update trailer with PR number
            update_branch_pr_number(repo, mutations, branch, pr.number)
        else:
            # Update base if needed
            if pr.base != branch.parent:
                github.update_pr(pr.number, base=branch.parent)

            output.info(f"Updated PR #{pr.number}")

        # Update description with stack info
        if not no_edit:
            github.update_pr(pr.number, body=generate_stack_description(stack, branch))
```

**Dependencies:**
- GitHub adapter
- `sc restack` (ensure up to date before submit)

**Tests:**
- Submit single branch
- Submit stack (multiple PRs)
- Submit with existing PRs (update)
- Submit draft PRs
- PR description with stack info

---

### 15. `sc pull`

**Purpose:** Pull latest changes for all tracked branches

**Interface:**
```bash
sc pull                      # Fast-forward all tracked branches
sc pull --all                # Also adopt new branches in stacks
```

**Implementation:**
```python
@app.command()
def pull(
    all_branches: bool = typer.Option(False, "--all"),
):
    repo = GitRepo()
    mutations = GitMutations(repo)

    mutations.fetch("origin")

    branches = storage.get_all_tracked_branches(repo)

    for branch in branches:
        remote_ref = f"origin/{branch.name}"
        if not repo.branch_exists(remote_ref):
            continue

        local_sha = repo.get_commit_sha(branch.name)
        remote_sha = repo.get_commit_sha(remote_ref)

        if local_sha == remote_sha:
            continue

        if repo.is_ancestor(local_sha, remote_sha):
            # Fast-forward
            fast_forward_branch(repo, mutations, branch.name, remote_sha)
            output.info(f"Fast-forwarded {branch.name}")
        else:
            # Diverged
            output.warning(f"{branch.name} has diverged from remote")
```

**Dependencies:**
- Storage adapter
- `sc sync` (for more complex reconciliation)

**Tests:**
- Pull when up to date
- Pull with behind branches
- Pull with diverged branches (warning)
- Pull --all (discover new branches)

---

## Phase 5: Advanced Commands

### 16. `sc move`

**Purpose:** Move branch to different parent

```bash
sc move --onto <new-parent>
```

### 17. `sc split`

**Purpose:** Split current branch into multiple branches

```bash
sc split                     # Interactive commit selection
```

### 18. `sc edit`

**Purpose:** Edit commit message

```bash
sc edit                      # Edit last commit
sc edit --all                # Interactive rebase for all branch commits
```

### 19. `sc diff`

**Purpose:** Show diff (wrapper with smart defaults)

```bash
sc diff                      # Diff vs parent branch
sc diff --staged             # Staged changes
```

### 20. `sc config`

**Purpose:** Manage configuration

```bash
sc config                    # Show current config
sc config set <key> <value>  # Set config value
```

---

## Testing Requirements

### Per-Command Coverage

Every command must have:
- [ ] Unit tests for core logic (100% coverage)
- [ ] Integration tests with real git (100% coverage)
- [ ] Edge case tests
- [ ] Error path tests
- [ ] Manual testing checklist

### Global Test Infrastructure

- [ ] Isolated git repo fixture
- [ ] Remote repo fixture (bare repo)
- [ ] GitHub mock fixture
- [ ] CLI runner fixture
- [ ] Trailer helper functions

---

## Success Criteria

Before moving to next command:
1. All tests pass (100% coverage)
2. Manual testing complete
3. Error messages are clear
4. Help text is accurate
5. No regressions in previous commands

---

*Document Version: 1.0*
*Created: 2025-01-15*

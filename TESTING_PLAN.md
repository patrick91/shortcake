# Shortcake Testing Plan

This document outlines the comprehensive testing strategy for the shortcake rewrite, including unit tests, integration tests, end-to-end tests, and manual testing procedures.

---

## 1. Test Categories

### 1.1 Unit Tests
- Pure functions in `core/`
- Mocked git and storage adapters
- Fast, run on every commit

### 1.2 Integration Tests
- Real git operations in isolated temp repos
- Test adapter implementations
- Test command logic with real git

### 1.3 End-to-End (E2E) Tests
- Full CLI invocation
- Real git repos with remote (local bare repo)
- Test complete workflows

### 1.4 Manual Tests
- Complex multi-device scenarios
- GitHub integration (requires real GitHub)
- Edge cases that are hard to automate

---

## 2. Core Functionality Tests

### 2.1 Commit Trailer Storage

#### Unit Tests

```python
def test_parse_trailer_from_commit_message():
    """Parse Shortcake-Parent trailer from commit message."""
    message = """feat: add auth

Shortcake-Parent: main
Shortcake-PR: 42"""
    assert parse_trailer(message, "Shortcake-Parent") == "main"
    assert parse_trailer(message, "Shortcake-PR") == "42"
    assert parse_trailer(message, "Shortcake-Missing") is None

def test_add_trailer_to_message():
    """Add trailer to commit message."""
    message = "feat: add auth\n\nSome description"
    result = add_trailer(message, "Shortcake-Parent", "main")
    assert "Shortcake-Parent: main" in result

def test_update_existing_trailer():
    """Update existing trailer value."""
    message = """feat: add auth

Shortcake-Parent: feature-1"""
    result = update_trailer(message, "Shortcake-Parent", "main")
    assert "Shortcake-Parent: main" in result
    assert "Shortcake-Parent: feature-1" not in result
```

#### Integration Tests

```python
def test_read_trailer_from_git_commit(git_repo):
    """Read trailer from actual git commit."""
    # Create commit with trailer
    git_repo.commit("feat: test", trailers={"Shortcake-Parent": "main"})

    # Read it back
    parent = git_repo.get_trailer("HEAD", "Shortcake-Parent")
    assert parent == "main"

def test_trailer_survives_rebase(git_repo):
    """Trailer in first commit survives rebase."""
    # Setup: main with commit, branch with trailer commit
    git_repo.commit("main commit")
    git_repo.create_branch("feature")
    git_repo.commit("feature commit", trailers={"Shortcake-Parent": "main"})

    # Add more commits to main
    git_repo.checkout("main")
    git_repo.commit("another main commit")

    # Rebase feature onto main
    git_repo.checkout("feature")
    git_repo.rebase("main")

    # Trailer should still be there
    first_commit = git_repo.get_first_commit_on_branch("feature", "main")
    parent = git_repo.get_trailer(first_commit, "Shortcake-Parent")
    assert parent == "main"

def test_trailer_survives_amend_of_later_commit(git_repo):
    """Trailer in first commit survives amend of tip."""
    git_repo.create_branch("feature")
    git_repo.commit("first", trailers={"Shortcake-Parent": "main"})
    git_repo.commit("second")

    # Amend tip
    git_repo.amend("second amended")

    # First commit trailer unchanged
    first = git_repo.get_first_commit_on_branch("feature", "main")
    assert git_repo.get_trailer(first, "Shortcake-Parent") == "main"

def test_trailer_survives_interactive_rebase_squash(git_repo):
    """Trailer preserved when squashing commits."""
    git_repo.create_branch("feature")
    git_repo.commit("first", trailers={"Shortcake-Parent": "main"})
    git_repo.commit("second")
    git_repo.commit("third")

    # Squash second and third into first
    git_repo.interactive_rebase_squash("main", squash_all=True)

    # Single commit should have trailer
    commits = git_repo.get_commits_on_branch("feature", "main")
    assert len(commits) == 1
    assert git_repo.get_trailer(commits[0], "Shortcake-Parent") == "main"
```

### 2.2 Branch Stack Operations

#### E2E Tests

```python
def test_create_simple_stack(cli, git_repo):
    """Create a 3-branch stack."""
    # Create first branch
    result = cli("create", input="feat: add auth\n")
    assert result.exit_code == 0
    assert "Created branch" in result.output

    # Create second branch
    result = cli("create", input="feat: add login\n")
    assert result.exit_code == 0

    # Create third branch
    result = cli("create", input="feat: add logout\n")
    assert result.exit_code == 0

    # Verify stack structure
    result = cli("ls")
    assert "auth" in result.output
    assert "login" in result.output
    assert "logout" in result.output

def test_restack_after_main_update(cli, git_repo, remote):
    """Restack when main has new commits."""
    # Setup stack
    cli("create", input="feat: feature-1\n")
    cli("create", input="feat: feature-2\n")

    # Add commit to main (simulating teammate's merge)
    git_repo.checkout("main")
    git_repo.commit("teammate's feature")
    git_repo.push("origin", "main")

    # Go back to feature-2
    git_repo.checkout("feature-2")

    # Restack
    result = cli("restack")
    assert result.exit_code == 0
    assert "Rebased" in result.output

    # Verify both branches are now based on updated main
    assert git_repo.is_ancestor("main", "feature-1")
    assert git_repo.is_ancestor("feature-1", "feature-2")

def test_sync_after_squash_merge(cli, git_repo, remote, github_mock):
    """Sync cleans up after parent branch is squash-merged."""
    # Setup: main → feature-1 → feature-2
    cli("create", input="feat: feature-1\n")
    cli("submit")
    cli("create", input="feat: feature-2\n")
    cli("submit")

    # Simulate squash merge of feature-1 PR
    git_repo.checkout("main")
    git_repo.commit("feat: feature-1 (#1)")  # Squash commit
    git_repo.push("origin", "main")
    github_mock.mark_pr_merged(1)

    # Sync
    git_repo.checkout("feature-2")
    result = cli("sync")
    assert result.exit_code == 0

    # feature-1 should be deleted
    assert not git_repo.branch_exists("feature-1")

    # feature-2 should now have parent=main
    trailer = git_repo.get_trailer_from_branch("feature-2", "Shortcake-Parent")
    assert trailer == "main"
```

---

## 3. Edge Case Tests

### 3.1 Trailer Edge Cases

```python
def test_user_deletes_first_commit_interactive_rebase(cli, git_repo):
    """Handle user deleting the commit with trailer."""
    cli("create", input="feat: feature-1\n")
    git_repo.commit("second commit")

    # User deletes first commit via interactive rebase
    git_repo.interactive_rebase_drop_first("main")

    # Next sc command should warn
    result = cli("status")
    assert "missing metadata" in result.output.lower() or "not tracked" in result.output.lower()

def test_user_reorders_commits_interactive_rebase(cli, git_repo):
    """Handle user reordering commits."""
    cli("create", input="feat: feature-1\n")
    git_repo.commit("second commit")
    git_repo.commit("third commit")

    # User reorders so first commit is now in middle
    git_repo.interactive_rebase_reorder("main")  # Moves first to middle

    # sc should still find trailer by searching all commits
    result = cli("ls")
    assert "feature-1" in result.output  # Still tracked

def test_multiple_trailers_in_branch(cli, git_repo):
    """Handle (unusual) case of multiple trailers."""
    cli("create", input="feat: feature-1\n")

    # Manually add another commit with trailer (shouldn't happen normally)
    git_repo.commit("second", trailers={"Shortcake-Parent": "main"})

    # Should use first found, maybe warn
    result = cli("ls")
    assert "feature-1" in result.output

def test_empty_branch_no_commits(cli, git_repo):
    """sc create requires a commit."""
    # Try to create without committing
    result = cli("create", input="\n")  # Empty message
    assert result.exit_code != 0
    assert "commit" in result.output.lower()
```

### 3.2 Rebase Edge Cases

```python
def test_restack_with_conflicts(cli, git_repo):
    """Handle rebase conflicts during restack."""
    # Setup conflicting changes
    git_repo.write_file("shared.txt", "main content")
    git_repo.commit("main: add shared")

    cli("create", input="feat: feature-1\n")
    git_repo.write_file("shared.txt", "feature content")
    git_repo.commit("feature: modify shared")

    # Create conflict on main
    git_repo.checkout("main")
    git_repo.write_file("shared.txt", "conflicting content")
    git_repo.commit("main: also modify shared")

    git_repo.checkout("feature-1")

    # Restack should hit conflict
    result = cli("restack")
    assert result.exit_code != 0
    assert "conflict" in result.output.lower()
    assert "sc add" in result.output
    assert "sc continue" in result.output

def test_restack_continue_after_conflict_resolution(cli, git_repo):
    """Continue restack after resolving conflicts."""
    # ... setup conflict as above ...
    cli("restack")  # Hits conflict

    # Resolve conflict
    git_repo.write_file("shared.txt", "resolved content")
    cli("add", "shared.txt")

    # Continue
    result = cli("continue")
    assert result.exit_code == 0
    assert "complete" in result.output.lower()

def test_restack_abort(cli, git_repo):
    """Abort restack mid-conflict."""
    # ... setup conflict ...
    cli("restack")  # Hits conflict

    result = cli("abort")
    assert result.exit_code == 0

    # Branch should be back to original state
    assert git_repo.get_file_content("shared.txt") == "feature content"

def test_restack_deep_stack(cli, git_repo):
    """Restack a 5-level deep stack."""
    for i in range(1, 6):
        cli("create", input=f"feat: feature-{i}\n")

    # Update main
    git_repo.checkout("main")
    git_repo.commit("main update")

    git_repo.checkout("feature-5")

    result = cli("restack")
    assert result.exit_code == 0

    # All branches should be rebased
    for i in range(1, 6):
        assert git_repo.is_ancestor("main", f"feature-{i}")
```

### 3.3 Sync Edge Cases

```python
def test_sync_multiple_merged_branches(cli, git_repo, github_mock):
    """Sync when multiple branches in stack are merged."""
    # main → A → B → C
    cli("create", input="feat: A\n")
    cli("create", input="feat: B\n")
    cli("create", input="feat: C\n")

    # Both A and B get merged
    git_repo.checkout("main")
    git_repo.commit("squash A")
    git_repo.commit("squash B")
    github_mock.mark_pr_merged(1)  # A
    github_mock.mark_pr_merged(2)  # B

    git_repo.checkout("C")
    result = cli("sync")
    assert result.exit_code == 0

    # A and B deleted
    assert not git_repo.branch_exists("A")
    assert not git_repo.branch_exists("B")

    # C now has parent=main (not A or B)
    trailer = git_repo.get_trailer_from_branch("C", "Shortcake-Parent")
    assert trailer == "main"

def test_sync_parent_branch_deleted_not_merged(cli, git_repo):
    """Handle parent branch deleted without merge (abandoned PR)."""
    cli("create", input="feat: feature-1\n")
    cli("create", input="feat: feature-2\n")

    # Delete feature-1 without merging (abandoned)
    git_repo.checkout("main")
    git_repo.delete_branch("feature-1", force=True)

    git_repo.checkout("feature-2")
    result = cli("sync")

    # Should warn and offer to rebase onto main
    assert "no longer exists" in result.output.lower()

def test_sync_worktree_has_branch_checked_out(cli, git_repo, tmp_path):
    """Sync deletes branch that's checked out in a worktree."""
    cli("create", input="feat: feature-1\n")
    cli("submit")

    # Create worktree with feature-1
    worktree_path = tmp_path / "worktree"
    git_repo.add_worktree(worktree_path, "feature-1")

    # Merge feature-1
    git_repo.checkout("main")
    git_repo.merge("feature-1")

    # Sync should switch worktree before deleting
    git_repo.checkout("main")
    result = cli("sync")
    assert result.exit_code == 0
    assert "worktree" in result.output.lower()
```

### 3.4 Multi-Device Edge Cases

```python
def test_checkout_from_remote_with_stack(cli, git_repo, remote):
    """Checkout a branch and its full stack from remote."""
    # Device A: create stack and push
    cli("create", input="feat: feature-1\n")
    cli("create", input="feat: feature-2\n")
    cli("submit")

    # Device B: fresh clone
    device_b = clone_repo(remote)

    # Checkout should fetch and adopt entire stack
    result = device_b.cli("checkout", "feature-2")
    assert result.exit_code == 0
    assert "feature-1" in result.output  # Found parent

    # Both branches should be tracked
    result = device_b.cli("ls")
    assert "feature-1" in result.output
    assert "feature-2" in result.output

def test_pull_after_restack_on_other_device(cli, git_repo, remote):
    """Pull fast-forwards after restack on another device."""
    # Setup
    cli("create", input="feat: feature-1\n")
    cli("submit")

    local_sha_before = git_repo.get_sha("feature-1")

    # Simulate restack on device B
    device_b = clone_repo(remote)
    device_b.git.checkout("main")
    device_b.git.commit("main update")
    device_b.git.push("origin", "main")
    device_b.git.checkout("feature-1")
    device_b.git.rebase("main")
    device_b.git.push("origin", "feature-1", force=True)

    # Device A: pull should fast-forward
    result = cli("pull")
    assert result.exit_code == 0

    local_sha_after = git_repo.get_sha("feature-1")
    assert local_sha_after != local_sha_before

def test_diverged_branch_warning(cli, git_repo, remote):
    """Warn when branch has unique local commits."""
    cli("create", input="feat: feature-1\n")
    cli("submit")

    # Local: add commit
    git_repo.commit("local only commit")

    # Remote: different commit (simulating another device)
    # ... (via another clone)

    result = cli("sync")
    assert "diverged" in result.output.lower()
    assert "--force" in result.output

def test_sync_force_resets_diverged(cli, git_repo, remote):
    """Force sync resets diverged branches."""
    # ... setup diverged state ...

    result = cli("sync", "--force")
    assert result.exit_code == 0

    # Local should match remote
    assert git_repo.get_sha("feature-1") == git_repo.get_sha("origin/feature-1")
```

### 3.5 Navigation Edge Cases

```python
def test_nav_up_at_top_of_stack(cli, git_repo):
    """nav up when already at top."""
    cli("create", input="feat: feature-1\n")
    git_repo.checkout("main")

    result = cli("up")
    assert result.exit_code == 0
    assert git_repo.current_branch() == "feature-1"

    # Already at top
    result = cli("up")
    assert "already at top" in result.output.lower() or git_repo.current_branch() == "feature-1"

def test_nav_down_at_bottom(cli, git_repo):
    """nav down when at bottom of stack."""
    cli("create", input="feat: feature-1\n")

    result = cli("down")
    assert git_repo.current_branch() == "main"

    # Already at bottom
    result = cli("down")
    assert "already at" in result.output.lower() or git_repo.current_branch() == "main"

def test_nav_with_multiple_children(cli, git_repo):
    """Navigate when branch has multiple children."""
    cli("create", input="feat: feature-1\n")
    git_repo.checkout("main")
    cli("create", input="feat: feature-2\n")  # Second child of main

    git_repo.checkout("main")
    result = cli("up")

    # Should prompt to choose or pick one
    # (implementation dependent)
```

---

## 4. Manual Testing Checklist

### 4.1 Basic Workflow

- [ ] `sc create` with gitmoji picker
- [ ] `sc create` with `-m "message"`
- [ ] `sc ls` shows correct stack structure
- [ ] `sc status` shows detailed info
- [ ] `sc up` / `sc down` navigation
- [ ] `sc top` / `sc bottom` navigation

### 4.2 GitHub Integration

- [ ] `sc submit` creates PR with correct base
- [ ] `sc submit` updates existing PR
- [ ] `sc submit` on stack creates multiple PRs with correct bases
- [ ] PR body shows stack information
- [ ] `sc checkout 123` fetches by PR number
- [ ] `sc checkout --mine` shows only your PRs

### 4.3 Restack Workflow

- [ ] `sc restack` with no changes needed
- [ ] `sc restack` after main is updated
- [ ] `sc restack` with conflicts
- [ ] `sc add` to stage conflict resolution
- [ ] `sc continue` after conflict resolution
- [ ] `sc abort` to cancel restack

### 4.4 Sync Workflow

- [ ] `sc sync` with no merges
- [ ] `sc sync` after regular merge
- [ ] `sc sync` after squash merge
- [ ] `sc sync` with deep stack (3+ levels)
- [ ] `sc sync` with multiple merged branches
- [ ] `sc sync --force` for diverged branches

### 4.5 Multi-Device Workflow

- [ ] Create stack on device A, `sc checkout` on device B
- [ ] Push from A, `sc pull` on B
- [ ] Restack on A, push, `sc pull` on B
- [ ] Make changes on both devices, see divergence warning
- [ ] `sc sync --force` to resolve divergence

### 4.6 Edge Cases to Test Manually

- [ ] Interactive rebase (`git rebase -i`) preserves trailers
- [ ] Squashing commits preserves trailers
- [ ] Dropping first commit shows warning
- [ ] Very long stack (10+ branches)
- [ ] Stack with conflicts at multiple levels
- [ ] Branch names with special characters
- [ ] Commit messages with special characters
- [ ] Large repositories (performance)
- [ ] Slow network (timeout handling)

---

## 5. Performance Tests

### 5.1 Benchmarks

```python
def test_ls_performance_large_stack(cli, git_repo, benchmark):
    """ls should be fast even with many branches."""
    # Create 50 branches
    for i in range(50):
        cli("create", input=f"feat: feature-{i}\n")

    result = benchmark(cli, "ls")
    assert result.exit_code == 0
    # Should complete in < 2 seconds

def test_status_performance_with_remote(cli, git_repo, remote, benchmark):
    """status with remote checks should be fast."""
    for i in range(20):
        cli("create", input=f"feat: feature-{i}\n")
    cli("submit")

    result = benchmark(cli, "status")
    assert result.exit_code == 0
    # Should complete in < 5 seconds

def test_cache_invalidation_performance(cli, git_repo, benchmark):
    """Cache should speed up repeated operations."""
    cli("create", input="feat: feature-1\n")

    # First call (cold cache)
    time_cold = benchmark(cli, "ls")

    # Second call (warm cache)
    time_warm = benchmark(cli, "ls")

    # Warm should be faster
    assert time_warm < time_cold
```

---

## 6. Test Infrastructure

### 6.1 Fixtures

```python
@pytest.fixture
def git_repo(tmp_path):
    """Isolated git repository."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)

    # Initial commit
    (repo_path / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

    return GitTestRepo(repo_path)

@pytest.fixture
def remote(tmp_path):
    """Bare repository as remote."""
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_path)], check=True)
    return remote_path

@pytest.fixture
def cli(git_repo):
    """CLI runner bound to test repo."""
    def run_cli(*args, input=None):
        return CliRunner().invoke(app, args, input=input, cwd=git_repo.path)
    return run_cli

@pytest.fixture
def github_mock(monkeypatch):
    """Mock GitHub API."""
    mock = GitHubMock()
    monkeypatch.setattr("shortcake.github.GitHubClient", mock.client_class)
    return mock
```

### 6.2 Test Helpers

```python
class GitTestRepo:
    """Helper for git operations in tests."""

    def __init__(self, path: Path):
        self.path = path

    def commit(self, message: str, trailers: dict = None):
        """Create commit with optional trailers."""
        trailer_args = []
        for key, value in (trailers or {}).items():
            trailer_args.extend(["--trailer", f"{key}:{value}"])
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", message] + trailer_args,
            cwd=self.path, check=True
        )

    def get_trailer(self, ref: str, key: str) -> str | None:
        """Get trailer value from commit."""
        result = subprocess.run(
            ["git", "log", "-1", f"--format=%(trailers:key={key},valueonly)", ref],
            cwd=self.path, capture_output=True, text=True
        )
        value = result.stdout.strip()
        return value if value else None

    def get_first_commit_on_branch(self, branch: str, parent: str) -> str:
        """Get SHA of first commit on branch after parent."""
        result = subprocess.run(
            ["git", "log", "--reverse", "--format=%H", f"{parent}..{branch}"],
            cwd=self.path, capture_output=True, text=True
        )
        commits = result.stdout.strip().split("\n")
        return commits[0] if commits else None

    def interactive_rebase_squash(self, base: str, squash_all: bool = False):
        """Programmatically squash commits."""
        # Use GIT_SEQUENCE_EDITOR to automate
        if squash_all:
            editor_script = "sed -i '2,$s/^pick/squash/'"
        else:
            editor_script = "cat"  # No changes
        subprocess.run(
            ["git", "rebase", "-i", base],
            cwd=self.path,
            env={**os.environ, "GIT_SEQUENCE_EDITOR": editor_script},
            check=True
        )

class GitHubMock:
    """Mock GitHub API for testing."""

    def __init__(self):
        self.prs = {}
        self.merged_prs = set()

    def create_pr(self, **kwargs):
        pr_number = len(self.prs) + 1
        self.prs[pr_number] = kwargs
        return pr_number

    def mark_pr_merged(self, pr_number: int):
        self.merged_prs.add(pr_number)

    def is_merged(self, pr_number: int) -> bool:
        return pr_number in self.merged_prs
```

---

## 7. CI Configuration

### 7.1 GitHub Actions

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest tests/unit -v

  integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: |
          git config --global user.email "test@test.com"
          git config --global user.name "Test"
      - run: pip install -e ".[dev]"
      - run: pytest tests/integration -v

  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: |
          git config --global user.email "test@test.com"
          git config --global user.name "Test"
      - run: pip install -e ".[dev]"
      - run: pytest tests/e2e -v --timeout=60
```

---

## 8. Test Coverage Goals

| Module | Target Coverage |
|--------|----------------|
| `core/stack.py` | 100% |
| `core/merge_detection.py` | 100% |
| `core/rebase.py` | 100% |
| `adapters/git/` | 100% |
| `adapters/storage.py` | 100% |
| `commands/` | 100% |
| **Overall** | **100%** |

No exceptions. Every line, every branch, every edge case.

---

*Document Version: 1.0*
*Created: 2025-01-15*

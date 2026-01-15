# Implementation Plan: `sc adopt`

**Priority:** 1 (First command to implement)
**Complexity:** Low
**Dependencies:** Core infrastructure only

---

## 1. What It Does

`sc adopt` takes an existing git branch and adds shortcake tracking by inserting a trailer into the first commit.

```bash
# Basic usage
sc adopt                     # Adopt current branch, auto-detect parent
sc adopt feature-1           # Adopt specific branch
sc adopt feature-1 --parent main  # Explicit parent
```

**Before:**
```
* abc123 (feature-1) feat: add auth
* def456 (main) initial commit
```

**After:**
```
* abc123' (feature-1) feat: add auth
|
| Shortcake-Parent: main
|
* def456 (main) initial commit
```

Note: SHA changes because we amend the commit.

---

## 2. Prerequisites (Build First)

### 2.1 Trailer Operations (`adapters/storage.py`)

```python
def read_trailer(repo_path: Path, commit: str, key: str) -> str | None:
    """Read a trailer value from a commit."""
    result = subprocess.run(
        ["git", "log", "-1", f"--format=%(trailers:key={key},valueonly)", commit],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    return value if value else None


def add_trailer(repo_path: Path, key: str, value: str) -> None:
    """Add trailer to HEAD commit via amend."""
    subprocess.run(
        ["git", "commit", "--amend", "--no-edit", "--trailer", f"{key}:{value}"],
        cwd=repo_path,
        check=True,
    )


def has_trailer(repo_path: Path, commit: str, key: str) -> bool:
    """Check if commit has a specific trailer."""
    return read_trailer(repo_path, commit, key) is not None
```

### 2.2 Branch Operations (`adapters/git/repo.py`)

```python
def get_current_branch(repo_path: Path) -> str:
    """Get name of current branch."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_first_commit_on_branch(repo_path: Path, branch: str, parent: str) -> str | None:
    """Get SHA of first commit on branch after diverging from parent."""
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", f"{parent}..{branch}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    commits = result.stdout.strip().split("\n")
    return commits[0] if commits and commits[0] else None


def get_merge_base(repo_path: Path, branch1: str, branch2: str) -> str | None:
    """Get common ancestor of two branches."""
    result = subprocess.run(
        ["git", "merge-base", branch1, branch2],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def detect_parent_branch(repo_path: Path, branch: str) -> str | None:
    """Auto-detect likely parent branch (main/master or closest ancestor)."""
    # Try main first
    for candidate in ["main", "master"]:
        if branch_exists(repo_path, candidate):
            if is_ancestor(repo_path, candidate, branch):
                return candidate
    return None


def checkout_branch(repo_path: Path, branch: str) -> None:
    """Checkout a branch."""
    subprocess.run(
        ["git", "checkout", branch],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
```

### 2.3 Cache Operations (`adapters/storage.py`)

```python
CACHE_FILE = ".git/shortcake.json"

def read_cache(repo_path: Path) -> dict:
    """Read local cache file."""
    cache_path = repo_path / CACHE_FILE
    if not cache_path.exists():
        return {"version": 2, "cache": {}}
    return json.loads(cache_path.read_text())


def write_cache(repo_path: Path, data: dict) -> None:
    """Write local cache file."""
    cache_path = repo_path / CACHE_FILE
    cache_path.write_text(json.dumps(data, indent=2) + "\n")


def update_cache_entry(repo_path: Path, branch: str, parent: str, pr_number: int | None = None) -> None:
    """Update cache for a single branch."""
    cache = read_cache(repo_path)
    first_commit = get_first_commit_on_branch(repo_path, branch, parent)
    cache["cache"][branch] = {
        "first_commit_sha": first_commit,
        "parent": parent,
        "pr_number": pr_number,
    }
    write_cache(repo_path, cache)
```

---

## 3. Command Implementation

### 3.1 CLI Interface (`commands/adopt.py`)

```python
import typer
from pathlib import Path

from shortcake.adapters.git import repo as git
from shortcake.adapters import storage
from shortcake.ui import output

app = typer.Typer()


@app.command()
def adopt(
    branch: str | None = typer.Argument(None, help="Branch to adopt (default: current)"),
    parent: str | None = typer.Option(None, "--parent", "-p", help="Parent branch"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-adopt even if already tracked"),
):
    """
    Adopt an existing branch for shortcake tracking.

    Adds a Shortcake-Parent trailer to the first commit of the branch.
    This amends the commit, changing its SHA.

    Examples:
        sc adopt                     # Adopt current branch
        sc adopt feature-1           # Adopt specific branch
        sc adopt feature-1 -p dev    # Adopt with explicit parent
    """
    repo_path = Path.cwd()

    # Resolve branch
    if branch is None:
        branch = git.get_current_branch(repo_path)
        if git.is_trunk_branch(repo_path, branch):
            output.error(f"Cannot adopt trunk branch '{branch}'")
            raise typer.Exit(1)

    # Check branch exists
    if not git.branch_exists(repo_path, branch):
        output.error(f"Branch '{branch}' does not exist")
        raise typer.Exit(1)

    # Check if already tracked
    first_commit = git.get_first_commit_on_branch(repo_path, branch, parent or "main")
    if first_commit and storage.has_trailer(repo_path, first_commit, "Shortcake-Parent"):
        if not force:
            existing_parent = storage.read_trailer(repo_path, first_commit, "Shortcake-Parent")
            output.warning(f"Branch '{branch}' is already tracked (parent: {existing_parent})")
            output.info("Use --force to re-adopt")
            raise typer.Exit(1)

    # Detect or validate parent
    if parent is None:
        parent = git.detect_parent_branch(repo_path, branch)
        if parent is None:
            output.error("Could not auto-detect parent branch. Use --parent to specify.")
            raise typer.Exit(1)
        output.info(f"Detected parent: {parent}")
    else:
        if not git.branch_exists(repo_path, parent):
            output.error(f"Parent branch '{parent}' does not exist")
            raise typer.Exit(1)

    # Verify branch has commits after parent
    first_commit = git.get_first_commit_on_branch(repo_path, branch, parent)
    if not first_commit:
        output.error(f"Branch '{branch}' has no commits after '{parent}'")
        raise typer.Exit(1)

    # Save current branch to return later
    original_branch = git.get_current_branch(repo_path)

    # Checkout the branch and its first commit
    git.checkout_branch(repo_path, branch)

    # We need to be AT the first commit to amend it
    # This is tricky - we need to do an interactive rebase or use git commit --fixup
    # Simpler approach: if first commit is not HEAD, we need rebase
    head_sha = git.get_commit_sha(repo_path, "HEAD")

    if head_sha == first_commit:
        # Simple case: first commit is the only commit (or we're at it)
        storage.add_trailer(repo_path, "Shortcake-Parent", parent)
    else:
        # Need to amend a commit that's not HEAD
        # Use rebase --exec to amend the specific commit
        _amend_first_commit_trailer(repo_path, first_commit, parent)

    # Update cache
    storage.update_cache_entry(repo_path, branch, parent)

    # Return to original branch
    if original_branch != branch:
        git.checkout_branch(repo_path, original_branch)

    output.success(f"Adopted '{branch}' with parent '{parent}'")
    output.warning("Note: First commit was amended. You may need to force push.")


def _amend_first_commit_trailer(repo_path: Path, first_commit: str, parent: str) -> None:
    """Amend a trailer onto a commit that's not HEAD using rebase."""
    # Create a script that adds the trailer
    import tempfile

    script = f'''
    if [ "$(git rev-parse HEAD)" = "{first_commit}" ] || git log -1 --format=%H | grep -q "^{first_commit[:8]}"; then
        git commit --amend --no-edit --trailer "Shortcake-Parent:{parent}"
    fi
    '''

    # Get the parent of first_commit for rebase base
    parent_of_first = git.get_parent_commit(repo_path, first_commit)

    subprocess.run(
        ["git", "rebase", "--exec", f"bash -c '{script}'", parent_of_first],
        cwd=repo_path,
        check=True,
    )
```

### 3.2 Simpler Alternative Implementation

The rebase approach above is complex. Here's a simpler alternative that only works when first commit = HEAD:

```python
@app.command()
def adopt(
    branch: str | None = typer.Argument(None, help="Branch to adopt (default: current)"),
    parent: str | None = typer.Option(None, "--parent", "-p", help="Parent branch"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-adopt even if already tracked"),
):
    """Adopt an existing branch for shortcake tracking."""
    repo_path = Path.cwd()

    # ... validation same as above ...

    # Check if first commit is HEAD
    first_commit = git.get_first_commit_on_branch(repo_path, branch, parent)
    head_sha = git.get_commit_sha(repo_path, branch)

    if first_commit != head_sha:
        output.error(
            f"Branch has multiple commits. Cannot adopt directly.\n"
            f"To adopt, first squash your commits or use interactive rebase."
        )
        raise typer.Exit(1)

    # Checkout and amend
    original_branch = git.get_current_branch(repo_path)
    git.checkout_branch(repo_path, branch)
    storage.add_trailer(repo_path, "Shortcake-Parent", parent)

    # Update cache
    storage.update_cache_entry(repo_path, branch, parent)

    # Return to original
    if original_branch != branch:
        git.checkout_branch(repo_path, original_branch)

    output.success(f"Adopted '{branch}' with parent '{parent}'")
```

**Decision needed:** Do we support adopting branches with multiple commits, or require squash first?

---

## 4. Tests

### 4.1 Unit Tests (`tests/unit/test_adopt.py`)

```python
def test_detect_parent_main(git_repo):
    """Auto-detect main as parent."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: first")

    parent = git.detect_parent_branch(git_repo.path, "feature-1")
    assert parent == "main"


def test_detect_parent_master(git_repo_master):
    """Auto-detect master as parent when main doesn't exist."""
    git_repo_master.create_branch("feature-1")
    git_repo_master.commit("feat: first")

    parent = git.detect_parent_branch(git_repo_master.path, "feature-1")
    assert parent == "master"


def test_get_first_commit_single(git_repo):
    """Get first commit when branch has one commit."""
    git_repo.create_branch("feature-1")
    commit_sha = git_repo.commit("feat: first")

    first = git.get_first_commit_on_branch(git_repo.path, "feature-1", "main")
    assert first == commit_sha


def test_get_first_commit_multiple(git_repo):
    """Get first commit when branch has multiple commits."""
    git_repo.create_branch("feature-1")
    first_sha = git_repo.commit("feat: first")
    git_repo.commit("feat: second")
    git_repo.commit("feat: third")

    first = git.get_first_commit_on_branch(git_repo.path, "feature-1", "main")
    assert first == first_sha


def test_add_trailer(git_repo):
    """Add trailer to HEAD commit."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: first")

    storage.add_trailer(git_repo.path, "Shortcake-Parent", "main")

    trailer = storage.read_trailer(git_repo.path, "HEAD", "Shortcake-Parent")
    assert trailer == "main"


def test_read_trailer_missing(git_repo):
    """Read non-existent trailer returns None."""
    git_repo.commit("feat: no trailer")

    trailer = storage.read_trailer(git_repo.path, "HEAD", "Shortcake-Parent")
    assert trailer is None
```

### 4.2 Integration Tests (`tests/integration/test_adopt.py`)

```python
def test_adopt_current_branch(cli, git_repo):
    """Adopt current branch with auto-detected parent."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature")

    result = cli("adopt")

    assert result.exit_code == 0
    assert "Adopted" in result.output
    assert "feature-1" in result.output

    # Verify trailer was added
    trailer = storage.read_trailer(git_repo.path, "HEAD", "Shortcake-Parent")
    assert trailer == "main"


def test_adopt_specific_branch(cli, git_repo):
    """Adopt a specific branch by name."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature")
    git_repo.checkout("main")

    result = cli("adopt", "feature-1")

    assert result.exit_code == 0
    assert "Adopted" in result.output


def test_adopt_with_explicit_parent(cli, git_repo):
    """Adopt with explicit parent branch."""
    git_repo.create_branch("dev")
    git_repo.commit("dev commit")
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature")

    result = cli("adopt", "--parent", "dev")

    assert result.exit_code == 0
    trailer = storage.read_trailer(git_repo.path, "HEAD", "Shortcake-Parent")
    assert trailer == "dev"


def test_adopt_already_tracked(cli, git_repo):
    """Reject adopting already tracked branch."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature", trailers={"Shortcake-Parent": "main"})

    result = cli("adopt")

    assert result.exit_code == 1
    assert "already tracked" in result.output


def test_adopt_force_readopt(cli, git_repo):
    """Force re-adopt an already tracked branch."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature", trailers={"Shortcake-Parent": "main"})

    # Create dev branch
    git_repo.checkout("main")
    git_repo.create_branch("dev")
    git_repo.commit("dev commit")
    git_repo.checkout("feature-1")

    result = cli("adopt", "--force", "--parent", "dev")

    assert result.exit_code == 0
    trailer = storage.read_trailer(git_repo.path, "HEAD", "Shortcake-Parent")
    assert trailer == "dev"


def test_adopt_trunk_branch_rejected(cli, git_repo):
    """Cannot adopt main/master."""
    result = cli("adopt", "main")

    assert result.exit_code == 1
    assert "Cannot adopt trunk" in result.output


def test_adopt_nonexistent_branch(cli, git_repo):
    """Error when branch doesn't exist."""
    result = cli("adopt", "nonexistent")

    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_adopt_no_commits_after_parent(cli, git_repo):
    """Error when branch has no commits after parent."""
    git_repo.create_branch("feature-1")  # No commits

    result = cli("adopt")

    assert result.exit_code == 1
    assert "no commits" in result.output


def test_adopt_updates_cache(cli, git_repo):
    """Adopting updates the local cache."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature")

    cli("adopt")

    cache = storage.read_cache(git_repo.path)
    assert "feature-1" in cache["cache"]
    assert cache["cache"]["feature-1"]["parent"] == "main"


def test_adopt_returns_to_original_branch(cli, git_repo):
    """After adopting different branch, return to original."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature")
    git_repo.checkout("main")

    original = git.get_current_branch(git_repo.path)
    cli("adopt", "feature-1")
    after = git.get_current_branch(git_repo.path)

    assert original == after == "main"
```

### 4.3 Edge Case Tests

```python
def test_adopt_branch_with_special_chars(cli, git_repo):
    """Adopt branch with special characters in name."""
    git_repo.create_branch("feature/add-auth")
    git_repo.commit("feat: add feature")

    result = cli("adopt")

    assert result.exit_code == 0


def test_adopt_preserves_commit_message(cli, git_repo):
    """Adopting preserves original commit message."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: important message\n\nWith body text")

    cli("adopt")

    message = git.get_commit_message(git_repo.path, "HEAD")
    assert "feat: important message" in message
    assert "With body text" in message
    assert "Shortcake-Parent: main" in message


def test_adopt_preserves_existing_trailers(cli, git_repo):
    """Adopting preserves other existing trailers."""
    git_repo.create_branch("feature-1")
    git_repo.commit("feat: add feature", trailers={"Signed-off-by": "Test User"})

    cli("adopt")

    message = git.get_commit_message(git_repo.path, "HEAD")
    assert "Signed-off-by: Test User" in message
    assert "Shortcake-Parent: main" in message
```

---

## 5. Manual Testing Checklist

- [ ] `sc adopt` on branch with single commit
- [ ] `sc adopt` on branch with multiple commits (should error or handle)
- [ ] `sc adopt feature-1` from different branch
- [ ] `sc adopt --parent dev` with explicit parent
- [ ] `sc adopt --force` to re-adopt
- [ ] `sc adopt` on main (should error)
- [ ] `sc adopt` on non-existent branch (should error)
- [ ] `sc adopt` on branch with no commits (should error)
- [ ] Verify commit SHA changes after adopt
- [ ] Verify `git push --force` is needed after adopt
- [ ] Verify trailer is readable after adopt

---

## 6. Definition of Done

- [ ] All unit tests pass (100% coverage of new code)
- [ ] All integration tests pass
- [ ] Manual testing checklist complete
- [ ] Command documented with `--help`
- [ ] Error messages are clear and actionable
- [ ] Works on Linux and macOS

---

## 7. Open Questions

1. **Multiple commits:** Should we support adopting branches with multiple commits (requires rebase), or require the user to squash first?

   **Recommendation:** Start simple - require single commit or first commit = HEAD. Add rebase support later if needed.

2. **Amend warning:** Should we require `--yes` flag since amend changes SHA and requires force push?

   **Recommendation:** Just warn, don't require confirmation for CLI tools.

3. **Detached HEAD:** What if user is in detached HEAD state?

   **Recommendation:** Error with clear message.

---

*Implementation Time Estimate: 1-2 days*
*Depends on: Core infrastructure (adapters/git, adapters/storage)*

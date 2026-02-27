# Testing Strategy

## Fixtures

### `temp_repo`

Create a fresh git repository for each test.

```python
@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary git repo with initial commit."""
    repo = porcelain.init(tmp_path)
    # Create initial commit on main
    (tmp_path / "README.md").write_text("# Test")
    porcelain.add(repo, paths=["README.md"])
    porcelain.commit(repo, message=b"Initial commit")
    return repo
```

### `stacked_repo`

Repository with a pre-built stack for testing.

```python
@pytest.fixture
def stacked_repo(temp_repo):
    """
    Repo with stack:

    feature-2
    │
    feature-1
    │
    main
    """
    # Create feature-1 with trailer
    # Create feature-2 with trailer
    return temp_repo
```

### CLI Runner

Use Typer's test runner for command testing.

```python
from typer.testing import CliRunner
from shortcake.cli import app

runner = CliRunner()

def test_adopt():
    result = runner.invoke(app, ["adopt"])
    assert result.exit_code == 0
```

## Test Patterns

### Happy Path

Test the expected usage:

```python
def test_adopt_current_branch(temp_repo):
    # Create a branch
    # Run sc adopt
    # Verify trailer added
```

### Edge Cases

Test boundary conditions:

```python
def test_adopt_already_tracked(stacked_repo):
    # Try to adopt already tracked branch
    # Verify error message
```

### Error Conditions

Test error handling:

```python
def test_adopt_on_trunk(temp_repo):
    # On main branch
    # Run sc adopt
    # Verify error about trunk branch
```

## Integration Tests

Test commands working together:

```python
def test_create_then_ls(temp_repo):
    # sc create -m "feat: something"
    # sc ls
    # Verify branch appears in output
```

## Coverage Goals

- All commands: 100% line coverage
- All error paths tested
- All edge cases documented and tested

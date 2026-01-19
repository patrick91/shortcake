import pytest
from pathlib import Path
from dulwich.repo import Repo
from dulwich import porcelain


@pytest.fixture
def temp_repo(tmp_path: Path) -> Repo:
    """Create a temporary git repo with initial commit on main."""
    repo = Repo.init(tmp_path, default_branch=b"main")

    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    return repo


@pytest.fixture
def repo_with_feature(temp_repo: Repo, tmp_path: Path) -> Repo:
    """Repo with main and a feature branch (1 commit)."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add a commit on feature
    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    porcelain.add(temp_repo, paths=[str(test_file)])
    porcelain.commit(temp_repo, message=b"Add feature")

    return temp_repo

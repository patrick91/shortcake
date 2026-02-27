"""Fixtures for navigation command tests."""

from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from shortcake._trailers import Trailers


@pytest.fixture
def repo_with_stack(tmp_path: Path) -> Repo:
    """
    Create a repo with a linear stack: main → branch_a → branch_b → branch_c.

    All branches are tracked with Shortcake-Parent trailers.
    """
    repo = Repo.init(tmp_path, default_branch=b"main")

    # Create initial commit on main
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create branch_a from main
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    # Create branch_b from branch_a
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    # Create branch_c from branch_b
    branch_b_sha = repo.refs[b"refs/heads/branch_b"]
    repo.refs[b"refs/heads/branch_c"] = branch_b_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    trailers_c = Trailers(parent_branch="branch_b")
    msg_c = trailers_c.apply_to("feat: branch c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    return repo


@pytest.fixture
def repo_with_fork(tmp_path: Path) -> Repo:
    """
    Create a repo with a forked stack: main → branch_a → (branch_b, branch_c).

    branch_b and branch_c both have branch_a as parent.
    """
    repo = Repo.init(tmp_path, default_branch=b"main")

    # Create initial commit on main
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create branch_a from main
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    trailers_a = Trailers(parent_branch="main")
    msg_a = trailers_a.apply_to("feat: branch a")
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(repo, paths=[str(file_a)])
    porcelain.commit(repo, message=msg_a.encode())

    # Create branch_b from branch_a
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    trailers_b = Trailers(parent_branch="branch_a")
    msg_b = trailers_b.apply_to("feat: branch b")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(repo, paths=[str(file_b)])
    porcelain.commit(repo, message=msg_b.encode())

    # Create branch_c from branch_a (fork!)
    repo.refs[b"refs/heads/branch_c"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_c")

    trailers_c = Trailers(parent_branch="branch_a")
    msg_c = trailers_c.apply_to("feat: branch c")
    file_c = tmp_path / "c.txt"
    file_c.write_text("c")
    porcelain.add(repo, paths=[str(file_c)])
    porcelain.commit(repo, message=msg_c.encode())

    return repo


@pytest.fixture
def repo_with_tracked_feature(tmp_path: Path) -> Repo:
    """
    Create a repo with main and a single tracked feature branch.

    main → feature (tracked with trailer)
    """
    repo = Repo.init(tmp_path, default_branch=b"main")

    # Create initial commit on main
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # Create feature branch from main
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/feature"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    trailers = Trailers(parent_branch="main")
    msg = trailers.apply_to("feat: add feature")
    file_f = tmp_path / "feature.txt"
    file_f.write_text("feature")
    porcelain.add(repo, paths=[str(file_f)])
    porcelain.commit(repo, message=msg.encode())

    return repo

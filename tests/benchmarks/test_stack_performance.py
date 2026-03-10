"""Performance benchmarks for stack operations.

Run with: uv run pytest tests/benchmarks/ --benchmark-only
"""

from pathlib import Path

import pytest

from shortcake import _git as git
from shortcake._trailers import Trailers
from tests._git_helpers import Repo, add_paths, commit, init_repo, reset_hard


@pytest.fixture
def repo_with_many_branches(tmp_path: Path) -> Repo:
    """Create a repo with 100 tracked branches to benchmark performance.

    Structure: main → branch_0 → branch_1 → ... → branch_99
    Each branch has 1 commit with a Shortcake-Parent trailer.
    """
    repo = init_repo(tmp_path)

    # Create initial commit on main
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")

    # Create 100 branches in a chain
    parent_branch = "main"
    for i in range(100):
        branch_name = f"branch_{i}"
        parent_sha = repo.refs[f"refs/heads/{parent_branch}".encode()]
        repo.refs[f"refs/heads/{branch_name}".encode()] = parent_sha
        repo.refs.set_symbolic_ref(b"HEAD", f"refs/heads/{branch_name}".encode())

        # Add a file and commit with trailer
        file_path = tmp_path / f"file_{i}.txt"
        file_path.write_text(f"content {i}")
        add_paths(repo, file_path)

        trailers = Trailers(parent_branch=parent_branch)
        message = trailers.apply_to(f"feat: add branch {i}")
        commit(repo, message)

        parent_branch = branch_name

    # Switch back to main
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    reset_hard(repo)

    return repo


@pytest.fixture
def repo_with_wide_branches(tmp_path: Path) -> Repo:
    """Create a repo with 100 branches all parented to main (wide, not deep).

    Structure: main → (branch_0, branch_1, ..., branch_99)
    All branches are direct children of main.
    """
    repo = init_repo(tmp_path)

    # Create initial commit on main
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    add_paths(repo, readme)
    commit(repo, b"Initial commit")
    main_sha = repo.refs[b"refs/heads/main"]

    # Create 100 branches all from main
    for i in range(100):
        branch_name = f"branch_{i}"
        repo.refs[f"refs/heads/{branch_name}".encode()] = main_sha
        repo.refs.set_symbolic_ref(b"HEAD", f"refs/heads/{branch_name}".encode())

        # Add a file and commit with trailer
        file_path = tmp_path / f"file_{i}.txt"
        file_path.write_text(f"content {i}")
        add_paths(repo, file_path)

        trailers = Trailers(parent_branch="main")
        message = trailers.apply_to(f"feat: add branch {i}")
        commit(repo, message)

    # Switch back to main
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    reset_hard(repo)

    return repo


class TestGetBranchParentPerformance:
    """Benchmarks for get_branch_parent()."""

    def test_get_branch_parent_single_call(
        self, benchmark, repo_with_many_branches: Repo
    ) -> None:
        """Benchmark a single get_branch_parent call."""
        all_branches = set(git.get_all_local_branches(repo_with_many_branches))

        def run():
            return git.get_branch_parent(
                repo_with_many_branches, "branch_50", all_branches
            )

        result = benchmark(run)
        assert result == "branch_49"

    def test_get_branch_parent_all_branches(
        self, benchmark, repo_with_many_branches: Repo
    ) -> None:
        """Benchmark get_branch_parent for all branches (current O(n²) behavior)."""
        all_branches = set(git.get_all_local_branches(repo_with_many_branches))
        branches = list(all_branches)

        def run():
            results = {}
            for branch in branches:
                results[branch] = git.get_branch_parent(
                    repo_with_many_branches, branch, all_branches
                )
            return results

        result = benchmark(run)
        assert result["branch_50"] == "branch_49"
        assert result["branch_0"] == "main"
        assert result["main"] is None


class TestGetBranchChildrenPerformance:
    """Benchmarks for get_branch_children()."""

    def test_get_branch_children_deep_stack(
        self, benchmark, repo_with_many_branches: Repo
    ) -> None:
        """Benchmark get_branch_children on a deep stack (each branch has 1 child)."""

        def run():
            return git.get_branch_children(repo_with_many_branches, "main")

        result = benchmark(run)
        assert result == ["branch_0"]  # main only has branch_0 as direct child

    def test_get_branch_children_wide_stack(
        self, benchmark, repo_with_wide_branches: Repo
    ) -> None:
        """Benchmark get_branch_children when parent has many children."""

        def run():
            return git.get_branch_children(repo_with_wide_branches, "main")

        result = benchmark(run)
        assert len(result) == 100  # main has 100 direct children

    def test_get_branch_children_middle_of_stack(
        self, benchmark, repo_with_many_branches: Repo
    ) -> None:
        """Benchmark get_branch_children from middle of stack."""

        def run():
            return git.get_branch_children(repo_with_many_branches, "branch_50")

        result = benchmark(run)
        assert result == ["branch_51"]


class TestUpCommandPerformance:
    """End-to-end benchmarks for the up command logic."""

    def test_up_command_flow(self, benchmark, repo_with_many_branches: Repo) -> None:
        """Benchmark the full up command flow (what actually runs)."""
        # Switch to branch_50
        repo_with_many_branches.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_50")

        def run():
            current = git.get_current_branch(repo_with_many_branches)
            children = git.get_branch_children(repo_with_many_branches, current)
            return current, children

        current, children = benchmark(run)
        assert current == "branch_50"
        assert children == ["branch_51"]


class TestDownCommandPerformance:
    """End-to-end benchmarks for the down command logic."""

    def test_down_command_flow(self, benchmark, repo_with_many_branches: Repo) -> None:
        """Benchmark the full down command flow."""
        # Switch to branch_50
        repo_with_many_branches.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_50")

        def run():
            current = git.get_current_branch(repo_with_many_branches)
            all_branches = set(git.get_all_local_branches(repo_with_many_branches))
            parent = git.get_branch_parent(
                repo_with_many_branches, current, all_branches
            )
            return current, parent

        current, parent = benchmark(run)
        assert current == "branch_50"
        assert parent == "branch_49"

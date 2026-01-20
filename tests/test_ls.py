from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake.commands.adopt import _adopt
from shortcake.commands.ls import _get_branch_parent, _ls


def test_ls_no_tracked(temp_repo: Repo) -> None:
    """Test ls with no tracked branches returns empty string."""
    result = _ls(temp_repo)
    assert result == ""


def test_ls_single_tracked(repo_with_feature: Repo) -> None:
    """Test ls with a single tracked branch."""
    # First adopt the feature branch
    _adopt(repo_with_feature)

    result = _ls(repo_with_feature)

    assert "feature" in result
    assert "main" in result
    assert "◉ feature (current)" in result
    assert "◯ main" in result


def test_ls_current_highlighted(repo_with_feature: Repo) -> None:
    """Test that current branch is highlighted with ◉."""
    _adopt(repo_with_feature)

    # Check from feature branch (current)
    result = _ls(repo_with_feature)
    assert "◉ feature (current)" in result

    # Switch to main and check
    repo_with_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    result = _ls(repo_with_feature)
    assert "◉ main (current)" in result
    assert "◯ feature" in result


def test_ls_multi_commit_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls finds trailer in first commit of multi-commit branch."""
    # Create feature branch
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature")

    # Add first commit
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    porcelain.add(temp_repo, paths=[str(file1)])
    porcelain.commit(temp_repo, message=b"First feature commit")

    # Add second commit
    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")
    porcelain.add(temp_repo, paths=[str(file2)])
    porcelain.commit(temp_repo, message=b"Second feature commit")

    # Adopt the branch (adds trailer to first commit)
    _adopt(temp_repo)

    result = _ls(temp_repo)
    assert "feature" in result
    assert "main" in result


def test_ls_chain_of_branches(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls with A → B → C chain."""
    # Create feature-a off main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/feature-a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature-a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    porcelain.add(temp_repo, paths=[str(file_a)])
    porcelain.commit(temp_repo, message=b"Add feature-a")

    _adopt(temp_repo, branch="feature-a", parent="main")

    # Create feature-b off feature-a
    feature_a_sha = temp_repo.refs[b"refs/heads/feature-a"]
    temp_repo.refs[b"refs/heads/feature-b"] = feature_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/feature-b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    porcelain.add(temp_repo, paths=[str(file_b)])
    porcelain.commit(temp_repo, message=b"Add feature-b")

    _adopt(temp_repo, branch="feature-b", parent="feature-a")

    result = _ls(temp_repo)

    # Verify all branches in output
    assert "feature-a" in result
    assert "feature-b" in result
    assert "main" in result

    # Verify order (top to bottom should be: feature-b, feature-a, main)
    lines = [line for line in result.split("\n") if line.strip()]
    branch_lines = [line for line in lines if "feature" in line or "main" in line]
    # feature-b should be before feature-a in output (children above parents)
    fb_idx = next(i for i, line in enumerate(branch_lines) if "feature-b" in line)
    fa_idx = next(i for i, line in enumerate(branch_lines) if "feature-a" in line)
    main_idx = next(i for i, line in enumerate(branch_lines) if "main" in line)
    assert fb_idx < fa_idx < main_idx


def test_get_branch_parent_no_trailer(temp_repo: Repo) -> None:
    """Test _get_branch_parent returns None when no trailer exists."""
    all_branches = set(git.get_all_local_branches(temp_repo))
    result = _get_branch_parent(temp_repo, "main", all_branches)
    assert result is None


def test_get_branch_parent_with_trailer(repo_with_feature: Repo) -> None:
    """Test _get_branch_parent finds trailer."""
    _adopt(repo_with_feature)
    all_branches = set(git.get_all_local_branches(repo_with_feature))
    result = _get_branch_parent(repo_with_feature, "feature", all_branches)
    assert result == "main"


def test_get_branch_parent_nonexistent_branch(temp_repo: Repo) -> None:
    """Test _get_branch_parent with nonexistent branch."""
    all_branches = set(git.get_all_local_branches(temp_repo))
    result = _get_branch_parent(temp_repo, "nonexistent", all_branches)
    assert result is None


def test_ls_detached_head(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test ls works when in detached HEAD state."""
    _adopt(repo_with_feature)

    # Detach HEAD by writing SHA directly to HEAD file
    head_sha = repo_with_feature.refs[b"refs/heads/feature"]
    head_file = tmp_path / ".git" / "HEAD"
    head_file.write_text(head_sha.decode() + "\n")

    result = _ls(repo_with_feature)

    # Should still show the tree, just without current marker
    assert "feature" in result
    assert "main" in result
    # No branch should be marked as current
    assert "(current)" not in result

"""Tests for trailer preservation during restack when --empty=drop drops commits."""

from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._trailers import Trailers
from shortcake.commands.restack import _restack


def switch_branch(repo: Repo, branch: str) -> None:
    """Properly switch branches with index and working tree reset."""
    ref = f"refs/heads/{branch}".encode()
    repo.refs.set_symbolic_ref(b"HEAD", ref)
    porcelain.reset(repo, "hard")


def test_restack_preserves_trailer_when_commit_becomes_empty(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Trailer is preserved when the commit carrying it becomes empty after rebase.

    Scenario:
    1. Create branch_a from main with a tracked commit (has trailer + file changes)
    2. Squash-merge branch_a's changes into main (main now has the same file changes)
    3. Run restack — branch_a's commit becomes empty and --empty=drop removes it
    4. Without the fix, branch_a loses its trailer and appears untracked
    5. With the fix, the trailer is restored on the remaining/new first commit
    """
    # Create branch_a from main with a single commit that has trailer + file changes
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())

    # Now simulate squash-merge: add the same file changes to main
    switch_branch(temp_repo, "main")
    file_a_on_main = tmp_path / "a.txt"
    file_a_on_main.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a_on_main)])
    porcelain.commit(temp_repo, message=b"chore: squash merge branch_a changes")

    # Switch to branch_a for restack
    switch_branch(temp_repo, "branch_a")

    # Verify branch_a is tracked before restack
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_a", all_branches) == "main"

    # Run restack — branch_a's commit will become empty
    result = _restack(temp_repo)

    assert result.restacked_branches == ["branch_a"]
    assert result.skipped_empty_commits is True

    # CRITICAL: branch_a should still be tracked after restack
    all_branches = set(git.get_all_local_branches(temp_repo))
    parent = git.get_branch_parent(temp_repo, "branch_a", all_branches)
    assert parent == "main", (
        f"branch_a should still be tracked with parent 'main', but got {parent!r}"
    )


def test_restack_preserves_trailer_multi_commit_branch(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Trailer preserved when first commit is dropped but later commits survive.

    Scenario: branch has 2 commits — the first (with trailer) becomes empty,
    the second has unique changes. After restack, the trailer should be on
    the surviving commit.
    """
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # First commit: trailer + file changes that will become empty
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a first commit")
    porcelain.commit(temp_repo, message=message_a.encode())

    # Second commit: unique file changes that won't become empty
    file_b = tmp_path / "unique.txt"
    file_b.write_text("unique content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    porcelain.commit(temp_repo, message=b"feat: branch a second commit")

    # Simulate squash-merge of first commit's changes into main
    switch_branch(temp_repo, "main")
    file_a_on_main = tmp_path / "a.txt"
    file_a_on_main.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a_on_main)])
    porcelain.commit(temp_repo, message=b"chore: squash merge first commit changes")

    # Switch to branch_a for restack
    switch_branch(temp_repo, "branch_a")

    # Verify tracked before
    all_branches = set(git.get_all_local_branches(temp_repo))
    assert git.get_branch_parent(temp_repo, "branch_a", all_branches) == "main"

    # Run restack
    result = _restack(temp_repo)

    assert result.restacked_branches == ["branch_a"]
    assert result.skipped_empty_commits is True

    # CRITICAL: branch_a should still be tracked
    all_branches = set(git.get_all_local_branches(temp_repo))
    parent = git.get_branch_parent(temp_repo, "branch_a", all_branches)
    assert parent == "main", (
        f"branch_a should still be tracked with parent 'main', but got {parent!r}"
    )

    # The surviving commit should have the unique content
    branch_a_head = git.get_branch_head(temp_repo, "branch_a")
    tree = temp_repo[temp_repo[branch_a_head].tree]
    file_names = [item.path.decode() for item in tree.items()]
    assert "unique.txt" in file_names


def test_restack_preserves_trailer_in_stack_with_empty_commit(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Trailer preserved in a stack where a middle branch's commit becomes empty.

    Scenario: main → branch_a → branch_b
    branch_a's changes are squash-merged into main, making branch_a's commit empty.
    After restack, both branches should still be tracked.
    """
    # Create branch_a from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/branch_a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")

    # Commit on branch_a with trailer
    file_a = tmp_path / "a.txt"
    file_a.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: branch a")
    porcelain.commit(temp_repo, message=message_a.encode())
    branch_a_sha = temp_repo.refs[b"refs/heads/branch_a"]

    # Create branch_b from branch_a
    temp_repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")

    # Commit on branch_b with trailer
    file_b = tmp_path / "b.txt"
    file_b.write_text("branch b content")
    porcelain.add(temp_repo, paths=[str(file_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: branch b")
    porcelain.commit(temp_repo, message=message_b.encode())

    # Squash-merge branch_a's changes into main
    switch_branch(temp_repo, "main")
    file_a_on_main = tmp_path / "a.txt"
    file_a_on_main.write_text("branch a content")
    porcelain.add(temp_repo, paths=[str(file_a_on_main)])
    porcelain.commit(temp_repo, message=b"chore: squash merge branch_a")

    # Switch to branch_b for restack
    switch_branch(temp_repo, "branch_b")

    # Run restack
    result = _restack(temp_repo)

    assert "branch_a" in result.restacked_branches

    # branch_a should still be tracked
    all_branches = set(git.get_all_local_branches(temp_repo))
    parent_a = git.get_branch_parent(temp_repo, "branch_a", all_branches)
    assert parent_a == "main", (
        f"branch_a should still be tracked with parent 'main', but got {parent_a!r}"
    )

    # branch_b should still be tracked
    parent_b = git.get_branch_parent(temp_repo, "branch_b", all_branches)
    assert parent_b == "branch_a", (
        f"branch_b should still be tracked with parent 'branch_a', but got {parent_b!r}"
    )

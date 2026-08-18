from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from inline_snapshot import snapshot

from shortcake import _git as git
from shortcake.commands.adopt import _adopt
from shortcake.commands.ls import _build_tree, _collect_nodes, _fetch_pr_info, _ls
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    get_ref,
    reset_hard,
    run_git,
    set_ref,
    set_remote,
    switch_branch,
)


def test_ls_no_tracked(temp_repo: Repo) -> None:
    """Test ls with no tracked branches returns empty string."""
    result = _ls(temp_repo)
    assert result == ""


def test_ls_single_tracked(repo_with_feature: Repo) -> None:
    """Test ls with a single tracked branch."""
    _adopt(repo_with_feature)

    result = _ls(repo_with_feature)

    assert result == snapshot("""\
◉ feature (current)
│ [dim]Add feature[/]
│
◯ main
  [dim]Initial commit[/]""")


def test_ls_current_highlighted(repo_with_feature: Repo) -> None:
    """Test that current branch is highlighted with ◉."""
    _adopt(repo_with_feature)

    # Check from feature branch (current)
    result = _ls(repo_with_feature)
    assert result == snapshot("""\
◉ feature (current)
│ [dim]Add feature[/]
│
◯ main
  [dim]Initial commit[/]""")

    # Switch to main and check
    repo_with_feature.set_head("refs/heads/main")
    result = _ls(repo_with_feature)
    assert result == snapshot("""\
◯ feature
│ [dim]Add feature[/]
│
◉ main (current)
  [dim]Initial commit[/]""")


def test_ls_marks_other_worktree(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test ls shows when a branch is checked out in another worktree."""
    _adopt(repo_with_feature)
    switch_branch(repo_with_feature, "main")
    worktree_path = tmp_path / "feature-worktree"
    run_git(repo_with_feature, "worktree", "add", str(worktree_path), "feature")

    result = _ls(repo_with_feature)

    assert f"[dim]worktree: {worktree_path.resolve()}[/]" in result
    assert result.count("worktree:") == 1


def test_ls_multi_commit_branch(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls finds trailer in first commit of multi-commit branch."""
    # Create feature branch
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    # Add first commit
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    add_paths(temp_repo, file1)
    commit(temp_repo, b"First feature commit")

    # Add second commit
    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")
    add_paths(temp_repo, file2)
    commit(temp_repo, b"Second feature commit")

    # Adopt the branch (adds trailer to first commit)
    _adopt(temp_repo)

    result = _ls(temp_repo)
    assert result == snapshot("""\
◉ feature (current)
│ [dim]Second feature commit[/]
│
◯ main
  [dim]Initial commit[/]""")


def test_ls_chain_of_branches(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls with A → B → C chain."""
    # Create feature-a off main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature-a", main_sha)
    temp_repo.set_head("refs/heads/feature-a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(temp_repo, file_a)
    commit(temp_repo, b"Add feature-a")

    _adopt(temp_repo, branch="feature-a", parent="main")

    # Create feature-b off feature-a
    feature_a_sha = get_ref(temp_repo, "refs/heads/feature-a")
    set_ref(temp_repo, "refs/heads/feature-b", feature_a_sha)
    temp_repo.set_head("refs/heads/feature-b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(temp_repo, file_b)
    commit(temp_repo, b"Add feature-b")

    _adopt(temp_repo, branch="feature-b", parent="feature-a")

    result = _ls(temp_repo)

    assert result == snapshot("""\
◉ feature-b (current)
│ [dim]Add feature-b[/]
│
◯ feature-a
│ [dim]Add feature-a[/]
│
◯ main
  [dim]Initial commit[/]""")


def test_ls_parallel_stacks(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls with parallel stacks off main."""
    main_sha = get_ref(temp_repo, "refs/heads/main")

    # Create stack-1-a off main
    set_ref(temp_repo, "refs/heads/stack-1-a", main_sha)
    temp_repo.set_head("refs/heads/stack-1-a")

    file_1a = tmp_path / "stack1a.txt"
    file_1a.write_text("stack1a")
    add_paths(temp_repo, file_1a)
    commit(temp_repo, b"Add stack-1-a")

    _adopt(temp_repo, branch="stack-1-a", parent="main")

    # Create stack-1-b off stack-1-a
    stack_1a_sha = get_ref(temp_repo, "refs/heads/stack-1-a")
    set_ref(temp_repo, "refs/heads/stack-1-b", stack_1a_sha)
    temp_repo.set_head("refs/heads/stack-1-b")

    file_1b = tmp_path / "stack1b.txt"
    file_1b.write_text("stack1b")
    add_paths(temp_repo, file_1b)
    commit(temp_repo, b"Add stack-1-b")

    _adopt(temp_repo, branch="stack-1-b", parent="stack-1-a")

    # Create stack-2-a off main (parallel stack)
    set_ref(temp_repo, "refs/heads/stack-2-a", main_sha)
    temp_repo.set_head("refs/heads/stack-2-a")

    file_2a = tmp_path / "stack2a.txt"
    file_2a.write_text("stack2a")
    add_paths(temp_repo, file_2a)
    commit(temp_repo, b"Add stack-2-a")

    _adopt(temp_repo, branch="stack-2-a", parent="main")

    # Create stack-2-b off stack-2-a
    stack_2a_sha = get_ref(temp_repo, "refs/heads/stack-2-a")
    set_ref(temp_repo, "refs/heads/stack-2-b", stack_2a_sha)
    temp_repo.set_head("refs/heads/stack-2-b")

    file_2b = tmp_path / "stack2b.txt"
    file_2b.write_text("stack2b")
    add_paths(temp_repo, file_2b)
    commit(temp_repo, b"Add stack-2-b")

    _adopt(temp_repo, branch="stack-2-b", parent="stack-2-a")

    result = _ls(temp_repo)

    assert result == snapshot("""\
◯ stack-1-b
│ [dim]Add stack-1-b[/]
│
◯ stack-1-a
│ [dim]Add stack-1-a[/]
│
│ ◉ stack-2-b (current)
│ │ [dim]Add stack-2-b[/]
│ │
│ ◯ stack-2-a
│ │ [dim]Add stack-2-a[/]
│ │
◯─┘ main
    [dim]Initial commit[/]\
""")


def test_get_branch_parent_no_trailer(temp_repo: Repo) -> None:
    """Test get_branch_parent returns None when no trailer exists."""
    all_branches = set(git.get_all_local_branches(temp_repo))
    result = git.get_branch_parent(temp_repo, "main", all_branches)
    assert result is None


def test_get_branch_parent_with_trailer(repo_with_feature: Repo) -> None:
    """Test get_branch_parent finds trailer."""
    _adopt(repo_with_feature)
    all_branches = set(git.get_all_local_branches(repo_with_feature))
    result = git.get_branch_parent(repo_with_feature, "feature", all_branches)
    assert result == "main"


def test_ls_detached_head(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test ls works when in detached HEAD state."""
    _adopt(repo_with_feature)

    # Detach HEAD by writing SHA directly to HEAD file
    head_sha = get_ref(repo_with_feature, "refs/heads/feature")
    head_file = tmp_path / ".git" / "HEAD"
    head_file.write_text(head_sha.decode() + "\n")

    result = _ls(repo_with_feature)

    # Should still show the tree, just without current marker
    assert result == snapshot("""\
◯ feature
│ [dim]Add feature[/]
│
◯ main
  [dim]Initial commit[/]""")


def test_get_branch_parent_stops_at_other_branch_head(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test that walking stops when reaching another branch's HEAD."""
    # Create a scenario where one branch is ahead of another:
    # main: C0 -> C1
    # develop: C0 -> C1 -> C2 (develop is ahead of main)
    #
    # When checking develop (untracked), we walk C2 -> C1
    # C1 is main's HEAD, so we stop there

    # Add another commit to main
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    add_paths(temp_repo, file1)
    commit(temp_repo, b"Second commit on main")

    # Create develop branch from main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/develop", main_sha)
    temp_repo.set_head("refs/heads/develop")

    # Add commit to develop (now develop is ahead of main)
    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")
    add_paths(temp_repo, file2)
    commit(temp_repo, b"Commit on develop")

    # Now check - develop has no trailer, so when we walk its history,
    # we'll hit main's HEAD and stop
    all_branches = set(git.get_all_local_branches(temp_repo))
    result = git.get_branch_parent(temp_repo, "develop", all_branches)

    # Should return None since develop is not tracked (no trailer found)
    assert result is None


def test_get_branch_parent_with_merge_commit(temp_repo: Repo, tmp_path: Path) -> None:
    """Test walking through merge commits (covers the 'already seen' check)."""
    # Create a diamond pattern with merge on a single branch:
    #
    #     C0 (initial)
    #      |
    #     C1
    #    /  \
    #   C2   C3
    #    \  /
    #     M (merge commit with 2 parents)
    #
    # Walking from M with BFS: queue starts [M]
    # - Pop M, add C2, C3 → queue = [C2, C3]
    # - Pop C2, add C1 → queue = [C3, C1]
    # - Pop C3, add C1 → queue = [C1, C1]  (C1 added twice!)
    # - Pop C1 (first), seen={M,C2,C3,C1}, add C0 → queue = [C1, C0]
    # - Pop C1 (second), it's in seen → skip

    # C1
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    add_paths(temp_repo, file1)
    commit(temp_repo, b"C1")

    # Create the diamond with a real merge commit:
    # main gets C2, side gets C3, then main merges side.
    run_git(temp_repo, "branch", "side")

    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")
    add_paths(temp_repo, file2)
    commit(temp_repo, b"C2")

    switch_branch(temp_repo, "side")
    file3 = tmp_path / "file3.txt"
    file3.write_text("content3")
    add_paths(temp_repo, file3)
    commit(temp_repo, b"C3")

    switch_branch(temp_repo, "main")
    run_git(temp_repo, "merge", "--no-ff", "--no-edit", "side")

    # Now when we walk main, we'll visit C1 through both C2 and C3,
    # hitting the "already seen" check
    all_branches = set(git.get_all_local_branches(temp_repo))
    result = git.get_branch_parent(temp_repo, "main", all_branches)

    # No trailer found
    assert result is None


# Tests for _build_tree and _collect_nodes helper functions


def test_build_tree_returns_tree_and_tracked(repo_with_feature: Repo) -> None:
    """Test _build_tree returns both tree and tracked branches set."""
    _adopt(repo_with_feature)

    tree, tracked = _build_tree(repo_with_feature)

    assert len(tree.roots) == 1
    assert "feature" in tracked
    assert "main" not in tracked  # main is not tracked (no trailer)


def test_build_tree_excludes_trunk_after_ff_merge(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Test _build_tree never treats trunk as tracked.

    After ff-merging a tracked branch, trunk's commit history contains
    Shortcake-Parent trailers from the merged commits. _build_tree must
    skip trunk so it doesn't appear as "(parent missing)".
    """
    from shortcake._trailers import Trailers

    # Create a tracked feature branch
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    test_file = tmp_path / "feature.txt"
    test_file.write_text("feature content")
    add_paths(temp_repo, test_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)
    feature_sha = get_ref(temp_repo, "refs/heads/feature")

    # Fast-forward main to feature (simulates merge)
    set_ref(temp_repo, "refs/heads/main", feature_sha)
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)

    # Add a post-merge commit on main
    post = tmp_path / "post.txt"
    post.write_text("post merge")
    add_paths(temp_repo, post)
    commit(temp_repo, b"chore: post merge")

    tree, tracked = _build_tree(temp_repo)

    # main must NOT appear in tracked set
    assert "main" not in tracked
    # The rendered output should not show "(parent missing)"
    output = tree.render()
    assert "(parent missing)" not in output


def test_untracked_branches_not_tracked_after_ff_merge(
    temp_repo: Repo, tmp_path: Path
) -> None:
    """Untracked branches not tracked after ff-merge.

    After ff-merging a tracked branch into trunk, the Shortcake-Parent trailer
    ends up in shared history. Untracked branches forked from trunk must NOT
    pick up those stale trailers when walking their commit history.
    """
    from shortcake._trailers import Trailers

    # Create a tracked feature branch with a trailer
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")

    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature content")
    add_paths(temp_repo, feature_file)
    trailers = Trailers(parent_branch="main")
    message = trailers.apply_to("feat: add feature")
    commit(temp_repo, message)
    feature_sha = get_ref(temp_repo, "refs/heads/feature")

    # Fast-forward main to feature (simulates merge)
    set_ref(temp_repo, "refs/heads/main", feature_sha)
    temp_repo.set_head("refs/heads/main")
    reset_hard(temp_repo)

    # Add a post-merge commit on main so main is ahead of feature
    post = tmp_path / "post.txt"
    post.write_text("post merge")
    add_paths(temp_repo, post)
    commit(temp_repo, b"chore: post merge")
    new_main_sha = get_ref(temp_repo, "refs/heads/main")

    # Create an untracked branch (e.g. dependabot) forked from new main
    set_ref(temp_repo, "refs/heads/dependabot/foo", new_main_sha)
    temp_repo.set_head("refs/heads/dependabot/foo")

    untracked_file = tmp_path / "untracked.txt"
    untracked_file.write_text("untracked content")
    add_paths(temp_repo, untracked_file)
    commit(temp_repo, b"chore: untracked change")

    # Now delete the merged feature branch (as sync would)
    temp_repo.references.delete("refs/heads/feature")

    _tree, tracked = _build_tree(temp_repo)

    # The untracked branch must NOT appear in tracked set
    assert "dependabot/foo" not in tracked
    # Only truly tracked branches should appear
    assert len(tracked) == 0


def test_collect_nodes_flat(repo_with_feature: Repo) -> None:
    """Test _collect_nodes returns all nodes from tree."""
    _adopt(repo_with_feature)

    tree, _ = _build_tree(repo_with_feature)
    nodes = _collect_nodes(tree)

    node_names = {n.name for n in nodes}
    assert "main" in node_names
    assert "feature" in node_names
    assert len(nodes) == 2


def test_collect_nodes_nested(temp_repo: Repo, tmp_path: Path) -> None:
    """Test _collect_nodes with nested branches."""
    # Create feature-a off main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature-a", main_sha)
    temp_repo.set_head("refs/heads/feature-a")

    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    add_paths(temp_repo, file_a)
    commit(temp_repo, b"Add feature-a")

    _adopt(temp_repo, branch="feature-a", parent="main")

    # Create feature-b off feature-a
    feature_a_sha = get_ref(temp_repo, "refs/heads/feature-a")
    set_ref(temp_repo, "refs/heads/feature-b", feature_a_sha)
    temp_repo.set_head("refs/heads/feature-b")

    file_b = tmp_path / "b.txt"
    file_b.write_text("b")
    add_paths(temp_repo, file_b)
    commit(temp_repo, b"Add feature-b")

    _adopt(temp_repo, branch="feature-b", parent="feature-a")

    tree, tracked = _build_tree(temp_repo)
    nodes = _collect_nodes(tree)

    node_names = {n.name for n in nodes}
    assert node_names == {"main", "feature-a", "feature-b"}
    assert tracked == {"feature-a", "feature-b"}


# Tests for PR info fetching in ls command


@respx.mock
def test_ls_fetches_pr_info(repo_with_feature: Repo) -> None:
    """Test ls fetches PR info from GitHub API."""
    _adopt(repo_with_feature)

    # Set up origin remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API for open PR
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "html_url": "https://github.com/owner/repo/pull/123",
                    "base": {"ref": "main"},
                    "title": "Feature PR",
                    "body": "Description",
                    "state": "open",
                    "draft": False,
                }
            ],
        )
    )

    tree, tracked = _build_tree(repo_with_feature)

    # Manually simulate what ls() does for PR fetching
    from shortcake._github import GitHubClient

    branch_nodes = _collect_nodes(tree)
    with GitHubClient("fake-token", "owner", "repo") as gh:
        for node in branch_nodes:
            if node.name not in tracked:
                continue
            pr = gh.get_pr_for_branch(node.name)
            if pr:
                node.pr_number = pr.number
                node.pr_is_draft = pr.is_draft

    # Check the feature node has PR info
    feature_node = next(n for n in branch_nodes if n.name == "feature")
    assert feature_node.pr_number == 123
    assert feature_node.pr_is_draft is False


@respx.mock
def test_ls_fetches_native_stack_membership(
    repo_with_feature: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _adopt(repo_with_feature)
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")
    monkeypatch.setenv("GH_TOKEN", "token")
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 123,
                    "html_url": "https://github.com/owner/repo/pull/123",
                    "base": {"ref": "main"},
                    "title": "Feature PR",
                    "body": "",
                    "state": "open",
                    "draft": False,
                    "stack": {
                        "id": 100,
                        "number": 7,
                        "size": 2,
                        "position": 1,
                        "base": {"ref": "main", "sha": "abc"},
                    },
                }
            ],
        )
    )
    tree, tracked = _build_tree(repo_with_feature)

    assert _fetch_pr_info(repo_with_feature, tree, tracked, quiet=True) is None

    feature = next(node for node in _collect_nodes(tree) if node.name == "feature")
    assert feature.native_stack_number == 7
    assert feature.native_stack_position == 1
    assert feature.native_stack_size == 2


@respx.mock
def test_ls_fetches_draft_pr_info(repo_with_feature: Repo) -> None:
    """Test ls fetches draft PR info from GitHub API."""
    _adopt(repo_with_feature)

    # Set up origin remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API for draft PR
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 456,
                    "html_url": "https://github.com/owner/repo/pull/456",
                    "base": {"ref": "main"},
                    "title": "Draft Feature",
                    "body": "",
                    "state": "open",
                    "draft": True,
                }
            ],
        )
    )

    tree, tracked = _build_tree(repo_with_feature)

    from shortcake._github import GitHubClient

    branch_nodes = _collect_nodes(tree)
    with GitHubClient("fake-token", "owner", "repo") as gh:
        for node in branch_nodes:
            if node.name not in tracked:
                continue
            pr = gh.get_pr_for_branch(node.name)
            if pr:
                node.pr_number = pr.number
                node.pr_is_draft = pr.is_draft

    feature_node = next(n for n in branch_nodes if n.name == "feature")
    assert feature_node.pr_number == 456
    assert feature_node.pr_is_draft is True


@respx.mock
def test_ls_fetches_merged_pr_info(repo_with_feature: Repo) -> None:
    """Test ls fetches merged PR info when no open PR exists."""
    _adopt(repo_with_feature)

    # Set up origin remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API - no open PR
    open_prs_route = respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        side_effect=[
            httpx.Response(200, json=[]),  # First call: open PRs (empty)
            httpx.Response(  # Second call: closed PRs
                200,
                json=[
                    {
                        "number": 789,
                        "merged_at": "2024-01-15T10:30:00Z",
                    }
                ],
            ),
        ]
    )

    tree, tracked = _build_tree(repo_with_feature)

    from shortcake._github import GitHubClient

    branch_nodes = _collect_nodes(tree)
    with GitHubClient("fake-token", "owner", "repo") as gh:
        for node in branch_nodes:
            if node.name not in tracked:
                continue
            pr = gh.get_pr_for_branch(node.name)
            if pr:
                node.pr_number = pr.number
                node.pr_is_draft = pr.is_draft
            else:
                merged_num = gh.get_merged_pr_number(node.name)
                if merged_num:
                    node.pr_number = merged_num
                    node.pr_is_merged = True

    feature_node = next(n for n in branch_nodes if n.name == "feature")
    assert feature_node.pr_number == 789
    assert feature_node.pr_is_merged is True
    assert open_prs_route.call_count == 2


@respx.mock
def test_ls_no_pr_for_branch(repo_with_feature: Repo) -> None:
    """Test ls handles branches without PRs."""
    _adopt(repo_with_feature)

    # Set up origin remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API - no PRs
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )

    tree, tracked = _build_tree(repo_with_feature)

    from shortcake._github import GitHubClient

    branch_nodes = _collect_nodes(tree)
    with GitHubClient("fake-token", "owner", "repo") as gh:
        for node in branch_nodes:
            if node.name not in tracked:
                continue
            pr = gh.get_pr_for_branch(node.name)
            if pr:
                node.pr_number = pr.number

    # Feature node should have no PR info
    feature_node = next(n for n in branch_nodes if n.name == "feature")
    assert feature_node.pr_number is None


def test_ls_without_cache(repo_with_feature: Repo) -> None:
    """Test ls works without any cached PR info."""
    _adopt(repo_with_feature)

    result = _ls(repo_with_feature)

    # Should still render tree, just without PR info
    assert "feature" in result
    assert "main" in result
    assert "#" not in result  # No PR numbers


def test_ls_with_cached_pr_info(repo_with_feature: Repo) -> None:
    """Test ls shows PR info from cache."""
    from shortcake._cache import update_pr_cache

    _adopt(repo_with_feature)

    # Populate cache
    update_pr_cache(repo_with_feature, "feature", 123, is_draft=False)

    # Build tree and apply cache (simulating what ls() does)
    from shortcake._cache import load_pr_cache

    tree, tracked = _build_tree(repo_with_feature)
    pr_cache = load_pr_cache(repo_with_feature)
    branch_nodes = _collect_nodes(tree)

    for node in branch_nodes:
        if node.name in tracked and node.name in pr_cache:
            cached = pr_cache[node.name]
            node.pr_number = cached.number
            node.pr_is_draft = cached.is_draft
            node.pr_is_merged = cached.is_merged

    output = tree.render()
    assert "#123" in output
    assert "feature" in output


def test_ls_with_cached_draft_pr(repo_with_feature: Repo) -> None:
    """Test ls shows draft status from cache."""
    from shortcake._cache import update_pr_cache

    _adopt(repo_with_feature)

    # Populate cache with draft PR
    update_pr_cache(repo_with_feature, "feature", 456, is_draft=True)

    from shortcake._cache import load_pr_cache

    tree, tracked = _build_tree(repo_with_feature)
    pr_cache = load_pr_cache(repo_with_feature)
    branch_nodes = _collect_nodes(tree)

    for node in branch_nodes:
        if node.name in tracked and node.name in pr_cache:
            cached = pr_cache[node.name]
            node.pr_number = cached.number
            node.pr_is_draft = cached.is_draft

    output = tree.render()
    assert "#456 [dim]draft[/]" in output


def test_ls_with_cached_merged_pr(repo_with_feature: Repo) -> None:
    """Test ls shows merged status from cache."""
    from shortcake._cache import update_pr_cache

    _adopt(repo_with_feature)

    # Populate cache with merged PR
    update_pr_cache(repo_with_feature, "feature", 789, is_merged=True)

    from shortcake._cache import load_pr_cache

    tree, tracked = _build_tree(repo_with_feature)
    pr_cache = load_pr_cache(repo_with_feature)
    branch_nodes = _collect_nodes(tree)

    for node in branch_nodes:
        if node.name in tracked and node.name in pr_cache:
            cached = pr_cache[node.name]
            node.pr_number = cached.number
            node.pr_is_merged = cached.is_merged

    output = tree.render()
    assert "#789 [dim]merged[/]" in output


def test_ls_cli_with_cache(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test ls CLI shows PR info from cache."""
    import os

    from typer.testing import CliRunner

    from shortcake._cache import update_pr_cache
    from shortcake.cli import app

    _adopt(repo_with_feature)
    update_pr_cache(
        repo_with_feature,
        "feature",
        123,
        is_draft=False,
        url="https://github.com/owner/repo/pull/123",
    )

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0
    assert "#123" in result.output
    assert "feature" in result.output


def test_ls_cli_no_tracked_branches(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls CLI with no tracked branches."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 0
    assert "No tracked branches found" in result.output


@respx.mock
def test_ls_cli_refresh_no_token(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: Any
) -> None:
    """Test ls --refresh when no GitHub token is available."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Remove token environment variables
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Mock gh auth token to return nothing
    monkeypatch.setattr(
        "shortcake._github.subprocess.run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 1, "stdout": "", "stderr": ""}
        )(),
    )

    # Mock gh config file to not exist
    monkeypatch.setattr("shortcake._github.Path.exists", lambda self: False)

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls", "--refresh"])

    assert result.exit_code == 0
    assert "Cannot fetch PR info" in result.output


@respx.mock
def test_ls_cli_refresh_fetches_pr(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: Any
) -> None:
    """Test ls --refresh fetches PR info from GitHub."""
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    # Set token
    monkeypatch.setenv("GH_TOKEN", "fake-token")

    # Set up remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API with full PR response
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 456,
                    "head": {"ref": "feature"},
                    "base": {"ref": "main"},
                    "draft": True,
                    "html_url": "https://github.com/owner/repo/pull/456",
                    "title": "Test PR",
                    "body": "Test body",
                    "state": "open",
                }
            ],
        )
    )

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls", "--refresh"])

    assert result.exit_code == 0


@respx.mock
def test_fetch_pr_info_updates_cache(repo_with_feature: Repo, monkeypatch: Any) -> None:
    """Test _fetch_pr_info updates the PR cache."""
    from shortcake._cache import load_pr_cache
    from shortcake.commands.ls import _fetch_pr_info

    _adopt(repo_with_feature)

    # Set token
    monkeypatch.setenv("GH_TOKEN", "fake-token")

    # Set up remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API with full PR response
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 789,
                    "head": {"ref": "feature"},
                    "base": {"ref": "main"},
                    "draft": False,
                    "html_url": "https://github.com/owner/repo/pull/789",
                    "title": "Test PR",
                    "body": "Test body",
                    "state": "open",
                }
            ],
        )
    )

    tree, tracked = _build_tree(repo_with_feature)
    _fetch_pr_info(repo_with_feature, tree, tracked)

    # Check cache was updated
    cache = load_pr_cache(repo_with_feature)
    assert "feature" in cache
    assert cache["feature"].number == 789


@respx.mock
def test_fetch_pr_info_merged_pr(repo_with_feature: Repo, monkeypatch: Any) -> None:
    """Test _fetch_pr_info handles merged PRs."""
    from shortcake._cache import load_pr_cache
    from shortcake.commands.ls import _fetch_pr_info

    _adopt(repo_with_feature)

    # Set token
    monkeypatch.setenv("GH_TOKEN", "fake-token")

    # Set up remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API - returns no PRs on first call (open), merged PR on second call (closed)
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        side_effect=[
            httpx.Response(200, json=[]),  # First call: open PRs (empty)
            httpx.Response(  # Second call: closed PRs
                200,
                json=[
                    {
                        "number": 555,
                        "merged_at": "2024-01-15T10:30:00Z",
                    }
                ],
            ),
        ]
    )

    tree, tracked = _build_tree(repo_with_feature)
    _fetch_pr_info(repo_with_feature, tree, tracked)

    # Check cache was updated with merged PR
    cache = load_pr_cache(repo_with_feature)
    assert "feature" in cache
    assert cache["feature"].number == 555
    assert cache["feature"].is_merged is True


@respx.mock
def test_fetch_pr_info_api_error(repo_with_feature: Repo, monkeypatch: Any) -> None:
    """Test _fetch_pr_info handles API errors gracefully."""
    from shortcake.commands.ls import _fetch_pr_info

    _adopt(repo_with_feature)

    # Set token
    monkeypatch.setenv("GH_TOKEN", "fake-token")

    # Set up remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock API - error
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(500, json={"message": "Server error"})
    )

    tree, tracked = _build_tree(repo_with_feature)

    # Should not raise
    _fetch_pr_info(repo_with_feature, tree, tracked)


def test_fetch_pr_info_client_exception(
    repo_with_feature: Repo, monkeypatch: Any
) -> None:
    """Test _fetch_pr_info handles GitHubClient exceptions gracefully."""
    from shortcake.commands.ls import _fetch_pr_info

    _adopt(repo_with_feature)

    # Set token
    monkeypatch.setenv("GH_TOKEN", "fake-token")

    # Set up remote
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    # Mock GitHubClient to raise an exception when creating client
    class MockGitHubClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise Exception("Connection failed")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("shortcake.commands.ls.GitHubClient", MockGitHubClient)

    tree, tracked = _build_tree(repo_with_feature)

    # Should not raise - outer exception handler catches this
    _fetch_pr_info(repo_with_feature, tree, tracked)


# Tests for ls --json


def test_ls_cli_json_with_cache(repo_with_feature: Repo, tmp_path: Path) -> None:
    """Test ls --json emits the stack as a JSON envelope."""
    import json
    import os

    from typer.testing import CliRunner

    from shortcake._cache import update_pr_cache
    from shortcake.cli import app

    _adopt(repo_with_feature)
    update_pr_cache(
        repo_with_feature,
        "feature",
        123,
        is_draft=True,
        url="https://github.com/owner/repo/pull/123",
    )

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["current"] == "feature"
    branches = {branch["name"]: branch for branch in document["data"]["branches"]}
    assert branches["main"]["parent"] is None
    assert branches["feature"]["parent"] == "main"
    assert branches["feature"]["current"] is True
    assert branches["feature"]["pr"] == {
        "number": 123,
        "url": "https://github.com/owner/repo/pull/123",
        "draft": True,
        "merged": False,
        "closed": False,
    }
    assert "warnings" not in document


def test_ls_cli_json_no_tracked_branches(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls --json with no tracked branches emits an empty list, not an error."""
    import json
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["branches"] == []


@respx.mock
def test_ls_cli_json_refresh_fetches_pr(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: Any
) -> None:
    """Test ls --json --refresh fetches PR info without console rendering."""
    import json
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    set_remote(repo_with_feature, "origin", "git@github.com:owner/repo.git")

    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 456,
                    "head": {"ref": "feature"},
                    "base": {"ref": "main"},
                    "draft": False,
                    "html_url": "https://github.com/owner/repo/pull/456",
                    "title": "Test PR",
                    "body": "Test body",
                    "state": "open",
                }
            ],
        )
    )

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls", "--json", "--refresh"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    branches = {branch["name"]: branch for branch in document["data"]["branches"]}
    assert branches["feature"]["pr"]["number"] == 456
    assert "warnings" not in document


@respx.mock
def test_ls_cli_json_refresh_no_token_warns(
    repo_with_feature: Repo, tmp_path: Path, monkeypatch: Any
) -> None:
    """Test ls --json --refresh without a token reports a warning in the envelope."""
    import json
    import os

    from typer.testing import CliRunner

    from shortcake.cli import app

    _adopt(repo_with_feature)

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "shortcake._github.subprocess.run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 1, "stdout": "", "stderr": ""}
        )(),
    )
    monkeypatch.setattr("shortcake._github.Path.exists", lambda self: False)

    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["ls", "--json", "--refresh"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["warnings"] == [
        "Cannot fetch PR info: no GitHub token or not a GitHub repo"
    ]
    branches = {branch["name"]: branch for branch in document["data"]["branches"]}
    assert branches["feature"]["pr"] is None


# Trailer detection with branches parked at shared heads


def test_get_branch_parent_branch_at_trunk_head_untracked(
    repo_with_feature: Repo,
) -> None:
    """A branch parked exactly at trunk's head is never tracked."""
    _adopt(repo_with_feature)

    main_sha = get_ref(repo_with_feature, "refs/heads/main")
    set_ref(repo_with_feature, "refs/heads/fresh-branch", main_sha)

    all_branches = set(git.get_all_local_branches(repo_with_feature))
    assert (
        git.get_branch_parent(repo_with_feature, "fresh-branch", all_branches) is None
    )


def test_get_branch_parent_branch_parked_at_tracked_head_untracked(
    repo_with_feature: Repo,
) -> None:
    """A ref parked at a tracked branch's head is reported untracked.

    Two refs on one commit are genuinely ambiguous — the walk deliberately
    stops at the shared head so commands like fold refuse to mutate a branch
    they can't attribute history to.
    """
    _adopt(repo_with_feature)

    feature_sha = get_ref(repo_with_feature, "refs/heads/feature")
    set_ref(repo_with_feature, "refs/heads/backup-ref", feature_sha)

    all_branches = set(git.get_all_local_branches(repo_with_feature))
    assert git.get_branch_parent(repo_with_feature, "backup-ref", all_branches) is None


def test_get_branch_parent_prefers_unique_remote_backed_same_head_branch(
    repo_with_feature: Repo,
) -> None:
    """A fetched branch wins over a local alias parked at the same commit."""
    _adopt(repo_with_feature)

    feature_sha = get_ref(repo_with_feature, "refs/heads/feature")
    set_ref(repo_with_feature, "refs/heads/investigation", feature_sha)
    set_ref(repo_with_feature, "refs/remotes/origin/feature", feature_sha)

    all_branches = set(git.get_all_local_branches(repo_with_feature))
    assert git.get_branch_parent(repo_with_feature, "feature", all_branches) == "main"
    assert (
        git.get_branch_parent(repo_with_feature, "investigation", all_branches) is None
    )


# Staleness markers


def test_ls_marks_branch_needing_restack(temp_repo: Repo, tmp_path: Path) -> None:
    """A branch whose parent advanced is marked as needing restack."""
    # Tracked feature branch off main
    main_sha = get_ref(temp_repo, "refs/heads/main")
    set_ref(temp_repo, "refs/heads/feature", main_sha)
    temp_repo.set_head("refs/heads/feature")
    (tmp_path / "f.txt").write_text("feature")
    add_paths(temp_repo, tmp_path / "f.txt")
    commit(temp_repo, b"Add feature")
    _adopt(temp_repo)

    # Advance main so feature's base goes stale
    switch_branch(temp_repo, "main")
    (tmp_path / "m.txt").write_text("main moved")
    add_paths(temp_repo, tmp_path / "m.txt")
    commit(temp_repo, b"Advance main")
    switch_branch(temp_repo, "feature")

    result = _ls(temp_repo)
    assert "⟳ needs restack" in result

    tree, _ = _build_tree(temp_repo)
    feature = next(n for n in _collect_nodes(tree) if n.name == "feature")
    assert feature.needs_restack is True
    assert feature.to_data()["needs_restack"] is True


def test_ls_no_restack_marker_when_up_to_date(repo_with_feature: Repo) -> None:
    """An up-to-date stack shows no restack marker."""
    _adopt(repo_with_feature)

    result = _ls(repo_with_feature)

    assert "needs restack" not in result

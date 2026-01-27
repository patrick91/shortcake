from pathlib import Path

import httpx
import respx
from dulwich import porcelain
from dulwich.repo import Repo
from inline_snapshot import snapshot

from shortcake import _git as git
from shortcake.commands.adopt import _adopt
from shortcake.commands.ls import _build_tree, _collect_nodes, _ls


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
│
◯ main""")


def test_ls_current_highlighted(repo_with_feature: Repo) -> None:
    """Test that current branch is highlighted with ◉."""
    _adopt(repo_with_feature)

    # Check from feature branch (current)
    result = _ls(repo_with_feature)
    assert result == snapshot("""\
◉ feature (current)
│
◯ main""")

    # Switch to main and check
    repo_with_feature.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    result = _ls(repo_with_feature)
    assert result == snapshot("""\
◯ feature
│
◉ main (current)""")


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
    assert result == snapshot("""\
◉ feature (current)
│
◯ main""")


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

    assert result == snapshot("""\
◉ feature-b (current)
│
◯ feature-a
│
◯ main""")


def test_ls_parallel_stacks(temp_repo: Repo, tmp_path: Path) -> None:
    """Test ls with parallel stacks off main."""
    main_sha = temp_repo.refs[b"refs/heads/main"]

    # Create stack-1-a off main
    temp_repo.refs[b"refs/heads/stack-1-a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/stack-1-a")

    file_1a = tmp_path / "stack1a.txt"
    file_1a.write_text("stack1a")
    porcelain.add(temp_repo, paths=[str(file_1a)])
    porcelain.commit(temp_repo, message=b"Add stack-1-a")

    _adopt(temp_repo, branch="stack-1-a", parent="main")

    # Create stack-1-b off stack-1-a
    stack_1a_sha = temp_repo.refs[b"refs/heads/stack-1-a"]
    temp_repo.refs[b"refs/heads/stack-1-b"] = stack_1a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/stack-1-b")

    file_1b = tmp_path / "stack1b.txt"
    file_1b.write_text("stack1b")
    porcelain.add(temp_repo, paths=[str(file_1b)])
    porcelain.commit(temp_repo, message=b"Add stack-1-b")

    _adopt(temp_repo, branch="stack-1-b", parent="stack-1-a")

    # Create stack-2-a off main (parallel stack)
    temp_repo.refs[b"refs/heads/stack-2-a"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/stack-2-a")

    file_2a = tmp_path / "stack2a.txt"
    file_2a.write_text("stack2a")
    porcelain.add(temp_repo, paths=[str(file_2a)])
    porcelain.commit(temp_repo, message=b"Add stack-2-a")

    _adopt(temp_repo, branch="stack-2-a", parent="main")

    # Create stack-2-b off stack-2-a
    stack_2a_sha = temp_repo.refs[b"refs/heads/stack-2-a"]
    temp_repo.refs[b"refs/heads/stack-2-b"] = stack_2a_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/stack-2-b")

    file_2b = tmp_path / "stack2b.txt"
    file_2b.write_text("stack2b")
    porcelain.add(temp_repo, paths=[str(file_2b)])
    porcelain.commit(temp_repo, message=b"Add stack-2-b")

    _adopt(temp_repo, branch="stack-2-b", parent="stack-2-a")

    result = _ls(temp_repo)

    assert result == snapshot("""\
◯ stack-1-b
│
◯ stack-1-a
│
│ ◉ stack-2-b (current)
│ │
│ ◯ stack-2-a
│ │
◯─┘ main\
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
    head_sha = repo_with_feature.refs[b"refs/heads/feature"]
    head_file = tmp_path / ".git" / "HEAD"
    head_file.write_text(head_sha.decode() + "\n")

    result = _ls(repo_with_feature)

    # Should still show the tree, just without current marker
    assert result == snapshot("""\
◯ feature
│
◯ main""")


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
    porcelain.add(temp_repo, paths=[str(file1)])
    porcelain.commit(temp_repo, message=b"Second commit on main")

    # Create develop branch from main
    main_sha = temp_repo.refs[b"refs/heads/main"]
    temp_repo.refs[b"refs/heads/develop"] = main_sha
    temp_repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/develop")

    # Add commit to develop (now develop is ahead of main)
    file2 = tmp_path / "file2.txt"
    file2.write_text("content2")
    porcelain.add(temp_repo, paths=[str(file2)])
    porcelain.commit(temp_repo, message=b"Commit on develop")

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

    from dulwich.objects import Commit

    # C1
    file1 = tmp_path / "file1.txt"
    file1.write_text("content1")
    porcelain.add(temp_repo, paths=[str(file1)])
    porcelain.commit(temp_repo, message=b"C1")
    c1_sha = temp_repo.refs[b"refs/heads/main"]

    # Create C2 directly (not on a branch) with parent C1
    c1_commit = temp_repo[c1_sha]
    c2 = Commit()
    c2.tree = c1_commit.tree
    c2.parents = [c1_sha]
    c2.author = c1_commit.author
    c2.committer = c1_commit.committer
    c2.author_time = c1_commit.author_time
    c2.author_timezone = c1_commit.author_timezone
    c2.commit_time = c1_commit.commit_time + 1
    c2.commit_timezone = c1_commit.commit_timezone
    c2.message = b"C2"
    temp_repo.object_store.add_object(c2)
    c2_sha = c2.id

    # Create C3 directly with parent C1
    c3 = Commit()
    c3.tree = c1_commit.tree
    c3.parents = [c1_sha]
    c3.author = c1_commit.author
    c3.committer = c1_commit.committer
    c3.author_time = c1_commit.author_time
    c3.author_timezone = c1_commit.author_timezone
    c3.commit_time = c1_commit.commit_time + 2
    c3.commit_timezone = c1_commit.commit_timezone
    c3.message = b"C3"
    temp_repo.object_store.add_object(c3)
    c3_sha = c3.id

    # Create merge commit M with parents C2 and C3 (both have parent C1)
    merge = Commit()
    merge.tree = c1_commit.tree
    merge.parents = [c2_sha, c3_sha]  # Two parents that share C1!
    merge.author = c1_commit.author
    merge.committer = c1_commit.committer
    merge.author_time = c1_commit.author_time
    merge.author_timezone = c1_commit.author_timezone
    merge.commit_time = c1_commit.commit_time + 3
    merge.commit_timezone = c1_commit.commit_timezone
    merge.message = b"Merge C2 and C3"
    temp_repo.object_store.add_object(merge)
    temp_repo.refs[b"refs/heads/main"] = merge.id

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
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

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
def test_ls_fetches_draft_pr_info(repo_with_feature: Repo) -> None:
    """Test ls fetches draft PR info from GitHub API."""
    _adopt(repo_with_feature)

    # Set up origin remote
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

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
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

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
    config = repo_with_feature.get_config()
    config.set((b"remote", b"origin"), b"url", b"git@github.com:owner/repo.git")
    config.write_to_path()

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
    assert "#456 draft" in output


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
    assert "#789 merged" in output

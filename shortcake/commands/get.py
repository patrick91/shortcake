"""Get command for fetching and adopting branches from remote."""

import typer
from rich_toolkit.menu import Menu, Option
from rich_toolkit.styles import TaggedStyle

from shortcake.git import GitError, GitRepo
from shortcake.github import GitHubClient, GitHubError, PullRequest, get_github_repo_info
from shortcake.metadata import get_branch_metadata, update_branch_metadata
from shortcake.output import print_error, print_warning

app = typer.Typer()


def _pick_pr_interactive(prs: list[PullRequest]) -> PullRequest | None:
    """Show an interactive menu to pick a PR.

    Args:
        prs: List of PRs to choose from

    Returns:
        The selected PR, or None if cancelled
    """
    if not prs:
        return None

    options = [
        Option({"value": pr, "name": f"#{pr.number} {pr.title} ({pr.head_ref})"}) for pr in prs
    ]

    result = Menu(
        label="Select a pull request to fetch:",
        options=options,
        allow_filtering=True,
        max_visible=15,
        style=TaggedStyle(),
    ).ask()

    return result


def _resolve_pr_to_branch(git: GitRepo, pr_number: int) -> str:
    """Resolve a PR number to a branch name using GitHub API.

    Args:
        git: GitRepo instance
        pr_number: The PR number to resolve

    Returns:
        The branch name for the PR

    Raises:
        GitHubError: If unable to resolve PR
    """
    owner, repo = get_github_repo_info(git)
    with GitHubClient() as client:
        pr = client.get_pull_request(owner, repo, pr_number)
        return pr.head_ref


def _get_remote_branches(git: GitRepo) -> set[str]:
    """Get all remote branch names from origin.

    Args:
        git: GitRepo instance

    Returns:
        Set of remote branch names (without origin/ prefix)
    """
    try:
        # Use git branch -r to list remote branches
        result = git.repo.git.branch("-r", "--format=%(refname:short)")
        branches = set()
        for line in result.strip().split("\n"):
            line = line.strip()
            if line.startswith("origin/") and not line.endswith("/HEAD"):
                branches.add(line.removeprefix("origin/"))
        return branches
    except Exception:
        return set()


def _find_stack_branches(
    git: GitRepo,
    target_branch: str,
    remote_branches: set[str],
    main_branch: str,
) -> list[str]:
    """Find all branches in a stack from main to target branch.

    Walks the commit history from target branch back to main,
    finding all remote branches that are ancestors of the target.

    Args:
        git: GitRepo instance
        target_branch: The target branch to find stack for
        remote_branches: Set of available remote branch names
        main_branch: The main/trunk branch name

    Returns:
        List of branch names in order from closest to main to target
    """
    # Get all remote branches that are ancestors of target
    target_ref = f"origin/{target_branch}"

    # Find branches that are on the path from main to target
    stack_candidates: list[tuple[str, int]] = []

    for branch in remote_branches:
        if branch == main_branch or branch == target_branch:
            continue

        remote_ref = f"origin/{branch}"

        try:
            # Check if this branch is an ancestor of target
            if not git.is_ancestor(remote_ref, target_ref):
                continue

            # Check if main is an ancestor of this branch (it should be between main and target)
            main_ref = f"origin/{main_branch}"
            if not git.is_ancestor(main_ref, remote_ref):
                continue

            # Calculate distance from main
            distance = git.count_commits_between(main_ref, remote_ref)
            if distance > 0:
                stack_candidates.append((branch, distance))
        except GitError:
            continue

    # Sort by distance from main (closest first)
    stack_candidates.sort(key=lambda x: x[1])

    # Build the stack in order
    stack = [branch for branch, _ in stack_candidates]
    stack.append(target_branch)

    return stack


def _create_or_update_local_branch(git: GitRepo, branch: str) -> bool:
    """Create or update a local branch from its remote counterpart.

    Args:
        git: GitRepo instance
        branch: Branch name

    Returns:
        True if branch was created/updated, False if already up to date
    """
    remote_ref = f"origin/{branch}"
    remote_sha = git.get_commit_sha(remote_ref)

    if git.branch_exists(branch):
        local_sha = git.get_commit_sha(branch)
        if local_sha == remote_sha:
            return False
        # Update local branch to match remote
        git.update_ref(f"refs/heads/{branch}", remote_sha)
        return True
    else:
        # Create local branch pointing to remote commit
        git.update_ref(f"refs/heads/{branch}", remote_sha)
        return True


@app.command()
def get(
    target: str | None = typer.Argument(
        None, help="Branch name or PR number to fetch (interactive if omitted)"
    ),
    mine: bool = typer.Option(
        False, "--mine", "-m", help="Only show PRs authored by you (interactive mode)"
    ),
    downstack_only: bool = typer.Option(
        False, "--downstack", "-d", help="Only fetch downstack branches (don't sync upstack)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite local branches with remote versions"
    ),
):
    """Fetch a branch and its stack from remote and adopt them.

    Syncs branches from trunk to the given branch from remote,
    setting up shortcake tracking for the entire stack.

    After fetching, run 'restack' to rebase onto the latest main if needed.

    Examples:
        shortcake get                 # Interactive: select from open PRs
        shortcake get --mine          # Interactive: select from your PRs
        shortcake get feature-1       # Fetch by branch name
        shortcake get 123             # Fetch by PR number
    """
    try:
        git = GitRepo()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    # Check for remote
    if not git.has_remote("origin"):
        print_error("No remote 'origin' configured")
        raise typer.Exit(1)

    # Interactive mode: no target provided
    if target is None:
        try:
            owner, repo = get_github_repo_info(git)
            typer.echo("Fetching open pull requests...")

            with GitHubClient() as client:
                prs = client.list_pull_requests(owner, repo, state="open")

                if mine:
                    current_user = client.get_current_user()
                    prs = [pr for pr in prs if pr.author == current_user]

            if not prs:
                if mine:
                    typer.echo("No open PRs found authored by you")
                else:
                    typer.echo("No open PRs found")
                raise typer.Exit(0)

            selected_pr = _pick_pr_interactive(prs)
            if selected_pr is None:
                typer.echo("Cancelled")
                raise typer.Exit(0)

            target_branch = selected_pr.head_ref
            typer.echo(f"Selected: #{selected_pr.number} → {target_branch}")

        except GitHubError as e:
            print_error(f"Failed to fetch PRs: {e}")
            raise typer.Exit(1) from None

    # Resolve target to branch name
    elif target.isdigit():
        pr_number = int(target)
        typer.echo(f"Resolving PR #{pr_number}...")
        try:
            target_branch = _resolve_pr_to_branch(git, pr_number)
            typer.echo(f"  → Branch: {target_branch}")
        except GitHubError as e:
            print_error(f"Failed to resolve PR #{pr_number}: {e}")
            raise typer.Exit(1) from None
    else:
        target_branch = target

    # Fetch from remote
    typer.echo("Fetching from origin...")
    try:
        git.fetch("origin")
    except GitError as e:
        print_error(f"Failed to fetch: {e}")
        raise typer.Exit(1) from None

    # Get main branch
    try:
        main_branch = git.get_main_branch()
    except GitError as e:
        print_error(str(e))
        raise typer.Exit(1) from None

    # Update local main to origin/main if behind
    remote_main = f"origin/{main_branch}"
    try:
        local_main_sha = git.get_commit_sha(main_branch)
        remote_main_sha = git.get_commit_sha(remote_main)

        if local_main_sha != remote_main_sha:
            if git.is_ancestor(local_main_sha, remote_main_sha):
                # Local is behind remote, fast-forward
                current = git.get_current_branch()
                if current == main_branch:
                    git.merge_ff_only(remote_main)
                else:
                    git.update_ref(f"refs/heads/{main_branch}", remote_main_sha)
                typer.echo(f"Updated {main_branch} to latest")
    except GitError:
        pass  # If we can't update main, continue anyway

    # Check if target branch exists on remote
    remote_branches = _get_remote_branches(git)
    if target_branch not in remote_branches:
        print_error(f"Branch '{target_branch}' not found on remote")
        raise typer.Exit(1)

    # Find stack branches
    typer.echo("Analyzing branch stack...")
    stack = _find_stack_branches(git, target_branch, remote_branches, main_branch)

    if not stack:
        print_error("Could not determine branch stack")
        raise typer.Exit(1)

    typer.echo(f"Found {len(stack)} branch(es) in stack:")
    for branch in stack:
        typer.echo(f"  • {branch}")
    typer.echo()

    # Check for local branches that would be overwritten
    if not force:
        conflicts = []
        for branch in stack:
            if git.branch_exists(branch):
                local_sha = git.get_commit_sha(branch)
                remote_sha = git.get_commit_sha(f"origin/{branch}")
                if local_sha != remote_sha:
                    # Check if local is ahead of remote
                    if not git.is_ancestor(local_sha, remote_sha):
                        conflicts.append(branch)

        if conflicts:
            print_warning("The following local branches would be overwritten:")
            for branch in conflicts:
                typer.echo(f"  • {branch}")
            typer.echo()
            typer.echo("Use --force to overwrite, or resolve manually.")
            raise typer.Exit(1)

    # Create/update local branches and adopt them
    typer.echo("Setting up branches...")
    current_branch = git.get_current_branch()

    for i, branch in enumerate(stack):
        # Determine parent
        if i == 0:
            parent = main_branch
        else:
            parent = stack[i - 1]

        # Check if already tracked with correct parent
        existing_metadata = get_branch_metadata(branch)
        if existing_metadata.get("parent") == parent and not force:
            local_sha = git.get_commit_sha(branch) if git.branch_exists(branch) else None
            remote_sha = git.get_commit_sha(f"origin/{branch}")
            if local_sha == remote_sha:
                typer.echo(f"  ✓ {branch} (already up to date)")
                continue

        # Create or update local branch
        _create_or_update_local_branch(git, branch)

        # Get parent revision for metadata
        # Use merge-base between origin/branch (not local) and parent_ref
        # This ensures we get the correct divergence point from freshly fetched data
        if parent == main_branch:
            parent_ref = f"origin/{main_branch}" if git.has_remote("origin") else main_branch
        else:
            parent_ref = f"origin/{parent}" if git.has_remote("origin") else parent

        try:
            # Use origin/branch to ensure we're using freshly fetched data
            parent_revision = git.get_merge_base(f"origin/{branch}", parent_ref)
        except GitError:
            parent_revision = None

        # Update metadata
        update_branch_metadata(
            branch,
            parent=parent,
            parent_revision=parent_revision,
        )

        typer.echo(f"  ✓ {branch} (parent: {parent})")

    typer.echo()
    typer.echo(f"Successfully fetched {len(stack)} branch(es)")

    # Checkout the target branch
    if current_branch != target_branch:
        try:
            git.checkout_branch(target_branch)
            typer.echo(f"Switched to {target_branch}")
        except GitError as e:
            print_warning(f"Could not checkout {target_branch}: {e}")

    # Check if restack is needed and hint to user
    bottom_branch = stack[0]
    bottom_metadata = get_branch_metadata(bottom_branch)
    parent_revision = bottom_metadata.get("parent_revision")

    if parent_revision:
        try:
            current_main_sha = git.get_commit_sha(remote_main)
            if parent_revision != current_main_sha:
                typer.echo()
                typer.echo(f"Hint: Branch is based on old {main_branch}. Run 'restack' to update.")
        except GitError:
            pass

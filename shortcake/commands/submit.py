"""Submit command for pushing branches and creating/updating PRs."""

import json
from dataclasses import dataclass

import typer

from shortcake.git import GitError, GitRepo
from shortcake.github import GitHubClient, GitHubError, get_github_repo_info

app = typer.Typer()


@dataclass
class BranchSubmitInfo:
    """Information about a branch to submit."""

    name: str
    parent: str
    commit_message: str
    pr_number: int | None = None
    pr_url: str | None = None


def _get_branch_metadata(git: GitRepo, branch: str) -> dict:
    """Get shortcake metadata for a branch from git notes."""
    notes = git.get_notes(branch, "shortcake")
    if notes:
        try:
            return json.loads(notes)
        except json.JSONDecodeError:
            return {}
    return {}


def _update_branch_metadata(git: GitRepo, branch: str, metadata: dict) -> None:
    """Update shortcake metadata for a branch in git notes."""
    git.update_notes(json.dumps(metadata), branch, "shortcake")


def _get_stack_branches(git: GitRepo, start_branch: str) -> list[BranchSubmitInfo]:
    """Get all branches in the stack from bottom to top.

    Walks up from start_branch to main, then returns branches in order
    from closest to main to start_branch.

    Args:
        git: GitRepo instance
        start_branch: The branch to start from (current branch)

    Returns:
        List of BranchSubmitInfo from bottom of stack to top
    """
    branches = []
    current = start_branch

    while current:
        metadata = _get_branch_metadata(git, current)
        parent = metadata.get("parent")

        if not parent:
            break  # Not a shortcake-managed branch

        # Get the first line of the commit message for PR title
        commit_msg = git.get_commit_message(current)
        first_line = commit_msg.split("\n")[0] if commit_msg else current

        branches.append(
            BranchSubmitInfo(
                name=current,
                parent=parent,
                commit_message=first_line,
                pr_number=metadata.get("pr_number"),
                pr_url=metadata.get("pr_url"),
            )
        )

        # Check if parent is a shortcake branch or if we've reached main/master
        parent_metadata = _get_branch_metadata(git, parent)
        if not parent_metadata.get("parent"):
            break  # Parent is not managed by shortcake (likely main)

        current = parent

    # Reverse so branches are ordered from bottom of stack to top
    branches.reverse()
    return branches


def _get_children(git: GitRepo, branch: str) -> list[str]:
    """Get all branches that have the given branch as their parent."""
    children = []
    for branch_name in git.get_branches():
        metadata = _get_branch_metadata(git, branch_name)
        if metadata.get("parent") == branch:
            children.append(branch_name)
    return children


def _get_descendant_branches(git: GitRepo, branch: str) -> list[BranchSubmitInfo]:
    """Get all descendant branches (children, grandchildren, etc.) in order.

    Args:
        git: GitRepo instance
        branch: The branch to find descendants of

    Returns:
        List of BranchSubmitInfo for all descendants, in topological order
    """
    result = []
    queue = _get_children(git, branch)

    while queue:
        child = queue.pop(0)
        metadata = _get_branch_metadata(git, child)
        if metadata.get("parent"):
            commit_msg = git.get_commit_message(child)
            first_line = commit_msg.split("\n")[0] if commit_msg else child
            result.append(
                BranchSubmitInfo(
                    name=child,
                    parent=metadata["parent"],
                    commit_message=first_line,
                    pr_number=metadata.get("pr_number"),
                    pr_url=metadata.get("pr_url"),
                )
            )
            # Add this child's children to the queue
            queue.extend(_get_children(git, child))

    return result


def _get_main_branch(git: GitRepo) -> str:
    """Get the name of the main branch."""
    if git.branch_exists("main"):
        return "main"
    if git.branch_exists("master"):
        return "master"
    raise GitError("Neither 'main' nor 'master' branch exists")


@app.command()
def submit(
    draft: bool = typer.Option(False, "--draft", "-d", help="Create PR as draft"),
    current: bool = typer.Option(False, "--current", "-c", help="Only submit the current branch"),
    stack: bool = typer.Option(
        False, "--stack", "-s", help="Submit all branches in the stack (including children)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be done without making changes"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Force push branches"),
):
    """Push branches and create or update pull requests.

    By default, submits all branches from trunk up to the current branch
    (parents + current). This ensures parent branches are pushed, so GitHub
    shows correct diffs for stacked PRs.

    Use --stack to also submit child branches (the entire stack).
    Use --current to only submit the current branch.

    Examples:
        shortcake submit              # Submit parents + current (default)
        shortcake submit --stack      # Submit entire stack (including children)
        shortcake submit --current    # Submit only current branch
        shortcake submit --draft      # Create PRs as drafts
        shortcake submit --dry-run    # Preview what would happen
    """
    try:
        git = GitRepo()
    except GitError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    current_branch = git.get_current_branch()
    main_branch = _get_main_branch(git)

    # Check if on main branch
    if current_branch in ("main", "master"):
        typer.echo("Error: Cannot submit from main/master branch", err=True)
        raise typer.Exit(1)

    # Get branch metadata
    metadata = _get_branch_metadata(git, current_branch)
    if not metadata.get("parent"):
        typer.echo(
            f"Error: Branch '{current_branch}' is not managed by shortcake. "
            "Use 'shortcake adopt' first.",
            err=True,
        )
        raise typer.Exit(1)

    # Check for remote
    if not git.has_remote("origin"):
        typer.echo("Error: No 'origin' remote configured", err=True)
        raise typer.Exit(1)

    # Get GitHub repo info
    try:
        owner, repo = get_github_repo_info(git)
    except GitHubError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if dry_run:
        typer.echo(f"Repository: {owner}/{repo}")
        typer.echo()

    # Get branches to submit
    if current:
        # Only submit the current branch
        commit_msg = git.get_commit_message(current_branch)
        first_line = commit_msg.split("\n")[0] if commit_msg else current_branch
        branches = [
            BranchSubmitInfo(
                name=current_branch,
                parent=metadata.get("parent", main_branch),
                commit_message=first_line,
                pr_number=metadata.get("pr_number"),
                pr_url=metadata.get("pr_url"),
            )
        ]
    elif stack:
        # Submit entire stack (parents + current + children)
        branches = _get_stack_branches(git, current_branch)
        branches.extend(_get_descendant_branches(git, current_branch))
    else:
        # Default: submit parents + current (downstack)
        branches = _get_stack_branches(git, current_branch)

    if not branches:
        typer.echo("No branches to submit")
        return

    if dry_run:
        typer.echo("Would submit the following branches:")
        for branch in branches:
            action = "update" if branch.pr_number else "create"
            typer.echo(f"  • {branch.name} → {branch.parent} ({action} PR)")
            if branch.pr_number:
                typer.echo(f"    Existing PR: #{branch.pr_number}")
        return

    # Initialize GitHub client
    try:
        github = GitHubClient()
    except GitHubError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    try:
        submitted_prs: list[tuple[str, str, int]] = []  # (branch, url, pr_number)

        for branch in branches:
            typer.echo(f"Submitting {branch.name}...", nl=False)

            # Check if branch needs pushing (compare local and remote SHAs)
            needs_push = True
            try:
                local_sha = git.get_commit_sha(branch.name)
                remote_sha = git.get_commit_sha(f"origin/{branch.name}")
                needs_push = local_sha != remote_sha
            except GitError:
                # Remote branch doesn't exist yet, needs push
                needs_push = True

            if needs_push:
                try:
                    git.push("origin", branch.name, force_with_lease=True)
                except GitError as e:
                    typer.echo(" FAILED")
                    typer.echo(f"Error pushing branch: {e}", err=True)
                    raise typer.Exit(1) from None

            # Determine base branch for PR
            # If parent is a shortcake branch, use it as base
            # Otherwise, use main
            parent_metadata = _get_branch_metadata(git, branch.parent)
            if parent_metadata.get("parent"):
                # Parent is also a shortcake branch, use it as base
                base_branch = branch.parent
            else:
                # Parent is main or not managed
                base_branch = branch.parent

            # Create or update PR
            try:
                if branch.pr_number:
                    # Check if PR exists and update base if needed
                    existing_pr = github.get_pull_request(owner, repo, branch.pr_number)
                    if existing_pr.base_ref != base_branch:
                        # Update base branch
                        pr = github.update_pull_request(
                            owner, repo, branch.pr_number, base=base_branch
                        )
                        typer.echo(f" updated base → {base_branch}")
                        submitted_prs.append((branch.name, pr.html_url, pr.number))
                    elif needs_push:
                        typer.echo(f" pushed (PR #{branch.pr_number})")
                        pr = existing_pr
                        submitted_prs.append((branch.name, pr.html_url, pr.number))
                    else:
                        typer.echo(f" up to date (PR #{branch.pr_number})")
                        pr = existing_pr
                else:
                    # Check if PR already exists for this branch
                    existing_prs = github.get_pull_requests_for_branch(owner, repo, branch.name)
                    if existing_prs:
                        # PR exists, update it
                        pr = existing_prs[0]
                        if pr.base_ref != base_branch:
                            pr = github.update_pull_request(
                                owner, repo, pr.number, base=base_branch
                            )
                            typer.echo(f" updated base → {base_branch} (PR #{pr.number})")
                        elif needs_push:
                            typer.echo(f" pushed (PR #{pr.number})")
                        else:
                            typer.echo(f" up to date (PR #{pr.number})")
                        submitted_prs.append((branch.name, pr.html_url, pr.number))
                    else:
                        # Create new PR
                        pr = github.create_pull_request(
                            owner=owner,
                            repo=repo,
                            title=branch.commit_message,
                            head=branch.name,
                            base=base_branch,
                            body="",
                            draft=draft,
                        )
                        typer.echo(f" created PR #{pr.number}")
                        submitted_prs.append((branch.name, pr.html_url, pr.number))

                # Update metadata with PR info
                branch_metadata = _get_branch_metadata(git, branch.name)
                branch_metadata["pr_number"] = pr.number
                branch_metadata["pr_url"] = pr.html_url
                _update_branch_metadata(git, branch.name, branch_metadata)

            except GitHubError as e:
                typer.echo(" FAILED")
                typer.echo(f"Error with GitHub API: {e}", err=True)
                raise typer.Exit(1) from None

        # Summary
        typer.echo()
        typer.echo("Submitted PRs:")
        for branch_name, url, _pr_number in submitted_prs:
            typer.echo(f"  • {branch_name}: {url}")

    finally:
        github.close()

"""Submit command for pushing branches and creating/updating PRs."""

from dataclasses import dataclass

import typer

from shortcake import get_cli_name
from shortcake.commands.restack import _get_remote_ref, _needs_restack
from shortcake.git import GitError, GitRepo
from shortcake.github import GitHubClient, GitHubError, get_github_repo_info
from shortcake.metadata import (
    get_all_branch_metadata,
    get_branch_metadata,
    update_branch_metadata,
)

app = typer.Typer()


@dataclass
class BranchSubmitInfo:
    """Information about a branch to submit."""

    name: str
    parent: str
    commit_message: str
    pr_number: int | None = None
    pr_url: str | None = None


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
    all_metadata = get_all_branch_metadata()
    branches = []
    current = start_branch

    while current:
        metadata = all_metadata.get(current, {})
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
        parent_metadata = all_metadata.get(parent, {})
        if not parent_metadata.get("parent"):
            break  # Parent is not managed by shortcake (likely main)

        current = parent

    # Reverse so branches are ordered from bottom of stack to top
    branches.reverse()
    return branches


def _get_children(branch: str) -> list[str]:
    """Get all branches that have the given branch as their parent."""
    children = []
    for name, meta in get_all_branch_metadata().items():
        if meta.get("parent") == branch:
            children.append(name)
    return children


def _get_descendant_branches(git: GitRepo, branch: str) -> list[BranchSubmitInfo]:
    """Get all descendant branches (children, grandchildren, etc.) in order.

    Args:
        git: GitRepo instance
        branch: The branch to find descendants of

    Returns:
        List of BranchSubmitInfo for all descendants, in topological order
    """
    all_metadata = get_all_branch_metadata()
    result = []
    queue = _get_children(branch)

    while queue:
        child = queue.pop(0)
        metadata = all_metadata.get(child, {})
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
            queue.extend(_get_children(child))

    return result


def _get_main_branch(git: GitRepo) -> str:
    """Get the name of the main branch."""
    if git.branch_exists("main"):
        return "main"
    if git.branch_exists("master"):
        return "master"
    raise GitError("Neither 'main' nor 'master' branch exists")


# Markers for the stack section in PR body
STACK_START_MARKER = "<!-- shortcake stack start -->"
STACK_END_MARKER = "<!-- shortcake stack end -->"


def _generate_stack_description(
    branches: list[BranchSubmitInfo],
    current_branch: str,
    main_branch: str,
    pr_states: dict[int, str] | None = None,
) -> str:
    """Generate a markdown description of the stack for PR body.

    Args:
        branches: List of branches in the stack (bottom to top)
        current_branch: The branch this description is for
        main_branch: The name of the main/trunk branch
        pr_states: Optional dict mapping PR number to state ('open', 'closed', 'merged')

    Returns:
        Markdown string describing the stack
    """
    pr_states = pr_states or {}
    lines = ["## Stack"]

    # Show branches from top to bottom (reverse order)
    for branch in reversed(branches):
        pr_link = f"#{branch.pr_number}" if branch.pr_number else branch.name

        # Check if PR is merged/closed
        state = pr_states.get(branch.pr_number) if branch.pr_number else None
        is_merged = state in ("closed", "merged")

        if branch.name == current_branch:
            lines.append(f"- **{pr_link}** ⬅")
        elif is_merged:
            lines.append(f"- ~~{pr_link}~~ ✅")
        else:
            lines.append(f"- {pr_link}")

    # Add main branch at the bottom
    lines.append(f"- {main_branch}")

    return "\n".join(lines)


def _update_pr_body_with_stack(
    existing_body: str,
    stack_description: str,
) -> str:
    """Update PR body with stack description.

    If the body already has a stack section, replace it.
    Otherwise, prepend the stack section.

    Args:
        existing_body: Current PR body
        stack_description: New stack description

    Returns:
        Updated PR body
    """
    stack_section = f"{STACK_START_MARKER}\n{stack_description}\n{STACK_END_MARKER}"

    # Check if body already has a stack section
    if STACK_START_MARKER in existing_body and STACK_END_MARKER in existing_body:
        # Replace existing stack section
        import re

        pattern = f"{re.escape(STACK_START_MARKER)}.*?{re.escape(STACK_END_MARKER)}"
        return re.sub(pattern, stack_section, existing_body, flags=re.DOTALL)
    else:
        # Prepend stack section
        if existing_body.strip():
            return f"{stack_section}\n\n{existing_body}"
        else:
            return stack_section


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
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force push branches (override remote changes) and update PR descriptions",
    ),
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

    cli = get_cli_name()
    current_branch = git.get_current_branch()
    main_branch = _get_main_branch(git)

    # Check if on main branch
    if current_branch in ("main", "master"):
        typer.echo("Error: Cannot submit from main/master branch", err=True)
        raise typer.Exit(1)

    # Get branch metadata
    metadata = get_branch_metadata(current_branch)
    if not metadata.get("parent"):
        typer.echo(
            f"Error: Branch '{current_branch}' is not managed by shortcake. "
            f"Use '{cli} adopt' first.",
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

    # Restack branches that need it before pushing
    restacked_branches: list[str] = []
    for branch in branches:
        branch_metadata = get_branch_metadata(branch.name)
        parent = branch_metadata.get("parent")
        if not parent:
            continue

        # Get the rebase target (use remote ref for trunk branches if it exists)
        rebase_target = _get_remote_ref(git, parent)
        # Fall back to local branch if remote ref doesn't exist
        try:
            git.get_commit_sha(rebase_target)
        except GitError:
            rebase_target = parent

        if _needs_restack(git, branch.name, rebase_target, branch_metadata):
            typer.echo(f"Restacking {branch.name} onto {rebase_target}...", nl=False)
            try:
                stored_parent_rev = branch_metadata.get("parent_revision")
                if stored_parent_rev:
                    git.rebase_onto(rebase_target, stored_parent_rev, branch.name)
                else:
                    # Fallback for legacy branches
                    merge_base = git.get_merge_base(branch.name, rebase_target)
                    if merge_base:
                        git.rebase_onto(rebase_target, merge_base, branch.name)
                    else:
                        git.checkout_branch(branch.name)
                        git.rebase(rebase_target)

                # Update metadata with new parent_revision
                update_branch_metadata(
                    branch.name, parent_revision=git.get_commit_sha(rebase_target)
                )

                typer.echo(" done")
                restacked_branches.append(branch.name)
            except GitError:
                typer.echo(" CONFLICT")

                typer.echo(f"\nError: Rebase conflict while rebasing {branch.name}.", err=True)
                typer.echo("\nRebase conflict occurred. Please resolve manually:")
                typer.echo("  1. Fix the conflicts in the affected files")
                typer.echo("  2. Stage the resolved files: git add <files>")
                typer.echo(f"  3. Continue: {cli} restack --continue")
                typer.echo(f"  4. Then run: {cli} submit")
                raise typer.Exit(1) from None

    # Return to original branch if we restacked anything
    if restacked_branches:
        try:
            git.checkout_branch(current_branch)
        except GitError:
            pass

    # Refresh branch info after restacking (commit messages may have changed)
    if restacked_branches:
        if current:
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
            branches = _get_stack_branches(git, current_branch)
            branches.extend(_get_descendant_branches(git, current_branch))
        else:
            branches = _get_stack_branches(git, current_branch)

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

            if needs_push or force:
                try:
                    git.push(
                        "origin",
                        branch.name,
                        force=force,
                        force_with_lease=not force,
                    )
                except GitError as e:
                    typer.echo(" FAILED")
                    typer.echo(f"Error pushing branch: {e}", err=True)
                    raise typer.Exit(1) from None

            # Determine base branch for PR
            # If parent is a shortcake branch, use it as base
            # Otherwise, use main
            parent_metadata = get_branch_metadata(branch.parent)
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
                    elif needs_push or force:
                        typer.echo(f" pushed (PR #{branch.pr_number})")
                        pr = existing_pr
                        submitted_prs.append((branch.name, pr.html_url, pr.number))
                    else:
                        typer.echo(f" up to date (PR #{branch.pr_number})")
                        pr = existing_pr
                        submitted_prs.append((branch.name, pr.html_url, pr.number))
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
                            submitted_prs.append((branch.name, pr.html_url, pr.number))
                        elif needs_push or force:
                            typer.echo(f" pushed (PR #{pr.number})")
                            submitted_prs.append((branch.name, pr.html_url, pr.number))
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
                update_branch_metadata(branch.name, pr_number=pr.number, pr_url=pr.html_url)

                # Update branch object with PR number for stack description
                branch.pr_number = pr.number
                branch.pr_url = pr.html_url

            except GitHubError as e:
                typer.echo(" FAILED")
                typer.echo(f"Error with GitHub API: {e}", err=True)
                raise typer.Exit(1) from None

        # Update PR bodies with stack info
        # Get the FULL stack (parents + current + all descendants) for PR descriptions
        full_stack = _get_stack_branches(git, current_branch)
        full_stack.extend(_get_descendant_branches(git, current_branch))

        # Only update if we have multiple branches in the full stack or force is set
        if len(full_stack) > 1 or force:
            typer.echo()
            typer.echo("Updating PR descriptions with stack info...")

            # Collect PR states for all branches in the stack
            pr_states: dict[int, str] = {}
            for branch in full_stack:
                if branch.pr_number:
                    try:
                        pr_info = github.get_pull_request(owner, repo, branch.pr_number)
                        pr_states[branch.pr_number] = pr_info.state
                    except GitHubError:
                        pass

            # Update ALL PRs in the full stack, not just the ones submitted
            for branch in full_stack:
                if branch.pr_number:
                    try:
                        # Get current PR to preserve existing body
                        current_pr = github.get_pull_request(owner, repo, branch.pr_number)

                        # Skip updating closed/merged PRs
                        if current_pr.state != "open":
                            continue

                        # Generate stack description using FULL stack
                        stack_desc = _generate_stack_description(
                            full_stack, branch.name, main_branch, pr_states
                        )

                        # Update PR body
                        new_body = _update_pr_body_with_stack(current_pr.body, stack_desc)
                        if new_body != current_pr.body or force:
                            github.update_pull_request(owner, repo, branch.pr_number, body=new_body)
                    except GitHubError:
                        pass  # Ignore errors updating PR body

        # Summary
        typer.echo()
        typer.echo("Submitted PRs:")
        for branch_name, url, _pr_number in submitted_prs:
            typer.echo(f"  • {branch_name}: {url}")

    finally:
        github.close()

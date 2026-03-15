from dataclasses import dataclass
from typing import Annotated

import pygit2
import typer

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._trailers import Trailers


class AdoptError(ShortcakeError):
    """Error during adopt operation."""

    pass


@dataclass
class AdoptResult:
    branch: str
    parent: str


def _replay_commits(repo: Repo, commits: list[bytes], base: bytes) -> bytes:
    """Replay commits on top of a new base, return final SHA."""
    current_base = base
    # Commits are newest-first, so reverse to replay in order
    for commit_sha in reversed(commits):
        old_commit = repo.get(commit_sha.decode())
        new_sha = git.amend_commit_message(repo, commit_sha, old_commit.message)
        new_commit = repo.get(new_sha.decode())

        new_oid = repo.create_commit(
            None,  # don't update any ref
            old_commit.author,
            pygit2.Signature(
                new_commit.committer.name,
                new_commit.committer.email,
                new_commit.committer.time,
                new_commit.committer.offset,
            ),
            old_commit.message,
            old_commit.tree_id,
            [pygit2.Oid(hex=current_base.decode())],
        )
        current_base = str(new_oid).encode()

    return current_base


def _adopt(
    repo: Repo,
    branch: str | None = None,
    parent: str | None = None,
    force: bool = False,
) -> AdoptResult:
    """
    Track an existing branch by adding Shortcake-Parent trailer.

    Args:
        repo: The git repository.
        branch: Branch to adopt (default: current branch).
        parent: Parent branch (default: main/master).
        force: If True, allow re-parenting an already-tracked branch.

    Raises AdoptError on failure, returns AdoptResult on success.
    """
    # Get default branch for validation and fallback
    default_branch = git.get_default_branch(repo)

    # Resolve branch
    if branch is None:
        branch = git.get_current_branch(repo)

    # Check not default branch
    if branch == default_branch:
        raise AdoptError(f"Cannot adopt default branch '{branch}'")

    # Resolve parent
    if parent is None and (parent := default_branch) is None:
        raise AdoptError("Cannot detect parent branch. Use --parent to specify.")

    # Check parent exists
    if not git.branch_exists(repo, parent):
        raise AdoptError(f"Parent branch '{parent}' not found")

    # Find commits on branch relative to new parent
    branch_head = git.get_branch_head(repo, branch)
    parent_head = git.get_branch_head(repo, parent)
    commits = git.get_commits_between(repo, branch_head, parent_head)

    if not commits:
        raise AdoptError(f"No commits on '{branch}' relative to '{parent}'")

    # When re-parenting with --force, the commit with the Shortcake-Parent
    # trailer may not be commits[-1] (the oldest). This happens when the new
    # parent diverges earlier in history, causing commits from other branches
    # to appear in the diff. Scan all commits to find the right one.
    first_commit = commits[-1]
    trailer_commit_idx = len(commits) - 1  # index of commit with trailer

    if force:
        # Scan from newest to oldest — the branch's own trailer is always
        # the first one we hit going backwards from HEAD, since any deeper
        # trailers belong to ancestor branches that ended up in the range.
        for i in range(len(commits)):
            msg = git.get_commit_message(repo, commits[i])
            t = Trailers.from_message(msg)
            if t.parent_branch is not None:
                first_commit = commits[i]
                trailer_commit_idx = i
                break

    # Check if already tracked
    message = git.get_commit_message(repo, first_commit)
    trailers = Trailers.from_message(message)
    if trailers.parent_branch is not None:
        if not force:
            raise AdoptError(
                f"Branch '{branch}' is already tracked by '{trailers.parent_branch}'. "
                f"Use --force to re-parent."
            )
        # Re-parenting: remove old trailer from message before adding new one
        message = trailers.remove_from(message)

    # Amend with trailer
    new_trailers = Trailers(parent_branch=parent)
    new_message = new_trailers.apply_to(message)
    new_sha = git.amend_commit_message(repo, first_commit, new_message)

    # Rewrite history: replay commits after the amended one
    commits_to_replay = commits[:trailer_commit_idx]
    if commits_to_replay:
        new_sha = _replay_commits(repo, commits_to_replay, new_sha)

    # Update branch ref
    git.update_branch(repo, branch, new_sha.decode())

    return AdoptResult(branch, parent)


# Typer command


def adopt(
    branch: Annotated[str | None, typer.Argument()] = None,
    parent: Annotated[str | None, typer.Option("--parent", "-p")] = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Re-parent an already-tracked branch")
    ] = False,
) -> None:
    """Track an existing branch by adding Shortcake-Parent trailer."""
    repo = git.open_repo()

    try:
        result = _adopt(repo, branch, parent, force=force)
    except AdoptError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if force:
        typer.echo(f"Re-parented '{result.branch}' to '{result.parent}'")
    else:
        typer.echo(f"Adopted '{result.branch}' with parent '{result.parent}'")

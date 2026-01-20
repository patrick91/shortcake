from dataclasses import dataclass
from dulwich.repo import Repo
from shortcake import _git as git

TRAILER_KEY = "Shortcake-Parent"


@dataclass
class AdoptResult:
    branch: str
    parent: str
    success: bool
    error: str | None = None


def get_trailer(message: str, key: str) -> str | None:
    """Extract trailer value from commit message."""
    for line in reversed(message.strip().split("\n")):
        if line.startswith(f"{key}: "):
            return line[len(key) + 2 :]
    return None


def add_trailer(message: str, key: str, value: str) -> str:
    """Add trailer to commit message."""
    return f"{message.rstrip()}\n\n{key}: {value}\n"


def adopt(
    repo: Repo,
    branch: str | None = None,
    parent: str | None = None,
) -> AdoptResult:
    """
    Track an existing branch by adding Shortcake-Parent trailer.

    Returns AdoptResult with success/failure and details.
    """
    # Get default branch for validation and fallback
    default_branch = git.get_default_branch(repo)

    # Resolve branch
    if branch is None:
        branch = git.get_current_branch(repo)

    # Check not default branch
    if branch == default_branch:
        return AdoptResult(branch, "", False, f"Cannot adopt default branch '{branch}'")

    # Resolve parent
    if parent is None:
        parent = default_branch
        if parent is None:
            return AdoptResult(
                branch, "", False, "Cannot detect parent branch. Use --parent to specify."
            )

    # Check parent exists
    if not git.branch_exists(repo, parent):
        return AdoptResult(branch, parent, False, f"Parent branch '{parent}' not found")

    # Find first commit on branch
    branch_head = git.get_branch_head(repo, branch)
    parent_head = git.get_branch_head(repo, parent)
    commits = git.get_commits_between(repo, branch_head, parent_head)

    if not commits:
        return AdoptResult(
            branch, parent, False, f"No commits on '{branch}' relative to '{parent}'"
        )

    # First commit is last in list (walker returns newest first)
    first_commit = commits[-1]

    # Check if already tracked
    message = git.get_commit_message(repo, first_commit)
    if get_trailer(message, TRAILER_KEY) is not None:
        return AdoptResult(branch, parent, False, f"Branch '{branch}' is already tracked")

    # Amend with trailer
    new_message = add_trailer(message, TRAILER_KEY, parent)
    new_sha = git.amend_commit_message(repo, first_commit, new_message)

    # Rewrite history: need to rebase all commits on top of new first commit
    if len(commits) > 1:
        # We need to replay commits on top of the amended first commit
        new_sha = _replay_commits(repo, commits[:-1], new_sha)

    # Update branch ref
    git.update_branch(repo, branch, new_sha)

    return AdoptResult(branch, parent, True)


def _replay_commits(repo: Repo, commits: list[bytes], base: bytes) -> bytes:
    """Replay commits on top of a new base, return final SHA."""
    current_base = base
    # Commits are newest-first, so reverse to replay in order
    for commit_sha in reversed(commits):
        old_commit = repo[commit_sha]
        new_sha = git.amend_commit_message(repo, commit_sha, old_commit.message.decode())
        # Update the parent to point to current_base
        new_commit = repo[new_sha]
        from dulwich.objects import Commit

        fixed_commit = Commit()
        fixed_commit.tree = old_commit.tree
        fixed_commit.parents = [current_base]
        fixed_commit.author = old_commit.author
        fixed_commit.committer = old_commit.committer
        fixed_commit.author_time = old_commit.author_time
        fixed_commit.author_timezone = old_commit.author_timezone
        fixed_commit.commit_time = new_commit.commit_time
        fixed_commit.commit_timezone = old_commit.commit_timezone
        fixed_commit.encoding = old_commit.encoding
        fixed_commit.message = old_commit.message

        repo.object_store.add_object(fixed_commit)
        current_base = fixed_commit.id

    return current_base

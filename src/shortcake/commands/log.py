from dataclasses import dataclass
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake._git._core import Repo
from shortcake._output import get_rich_toolkit


@dataclass
class LogResult:
    commits: list[tuple[str, str]]  # (short_sha, first_line_of_message)
    branch: str
    parent: str | None


def _log(repo: Repo) -> LogResult:
    """Get commits on current branch between parent and HEAD."""
    current = git.get_current_branch(repo)
    if current is None:
        raise ValueError("Cannot log in detached HEAD state")

    all_branches = set(git.get_all_local_branches(repo))
    parent = git.get_branch_parent(repo, current, all_branches)

    head_sha = str(repo.head.target).encode()
    if parent:
        parent_sha = git.get_branch_head(repo, parent)
        commit_shas = git.get_commits_between(repo, head_sha, parent_sha)
    else:
        # Untracked branch - show commits to default branch or just HEAD
        default = git.get_default_branch(repo)
        if default and default != current:
            parent_sha = git.get_branch_head(repo, default)
            commit_shas = git.get_commits_between(repo, head_sha, parent_sha)
        else:
            commit_shas = [head_sha]  # Just show HEAD

    commits = []
    for sha in commit_shas:
        short_sha = sha[:7].decode()
        message = git.get_commit_message(repo, sha).split("\n")[0]
        commits.append((short_sha, message))

    return LogResult(commits=commits, branch=current, parent=parent)


def _render_log(result: LogResult) -> str:
    """Render log result as a tree with pipes."""
    lines: list[str] = []

    # Header: current branch
    lines.append(f"◉ {result.branch}")
    lines.append("│")

    # Commits
    for short_sha, message in result.commits:
        lines.append(f"● {short_sha} {message}")
        lines.append("│")

    # Parent branch at bottom
    if result.parent:
        lines.append(f"◯ {result.parent}")
    else:
        # Remove trailing pipe if no parent to show
        if lines and lines[-1] == "│":
            lines.pop()

    return "\n".join(lines)


def log(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output the log as JSON"),
    ] = False,
) -> None:
    """Show commits on current branch."""
    repo = git.open_repo()

    current = git.get_current_branch(repo)
    if current is None:
        get_rich_toolkit(json_output=json_output).fail(
            "detached_head", "Cannot log in detached HEAD state"
        )

    result = _log(repo)

    if json_output:
        get_rich_toolkit(json_output=True).success(
            {
                "branch": result.branch,
                "parent": result.parent,
                "commits": [
                    {"sha": sha, "subject": subject} for sha, subject in result.commits
                ],
            }
        )
        return

    if not result.commits:
        typer.echo("No commits on this branch.")
        return

    typer.echo(_render_log(result))

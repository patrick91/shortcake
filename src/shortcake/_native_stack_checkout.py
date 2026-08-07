"""Materialize a native GitHub pull request stack during checkout."""

from dataclasses import dataclass, field

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._core import Repo
from shortcake._git._pygit2 import fetch_remote
from shortcake._github import NativeStack, PRInfo
from shortcake._output import ShortcakeRichToolkit, get_rich_toolkit
from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep
from shortcake.commands.reorder import _update_branch_trailer
from shortcake.commands.restack import (
    _get_conflict_files,
    _rebase_branch,
    _show_conflict_message,
    _show_rebase_error,
)


class NativeStackCheckoutError(ShortcakeError):
    """Error materializing GitHub's stack representation."""


@dataclass
class NativeStackCheckoutResult:
    """Result of materializing one native GitHub stack."""

    stack_number: int
    branches: list[str]
    created_branches: list[str] = field(default_factory=list)
    rewritten_branches: list[str] = field(default_factory=list)
    conflict_branch: str | None = None


def _ensure_local_ref(repo: Repo, branch: str, remote_sha: bytes) -> bool:
    """Create a missing local ref."""
    if git.branch_exists(repo, branch):
        return False

    git.create_branch(repo, branch, remote_sha)
    return True


def _delete_created_refs(repo: Repo, branches: list[str]) -> None:
    """Roll back refs created before a checkout plan is durable."""
    for branch in reversed(branches):
        git.delete_branch(repo, branch)


def checkout_native_stack(
    repo: Repo,
    pull_request: PRInfo,
    native_stack: NativeStack,
    *,
    force: bool = False,
    toolkit: ShortcakeRichToolkit | None = None,
) -> NativeStackCheckoutResult:
    """Materialize a native stack with conflict recovery through RestackState."""
    toolkit = toolkit or get_rich_toolkit()
    original_branch = git.get_current_branch(repo)
    if original_branch is None:
        raise NativeStackCheckoutError(
            "Cannot checkout a stack in detached HEAD state."
        )
    if git.has_uncommitted_changes(repo):
        raise NativeStackCheckoutError(
            "You have uncommitted changes. Commit or stash them first."
        )
    if git.is_rebase_in_progress(repo):
        raise NativeStackCheckoutError(
            "Git rebase in progress. Complete or abort it first."
        )
    if RestackState.exists(repo):
        raise NativeStackCheckoutError(
            "Restack already in progress. Use 'sc continue' or 'sc abort'."
        )
    if not git.has_remote(repo, "origin"):
        raise NativeStackCheckoutError("No remote 'origin' configured.")

    open_pull_requests = [
        entry for entry in native_stack.pull_requests if entry.is_open
    ]
    if not open_pull_requests:
        raise NativeStackCheckoutError(
            f"GitHub stack #{native_stack.number} has no open pull requests."
        )

    if not fetch_remote(repo, "origin"):
        raise NativeStackCheckoutError("Failed to fetch from origin.")

    trunk = native_stack.base_ref
    branch_names = [entry.head_ref for entry in open_pull_requests]
    remote_refs: dict[str, bytes] = {}
    for branch in [trunk, *branch_names]:
        remote_sha = git.get_remote_ref(repo, f"origin/{branch}")
        if remote_sha is None:
            raise NativeStackCheckoutError(
                f"Remote branch 'origin/{branch}' was not found."
            )
        remote_refs[branch] = remote_sha

    created_branches: list[str] = []
    for branch in [trunk, *branch_names]:
        if _ensure_local_ref(repo, branch, remote_refs[branch]):
            created_branches.append(branch)

    all_branches = set(git.get_all_local_branches(repo))
    branch_heads = {
        branch: git.get_branch_head(repo, branch) for branch in all_branches
    }
    expected_parents = {
        branch: trunk if index == 0 else branch_names[index - 1]
        for index, branch in enumerate(branch_names)
    }

    first_rewrite: int | None = None
    for index, branch in enumerate(branch_names):
        existing_parent = git.get_branch_parent(
            repo, branch, all_branches, branch_heads
        )
        expected_parent = expected_parents[branch]
        if existing_parent == expected_parent:
            continue
        if branch_heads[branch] != remote_refs[branch]:
            _delete_created_refs(repo, created_branches)
            raise NativeStackCheckoutError(
                f"Local branch '{branch}' differs from 'origin/{branch}' and "
                "does not match the GitHub stack order. Reconcile it before "
                "checking out the stack."
            )
        if existing_parent is not None and not force:
            _delete_created_refs(repo, created_branches)
            raise NativeStackCheckoutError(
                f"Branch '{branch}' is already tracked by '{existing_parent}', "
                f"not '{expected_parent}'. Re-run with --force to re-parent it."
            )
        if first_rewrite is None:
            first_rewrite = index

    target_branch = pull_request.head_ref
    if target_branch not in branch_names:
        target_branch = branch_names[-1]

    if first_rewrite is None:
        try:
            git.switch_branch(repo, target_branch)
        except ValueError as error:
            _delete_created_refs(repo, created_branches)
            raise NativeStackCheckoutError(str(error)) from None
        return NativeStackCheckoutResult(
            native_stack.number,
            branch_names,
            created_branches=created_branches,
        )

    plan: list[RestackStep] = []
    for index in range(first_rewrite, len(branch_names)):
        branch = branch_names[index]
        parent = expected_parents[branch]
        if index == 0:
            merge_base = git.get_merge_base(
                repo, remote_refs[branch], remote_refs[parent]
            )
            if merge_base is None:
                _delete_created_refs(repo, created_branches)
                raise NativeStackCheckoutError(
                    f"'{branch}' shares no history with stack base '{parent}'."
                )
        else:
            merge_base = remote_refs[parent]
            if not git.is_ancestor(repo, merge_base, remote_refs[branch]):
                _delete_created_refs(repo, created_branches)
                raise NativeStackCheckoutError(
                    f"GitHub stack branch '{branch}' is not based on '{parent}'."
                )
        plan.append(
            RestackStep(
                branch=branch,
                onto=parent,
                merge_base=merge_base.decode(),
                new_parent_trailer=parent,
            )
        )

    original_refs = {
        step.branch: git.get_branch_head(repo, step.branch).decode() for step in plan
    }
    state = RestackState(
        version=STATE_VERSION,
        original_branch=original_branch,
        completion_branch=target_branch,
        plan=plan,
        current_index=0,
        original_refs=original_refs,
        created_branches=created_branches,
    )
    state.save(repo)

    rewritten: list[str] = []
    for index, step in enumerate(plan):
        state.current_index = index
        state.save(repo)
        toolkit.echo(f"Rebasing '{step.branch}' onto '{step.onto}'...")
        rebase_result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
        if not rebase_result.success:
            if git.is_rebase_in_progress(repo):
                _show_conflict_message(
                    step.branch,
                    step.onto,
                    _get_conflict_files(repo),
                    toolkit,
                )
            else:
                _show_rebase_error(
                    step.branch, step.onto, rebase_result.error_output, toolkit
                )
            return NativeStackCheckoutResult(
                native_stack.number,
                branch_names,
                created_branches=created_branches,
                rewritten_branches=rewritten,
                conflict_branch=step.branch,
            )

        _update_branch_trailer(repo, step.branch, step.onto)
        rewritten.append(step.branch)

    try:
        git.switch_branch(repo, target_branch, force=True)
    except ValueError as error:
        raise NativeStackCheckoutError(str(error)) from None
    state.delete(repo)
    return NativeStackCheckoutResult(
        native_stack.number,
        branch_names,
        created_branches=created_branches,
        rewritten_branches=rewritten,
    )

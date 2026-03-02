"""Git operations module.

This module is split into submodules for organization:
- _core: Core repo, branch, commit, staging operations
- _rebase: Rebase operations
- _remote: Remote operations
- _stack: Shortcake-specific stack operations (parent/children, tracked branches)

All symbols are re-exported here for backward compatibility.
"""

from shortcake._git._core import (
    DULWICH_ERRORS,
    DULWICH_HOOK_ERRORS,
    DULWICH_IO_ERRORS,
    amend_commit,
    amend_commit_message,
    branch_exists,
    create_branch,
    create_commit,
    delete_branch,
    get_all_local_branches,
    get_branch_head,
    get_commit_message,
    get_commits_between,
    get_conflict_files,
    get_current_branch,
    get_default_branch,
    get_staged_diff,
    get_staged_files,
    has_precommit_hook,
    has_staged_changes,
    has_uncommitted_changes,
    open_repo,
    run_precommit_hook,
    set_head_to_branch,
    switch_branch,
    unstage_all,
    update_branch,
)
from shortcake._git._patch import (
    EmptyPatchError,
    extract_sub_patch,
)
from shortcake._git._rebase import (
    DULWICH_REBASE_ERRORS,
    RebaseFailure,
    RebaseResult,
    cherry_pick,
    get_cherry_pick_head,
    get_merge_base,
    get_rebase_commits,
    is_ancestor,
    is_rebase_in_progress,
    rebase_abort,
    rebase_branch,
    rebase_continue,
)
from shortcake._git._remote import (
    fetch_and_fast_forward_trunk,
    get_remote_ref,
    has_remote,
)
from shortcake._git._stack import (
    get_branch_children,
    get_branch_parent,
    get_branch_parent_info,
    get_merged_branches,
    get_tracked_branches,
    is_merged,
)

__all__ = [
    "DULWICH_ERRORS",
    "DULWICH_HOOK_ERRORS",
    "DULWICH_IO_ERRORS",
    "DULWICH_REBASE_ERRORS",
    "EmptyPatchError",
    "RebaseFailure",
    "RebaseResult",
    "amend_commit",
    "amend_commit_message",
    "branch_exists",
    "cherry_pick",
    "create_branch",
    "create_commit",
    "delete_branch",
    "extract_sub_patch",
    "fetch_and_fast_forward_trunk",
    "get_all_local_branches",
    "get_branch_children",
    "get_branch_head",
    "get_branch_parent",
    "get_branch_parent_info",
    "get_cherry_pick_head",
    "get_commit_message",
    "get_commits_between",
    "get_conflict_files",
    "get_current_branch",
    "get_default_branch",
    "get_merge_base",
    "get_merged_branches",
    "get_rebase_commits",
    "get_remote_ref",
    "get_staged_diff",
    "get_staged_files",
    "get_tracked_branches",
    "has_precommit_hook",
    "has_remote",
    "has_staged_changes",
    "has_uncommitted_changes",
    "is_ancestor",
    "is_merged",
    "is_rebase_in_progress",
    "open_repo",
    "rebase_abort",
    "rebase_branch",
    "rebase_continue",
    "run_precommit_hook",
    "set_head_to_branch",
    "switch_branch",
    "unstage_all",
    "update_branch",
]

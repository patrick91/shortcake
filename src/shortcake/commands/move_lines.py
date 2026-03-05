"""Move selected lines/hunks between branches in a stack."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._exceptions import ShortcakeError
from shortcake._git._patch import EmptyPatchError, Side, extract_sub_patch
from shortcake.commands.restack import _plan_restack, _rebase_branch

# Module-level lock to serialize move operations (they mutate working tree).
_move_lock = threading.Lock()


class MoveError(ShortcakeError):
    """Error during move-lines operation."""

    pass


@dataclass
class MoveResult:
    source_branch: str
    target_branch: str
    file_path: str
    restacked_branches: list[str] = field(default_factory=list)


@dataclass
class HunkSelection:
    file_path: str
    file_patch: str  # full single-file patch (all hunks)
    hunk_index: int  # 0-based index of selected hunk


@dataclass
class MoveHunksResult:
    source_branch: str
    target_branch: str
    file_paths: list[str] = field(default_factory=list)
    restacked_branches: list[str] = field(default_factory=list)


@dataclass
class AcceptResult:
    target_branch: str
    file_paths: list[str] = field(default_factory=list)
    restacked_branches: list[str] = field(default_factory=list)


def _git_apply(
    repo_path: Path,
    patch_content: str,
    reverse: bool = False,
    index: bool = False,
    three_way: bool = False,
) -> None:
    """Apply a patch using git apply."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as tmp:
        tmp.write(patch_content)
        tmp_path = tmp.name

    try:
        cmd = ["git", "apply"]
        if reverse:
            cmd.append("--reverse")
        if three_way:
            cmd.append("--3way")
        elif index:
            cmd.append("--index")
        cmd.append(tmp_path)
        result = subprocess.run(
            cmd, cwd=repo_path, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise MoveError(
                f"Failed to {'reverse-' if reverse else ''}apply patch: "
                f"{result.stderr.strip()}"
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _remove_lines_from_file(
    repo_path: Path, file_path: str, start_line: int, end_line: int
) -> list[str]:
    """Remove lines start_line through end_line (1-indexed) from a file.

    Returns the removed lines. If all lines are removed, deletes the file.
    """
    full_path = repo_path / file_path
    if not full_path.exists():
        raise MoveError(f"File '{file_path}' not found on source branch")

    lines = full_path.read_text().splitlines(keepends=True)
    removed = lines[start_line - 1 : end_line]
    remaining = lines[: start_line - 1] + lines[end_line:]

    if remaining:
        full_path.write_text("".join(remaining))
    else:
        full_path.unlink()

    return removed


def _add_lines_to_file(
    repo_path: Path, file_path: str, lines_to_add: list[str]
) -> None:
    """Add lines to a file on the target branch.

    If the file doesn't exist, creates it. If it exists, appends the lines.
    """
    full_path = repo_path / file_path

    # Ensure parent directories exist
    full_path.parent.mkdir(parents=True, exist_ok=True)

    if full_path.exists():
        existing = full_path.read_text()
        # Ensure there's a newline before appending
        if existing and not existing.endswith("\n"):
            existing += "\n"
        full_path.write_text(existing + "".join(lines_to_add))
    else:
        full_path.write_text("".join(lines_to_add))


def _get_patch_files(patch_content: str) -> list[str]:
    """Extract file paths affected by a patch."""
    files: list[str] = []
    for line in patch_content.splitlines():
        if line.startswith("diff --git a/"):
            # Format: diff --git a/path b/path
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                files.append(parts[1])
    return files


def _stage_patch_files(repo_path: Path, patch_content: str) -> None:
    """Stage only the files affected by a patch (avoids staging untracked files)."""
    files = _get_patch_files(patch_content)
    if not files:
        return
    subprocess.run(
        ["git", "add", "--", *files],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


def _get_tracked_branches_in_order(repo: Repo) -> list[str]:
    """Get all tracked branches in topological order."""
    all_branches = set(git.get_all_local_branches(repo))
    tracked: dict[str, str] = {}
    for branch in all_branches:
        parent = git.get_branch_parent(repo, branch, all_branches)
        if parent is not None:
            tracked[branch] = parent

    # BFS from roots (branches whose parent is untracked)
    order: list[str] = []
    visited: set[str] = set()

    roots = [b for b, p in tracked.items() if p not in tracked]
    roots.sort()
    queue = list(roots)

    while queue:
        branch = queue.pop(0)
        if branch in visited:  # pragma: no cover
            continue
        visited.add(branch)
        order.append(branch)
        children = sorted([b for b, p in tracked.items() if p == branch])
        queue.extend(children)

    return order


def _move_lines(
    repo: Repo,
    source_branch: str,
    target_branch: str,
    file_patch: str,
    file_path: str,
    start_line: int,
    end_line: int,
    side: Side,
) -> MoveResult:
    """Move selected lines from source_branch to target_branch.

    For side='additions': removes lines from the source file directly and
    adds them to the target file.

    For side='deletions': reverse-applies the sub-patch on source (adds back
    deleted lines) and forward-applies it on target (removes lines).

    Raises MoveError on any failure (with rollback of modified refs).
    """
    repo_path = Path(repo.path)

    # --- Preconditions ---
    if source_branch == target_branch:
        raise MoveError("Source and target branches must be different")

    if git.has_uncommitted_changes(repo):
        raise MoveError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise MoveError("Git rebase in progress. Complete or abort it first.")

    all_branches = set(git.get_all_local_branches(repo))

    if not git.branch_exists(repo, source_branch):
        raise MoveError(f"Branch '{source_branch}' does not exist")

    if not git.branch_exists(repo, target_branch):
        raise MoveError(f"Branch '{target_branch}' does not exist")

    source_parent = git.get_branch_parent(repo, source_branch, all_branches)
    if source_parent is None:
        raise MoveError(f"Branch '{source_branch}' is not tracked by Shortcake")

    target_parent = git.get_branch_parent(repo, target_branch, all_branches)
    if target_parent is None:
        raise MoveError(f"Branch '{target_branch}' is not tracked by Shortcake")

    # --- Validate the sub-patch can be extracted ---
    try:
        extract_sub_patch(file_patch, start_line, end_line, side)
    except EmptyPatchError as e:
        raise MoveError(str(e)) from e

    # --- Save state for rollback ---
    original_branch = git.get_current_branch(repo)
    # Save original SHAs for ALL tracked branches (for full rollback).
    all_tracked = _get_tracked_branches_in_order(repo)
    original_refs: dict[str, str] = {}
    for b in all_tracked:
        original_refs[b] = git.get_branch_head(repo, b).decode()
    source_modified = False

    def _rollback() -> None:
        """Restore all modified branch refs and abort any in-progress rebase."""
        if git.is_rebase_in_progress(repo):
            with contextlib.suppress(Exception):
                git.rebase_abort(repo)
        for b, sha in original_refs.items():
            with contextlib.suppress(Exception):
                git.update_branch(repo, b, sha)
        with contextlib.suppress(Exception):
            git.switch_branch(repo, original_branch or source_branch, force=True)

    try:
        # --- Phase 1: Remove from source and amend ---
        git.switch_branch(repo, source_branch)

        if side == "additions":
            removed_lines = _remove_lines_from_file(
                repo_path, file_path, start_line, end_line
            )
        else:
            sub_patch = extract_sub_patch(file_patch, start_line, end_line, side)
            _git_apply(repo_path, sub_patch, reverse=True)
            removed_lines = []

        _stage_patch_files(repo_path, file_patch)
        source_head = git.get_branch_head(repo, source_branch)
        source_message = git.get_commit_message(repo, source_head)
        git.amend_commit(repo, source_message)
        source_modified = True

        # --- Phase 2: Restack so descendants pick up source changes ---
        restacked_phase1: list[str] = []
        plan = _plan_restack(repo, all_tracked)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:
                _rollback()
                raise MoveError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked_phase1.append(step.branch)

        # --- Phase 3: Add to target and amend ---
        git.switch_branch(repo, target_branch)

        if side == "additions":
            try:
                _add_lines_to_file(repo_path, file_path, removed_lines)
            except Exception as exc:  # pragma: no cover
                _rollback()
                raise MoveError(f"Failed to add lines to target: {exc}") from exc
        else:
            sub_patch = extract_sub_patch(file_patch, start_line, end_line, side)
            try:
                _git_apply(repo_path, sub_patch, reverse=False)
            except MoveError:  # pragma: no cover
                _rollback()
                raise

        _stage_patch_files(repo_path, file_patch)
        target_head = git.get_branch_head(repo, target_branch)
        target_message = git.get_commit_message(repo, target_head)
        git.amend_commit(repo, target_message)

        # --- Phase 4: Restack again for target's descendants ---
        restacked_phase2: list[str] = []
        plan = _plan_restack(repo, all_tracked)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:  # pragma: no cover
                _rollback()
                raise MoveError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked_phase2.append(step.branch)

        # --- Cleanup ---
        all_restacked = list(dict.fromkeys(restacked_phase1 + restacked_phase2))
        git.switch_branch(repo, original_branch or source_branch, force=True)

        return MoveResult(
            source_branch=source_branch,
            target_branch=target_branch,
            file_path=file_path,
            restacked_branches=all_restacked,
        )

    except MoveError:
        raise
    except Exception as e:  # pragma: no cover
        if source_modified:
            _rollback()
        else:
            with contextlib.suppress(Exception):
                git.switch_branch(repo, original_branch or source_branch, force=True)
        raise MoveError(f"Unexpected error: {e}") from e


@dataclass
class SplitHunksResult:
    source_branch: str
    new_branch: str
    placement: str  # "before" or "after"
    file_paths: list[str] = field(default_factory=list)
    restacked_branches: list[str] = field(default_factory=list)


def _split_hunks(
    repo: Repo,
    source_branch: str,
    commit_message: str,
    placement: str,
    hunks: list[HunkSelection],
    *,
    no_verify: bool = False,
) -> SplitHunksResult:
    """Split selected hunks from source_branch into a new branch.

    placement="before": new branch becomes parent of source.
        Stack: P → source → children  becomes  P → NEW → source → children
    placement="after": new branch becomes child of source.
        Stack: P → source → child  becomes  P → source → NEW → child

    Raises MoveError on any failure (with rollback of modified refs).
    """
    from shortcake.commands.create import _slugify, _validate_branch_name
    from shortcake.commands.reorder import _update_branch_trailer

    repo_path = Path(repo.path)

    # --- Preconditions ---
    if not hunks:
        raise MoveError("No hunks selected")

    if placement not in ("before", "after"):
        raise MoveError(f"Invalid placement: {placement}")

    if git.has_uncommitted_changes(repo):
        raise MoveError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise MoveError("Git rebase in progress. Complete or abort it first.")

    if not git.branch_exists(repo, source_branch):
        raise MoveError(f"Branch '{source_branch}' does not exist")

    all_branches = set(git.get_all_local_branches(repo))

    source_parent = git.get_branch_parent(repo, source_branch, all_branches)
    if source_parent is None:
        raise MoveError(f"Branch '{source_branch}' is not tracked by Shortcake")

    # Generate and validate new branch name
    new_branch_name = _slugify(commit_message)
    try:
        _validate_branch_name(repo, new_branch_name)
    except Exception as e:
        raise MoveError(str(e)) from e

    # For "after" placement, source must have at most 1 child
    children = git.get_branch_children(repo, source_branch)
    if placement == "after" and len(children) > 1:
        raise MoveError(
            f"Branch '{source_branch}' has multiple children "
            f"({', '.join(children)}). Cannot split after."
        )

    # --- Validate and extract each hunk patch ---
    individual_patches: list[tuple[str, str]] = []
    for hunk in hunks:
        patch = _extract_hunk_patch(hunk.file_patch, hunk.hunk_index)
        individual_patches.append((hunk.file_path, patch))

    # --- Build combined patch ---
    combined_patch = _combine_patches(individual_patches)

    # --- Save state for rollback ---
    original_branch = git.get_current_branch(repo)
    all_tracked = _get_tracked_branches_in_order(repo)
    original_refs: dict[str, str] = {}
    for b in all_tracked:
        original_refs[b] = git.get_branch_head(repo, b).decode()
    source_modified = False

    def _rollback() -> None:
        """Restore all modified branch refs and abort any in-progress rebase."""
        if git.is_rebase_in_progress(repo):
            with contextlib.suppress(Exception):
                git.rebase_abort(repo)
        for b, sha in original_refs.items():
            with contextlib.suppress(Exception):
                git.update_branch(repo, b, sha)
        # Delete the new branch if it was created
        if git.branch_exists(repo, new_branch_name):
            with contextlib.suppress(Exception):
                git.delete_branch(repo, new_branch_name)
        with contextlib.suppress(Exception):
            git.switch_branch(repo, original_branch or source_branch, force=True)

    try:
        # --- Phase 1: Remove hunks from source and amend ---
        git.switch_branch(repo, source_branch)
        _git_apply(repo_path, combined_patch, reverse=True)
        _stage_patch_files(repo_path, combined_patch)
        source_head = git.get_branch_head(repo, source_branch)
        source_message = git.get_commit_message(repo, source_head)
        git.amend_commit(repo, source_message, no_verify=no_verify)
        source_modified = True

        if placement == "before":
            # --- Phase 2a: Restack source descendants after amend ---
            restacked_phase1: list[str] = []
            plan = _plan_restack(repo, all_tracked)
            for step in plan:
                result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
                if not result.success:
                    _rollback()
                    raise MoveError(
                        f"Restack failed for '{step.branch}': {result.error_output}"
                    )
                restacked_phase1.append(step.branch)

            # --- Phase 3a: Create new branch at source's parent HEAD ---
            parent_head = git.get_branch_head(repo, source_parent)
            git.create_branch(repo, new_branch_name, parent_head)
            git.switch_branch(repo, new_branch_name)

            # --- Phase 4a: Forward-apply hunks and commit ---
            _git_apply(repo_path, combined_patch, reverse=False)
            _stage_patch_files(repo_path, combined_patch)

            from shortcake._trailers import Trailers

            trailers = Trailers(parent_branch=source_parent)
            full_message = trailers.apply_to(commit_message)
            git.create_commit(repo, full_message, no_verify=True)

            # --- Phase 5a: Update source's parent trailer to point to new branch ---
            _update_branch_trailer(repo, source_branch, new_branch_name)

            # --- Phase 6a: Restack source (and descendants) onto new branch ---
            # Re-read tracked branches since we added a new one
            all_tracked_new = _get_tracked_branches_in_order(repo)
            restacked_phase2: list[str] = []
            plan = _plan_restack(repo, all_tracked_new)
            for step in plan:
                result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
                if not result.success:
                    _rollback()
                    raise MoveError(
                        f"Restack failed for '{step.branch}': {result.error_output}"
                    )
                restacked_phase2.append(step.branch)

            all_restacked = list(dict.fromkeys(restacked_phase1 + restacked_phase2))

        else:
            # placement == "after"
            # --- Phase 2b: Restack source descendants after amend ---
            restacked_phase1 = []
            plan = _plan_restack(repo, all_tracked)
            for step in plan:
                result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
                if not result.success:
                    _rollback()
                    raise MoveError(
                        f"Restack failed for '{step.branch}': {result.error_output}"
                    )
                restacked_phase1.append(step.branch)

            # --- Phase 3b: Create new branch at source's (amended) HEAD ---
            source_amended_head = git.get_branch_head(repo, source_branch)
            git.create_branch(repo, new_branch_name, source_amended_head)
            git.switch_branch(repo, new_branch_name)

            # --- Phase 4b: Forward-apply hunks and commit ---
            _git_apply(repo_path, combined_patch, reverse=False)
            _stage_patch_files(repo_path, combined_patch)

            from shortcake._trailers import Trailers

            trailers = Trailers(parent_branch=source_branch)
            full_message = trailers.apply_to(commit_message)
            git.create_commit(repo, full_message, no_verify=True)

            # --- Phase 5b: If source had a child, update child's parent to new branch ---
            restacked_phase2 = []
            if children:
                child = children[0]
                _update_branch_trailer(repo, child, new_branch_name)

                # Restack child (and its descendants) onto new branch
                all_tracked_new = _get_tracked_branches_in_order(repo)
                plan = _plan_restack(repo, all_tracked_new)
                for step in plan:
                    result = _rebase_branch(
                        repo, step.branch, step.onto, step.merge_base
                    )
                    if not result.success:
                        _rollback()
                        raise MoveError(
                            f"Restack failed for '{step.branch}': {result.error_output}"
                        )
                    restacked_phase2.append(step.branch)

            all_restacked = list(dict.fromkeys(restacked_phase1 + restacked_phase2))

        # --- Cleanup ---
        git.switch_branch(repo, original_branch or source_branch, force=True)

        file_paths = list(dict.fromkeys(h.file_path for h in hunks))

        return SplitHunksResult(
            source_branch=source_branch,
            new_branch=new_branch_name,
            placement=placement,
            file_paths=file_paths,
            restacked_branches=all_restacked,
        )

    except MoveError:
        raise
    except Exception as e:  # pragma: no cover
        if source_modified:
            _rollback()
        else:
            with contextlib.suppress(Exception):
                git.switch_branch(repo, original_branch or source_branch, force=True)
        raise MoveError(f"Unexpected error: {e}") from e


def _stash_push(repo_path: Path) -> bool:
    """Stash uncommitted changes. Returns True if something was stashed."""
    result = subprocess.run(
        ["git", "stash", "push", "-u", "-m", "shortcake-accept-temp"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # "No local changes to save" means nothing was stashed
    if result.returncode != 0:  # pragma: no cover
        return False
    return "No local changes to save" not in result.stdout


def _stash_pop(repo_path: Path) -> None:
    """Pop the most recent stash entry.

    If the pop results in merge conflicts (e.g. because the base commit was
    amended), reset the index to resolve conflict markers and drop the stash.
    The working tree will retain the merged content.
    """
    result = subprocess.run(
        ["git", "stash", "pop"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and "CONFLICT" in result.stdout:  # pragma: no cover
        # Resolve conflicted index entries and drop the stash manually
        subprocess.run(
            ["git", "reset", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "stash", "drop"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )


def _extract_hunk_patch(file_patch: str, hunk_index: int) -> str:
    """Extract a single hunk from a file patch, returning a valid patch.

    Takes a single-file patch and returns a patch containing only the
    selected hunk (file headers + that one hunk).

    Raises MoveError if hunk_index is out of range.
    """
    lines = file_patch.split("\n")

    # Separate file headers from hunks
    file_headers: list[str] = []
    hunk_start_indices: list[int] = []

    for i, line in enumerate(lines):
        if line.startswith("@@"):
            hunk_start_indices.append(i)
            if not file_headers:
                file_headers = lines[:i]

    if not hunk_start_indices:  # pragma: no cover
        raise MoveError("No hunks found in patch")

    if not file_headers:  # pragma: no cover
        file_headers = lines[: hunk_start_indices[0]]

    if hunk_index < 0 or hunk_index >= len(hunk_start_indices):
        raise MoveError(
            f"Invalid hunk index {hunk_index}: "
            f"patch has {len(hunk_start_indices)} hunk(s)"
        )

    # Extract the selected hunk
    start = hunk_start_indices[hunk_index]
    if hunk_index + 1 < len(hunk_start_indices):
        end = hunk_start_indices[hunk_index + 1]
    else:
        end = len(lines)

    hunk_lines = lines[start:end]

    # Remove trailing empty lines
    while hunk_lines and hunk_lines[-1] == "":  # pragma: no cover
        hunk_lines.pop()

    return "\n".join(file_headers) + "\n" + "\n".join(hunk_lines) + "\n"


def _combine_patches(hunk_patches: list[tuple[str, str]]) -> str:
    """Combine multiple single-hunk patches into one multi-file patch.

    Takes a list of (file_path, single_hunk_patch) tuples and merges hunks
    that share a file path into a single patch.
    """
    from collections import OrderedDict

    # Group hunks by file path, preserving order
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    file_headers_map: dict[str, str] = {}

    for file_path, hunk_patch in hunk_patches:
        patch_lines = hunk_patch.split("\n")

        # Find where the hunk starts (first @@ line)
        header_lines: list[str] = []
        hunk_content: list[str] = []
        in_hunk = False

        for line in patch_lines:
            if line.startswith("@@"):
                in_hunk = True
            if in_hunk:
                hunk_content.append(line)
            else:
                header_lines.append(line)

        if file_path not in file_headers_map:
            file_headers_map[file_path] = "\n".join(header_lines)
            grouped[file_path] = []

        # Remove trailing empty lines from hunk content
        while hunk_content and hunk_content[-1] == "":
            hunk_content.pop()

        grouped[file_path].append("\n".join(hunk_content))

    # Build combined patch
    parts: list[str] = []
    for file_path, hunks in grouped.items():
        parts.append(file_headers_map[file_path])
        for hunk in hunks:
            parts.append(hunk)

    return "\n".join(parts) + "\n"


def _accept_working_hunks(
    repo: Repo,
    target_branch: str,
    hunks: list[HunkSelection],
) -> AcceptResult:
    """Accept selected hunks from working changes into a target branch's commit.

    Uses full-hunk reverse-apply on the working tree (atomic), stashes
    remaining changes, switches to target, forward-applies, amends,
    restacks, then switches back and pops stash.

    Raises MoveError on any failure (with rollback of modified refs).
    """
    repo_path = Path(repo.path)

    # --- Preconditions ---
    if not hunks:
        raise MoveError("No hunks selected")

    if git.is_rebase_in_progress(repo):
        raise MoveError("Git rebase in progress. Complete or abort it first.")

    if not git.branch_exists(repo, target_branch):
        raise MoveError(f"Branch '{target_branch}' does not exist")

    all_branches = set(git.get_all_local_branches(repo))

    target_parent = git.get_branch_parent(repo, target_branch, all_branches)
    if target_parent is None:
        raise MoveError(f"Branch '{target_branch}' is not tracked by Shortcake")

    # --- Validate and extract each hunk patch ---
    individual_patches: list[tuple[str, str]] = []
    for hunk in hunks:
        patch = _extract_hunk_patch(hunk.file_patch, hunk.hunk_index)
        individual_patches.append((hunk.file_path, patch))

    # --- Build combined patch ---
    combined_patch = _combine_patches(individual_patches)

    # --- Save state for rollback ---
    original_branch = git.get_current_branch(repo)
    all_tracked = _get_tracked_branches_in_order(repo)
    original_refs: dict[str, str] = {}
    for b in all_tracked:
        original_refs[b] = git.get_branch_head(repo, b).decode()
    stashed = False

    def _rollback() -> None:
        """Restore all modified branch refs, abort rebase, switch back, pop stash."""
        if git.is_rebase_in_progress(repo):
            with contextlib.suppress(Exception):
                git.rebase_abort(repo)
        for b, sha in original_refs.items():
            with contextlib.suppress(Exception):
                git.update_branch(repo, b, sha)
        with contextlib.suppress(Exception):
            git.switch_branch(repo, original_branch or target_branch, force=True)
        if stashed:  # pragma: no cover
            _stash_pop(repo_path)

    try:
        # --- Step 1: Reverse-apply combined patch on working tree ---
        # This atomically removes the selected hunks. If it fails,
        # the working tree is untouched and no rollback is needed.
        _git_apply(repo_path, combined_patch, reverse=True)

        # --- Step 2: Stash remaining uncommitted changes ---
        stashed = _stash_push(repo_path)

        # --- Step 3: Switch to target branch ---
        git.switch_branch(repo, target_branch)

        # --- Step 4: Forward-apply combined patch on target ---
        try:
            _git_apply(repo_path, combined_patch, reverse=False)
        except MoveError:  # pragma: no cover
            _rollback()
            raise

        # --- Step 5: Stage and amend ---
        _stage_patch_files(repo_path, combined_patch)
        target_head = git.get_branch_head(repo, target_branch)
        target_message = git.get_commit_message(repo, target_head)
        git.amend_commit(repo, target_message)

        # --- Step 6: Restack downstream branches ---
        restacked: list[str] = []
        plan = _plan_restack(repo, all_tracked)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:  # pragma: no cover
                _rollback()
                raise MoveError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked.append(step.branch)

        # --- Step 7: Switch back and pop stash ---
        git.switch_branch(repo, original_branch or target_branch, force=True)
        if stashed:
            _stash_pop(repo_path)
            stashed = False

        # Collect unique file paths
        file_paths = list(dict.fromkeys(h.file_path for h in hunks))

        return AcceptResult(
            target_branch=target_branch,
            file_paths=file_paths,
            restacked_branches=restacked,
        )

    except MoveError:
        raise
    except Exception as e:  # pragma: no cover
        _rollback()
        raise MoveError(f"Unexpected error: {e}") from e


@dataclass
class LineSelection:
    file_path: str
    file_patch: str
    start_line: int
    end_line: int
    side: Side


@dataclass
class SplitChunk:
    commit_message: str
    selections: list[LineSelection]


@dataclass
class SplitLinesBatchResult:
    source_branch: str
    new_branches: list[str] = field(default_factory=list)
    restacked_branches: list[str] = field(default_factory=list)


def _check_no_overlapping_selections(chunks: list[SplitChunk]) -> None:
    """Check that no line ranges overlap across chunks for same (file_path, side).

    Raises MoveError if any overlap is detected.
    """
    # Collect all ranges per (file_path, side)
    ranges: dict[tuple[str, Side], list[tuple[int, int]]] = {}
    for chunk in chunks:
        for sel in chunk.selections:
            key = (sel.file_path, sel.side)
            ranges.setdefault(key, []).append((sel.start_line, sel.end_line))

    for key, intervals in ranges.items():
        intervals.sort()
        for i in range(1, len(intervals)):
            prev_end = intervals[i - 1][1]
            curr_start = intervals[i][0]
            if curr_start <= prev_end:
                file_path, side = key
                raise MoveError(
                    f"Overlapping line ranges in '{file_path}' ({side}): "
                    f"[{intervals[i-1][0]}, {prev_end}] and [{curr_start}, {intervals[i][1]}]"
                )


def _build_combined_patch_from_selections(
    selections: list[LineSelection],
) -> str:
    """Build a combined patch from a list of LineSelection objects.

    Calls extract_sub_patch() for each selection, then feeds results
    into _combine_patches().
    """
    individual_patches: list[tuple[str, str]] = []
    for sel in selections:
        try:
            sub_patch = extract_sub_patch(
                sel.file_patch, sel.start_line, sel.end_line, sel.side
            )
        except EmptyPatchError as e:
            raise MoveError(str(e)) from e
        individual_patches.append((sel.file_path, sub_patch))
    return _combine_patches(individual_patches)


def _split_lines_batch(
    repo: Repo,
    source_branch: str,
    chunks: list[SplitChunk],
    *,
    no_verify: bool = False,
) -> SplitLinesBatchResult:
    """Split selected line ranges from source_branch into multiple new branches.

    All chunks are placed "before" the source branch.
    Result: P → chunk1 → chunk2 → ... → source

    Uses direct file manipulation to avoid patch conflicts with new files.

    Raises MoveError on any failure (with rollback of modified refs).
    """
    from shortcake.commands.create import _slugify, _validate_branch_name

    repo_path = Path(repo.path)

    # --- Preconditions ---
    if not chunks:
        raise MoveError("No chunks provided")

    for chunk in chunks:
        if not chunk.selections:
            raise MoveError("Chunk has no selections")

    if git.has_uncommitted_changes(repo):
        raise MoveError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise MoveError("Git rebase in progress. Complete or abort it first.")

    if not git.branch_exists(repo, source_branch):
        raise MoveError(f"Branch '{source_branch}' does not exist")

    all_branches = set(git.get_all_local_branches(repo))

    source_parent = git.get_branch_parent(repo, source_branch, all_branches)
    if source_parent is None:
        raise MoveError(f"Branch '{source_branch}' is not tracked by Shortcake")

    # Generate and validate all new branch names, check for duplicates
    new_branch_names: list[str] = []
    seen_names: set[str] = set()
    for chunk in chunks:
        name = _slugify(chunk.commit_message)
        if name in seen_names:
            raise MoveError(f"Duplicate branch name: '{name}'")
        seen_names.add(name)
        try:
            _validate_branch_name(repo, name)
        except Exception as e:
            raise MoveError(str(e)) from e
        new_branch_names.append(name)

    # Check for overlapping selections
    _check_no_overlapping_selections(chunks)

    # Pre-validate all selections via extract_sub_patch
    for chunk in chunks:
        _build_combined_patch_from_selections(chunk.selections)

    # --- Collect affected files and line mappings ---
    affected_files: set[str] = set()
    # Per-chunk addition line numbers: chunk_idx → file → set of line numbers
    chunk_addition_lines: list[dict[str, set[int]]] = []
    # All selected addition line numbers per file (across all chunks)
    all_selected_lines: dict[str, set[int]] = {}
    for chunk in chunks:
        chunk_lines: dict[str, set[int]] = {}
        for sel in chunk.selections:
            affected_files.add(sel.file_path)
            if sel.side == "additions":
                s = chunk_lines.setdefault(sel.file_path, set())
                all_s = all_selected_lines.setdefault(sel.file_path, set())
                for ln in range(sel.start_line, sel.end_line + 1):
                    s.add(ln)
                    all_s.add(ln)
        chunk_addition_lines.append(chunk_lines)

    # --- Save state for rollback ---
    original_branch = git.get_current_branch(repo)
    all_tracked = _get_tracked_branches_in_order(repo)
    original_refs: dict[str, str] = {}
    for b in all_tracked:
        original_refs[b] = git.get_branch_head(repo, b).decode()
    created_branches: list[str] = []

    def _rollback() -> None:
        """Restore all modified branch refs, delete created branches."""
        if git.is_rebase_in_progress(repo):
            with contextlib.suppress(Exception):
                git.rebase_abort(repo)
        for b, sha in original_refs.items():
            with contextlib.suppress(Exception):
                git.update_branch(repo, b, sha)
        for b in created_branches:
            if git.branch_exists(repo, b):
                with contextlib.suppress(Exception):
                    git.delete_branch(repo, b)
        with contextlib.suppress(Exception):
            git.switch_branch(repo, original_branch or source_branch, force=True)

    try:
        # --- Save original file contents ---
        git.switch_branch(repo, source_branch)
        original_file_contents: dict[str, list[str]] = {}
        for fp in affected_files:
            full_path = repo_path / fp
            if full_path.exists():
                original_file_contents[fp] = full_path.read_text().splitlines(
                    keepends=True
                )

        # Save source commit message (without trailer) for later
        source_head = git.get_branch_head(repo, source_branch)
        source_message = git.get_commit_message(repo, source_head)

        # --- Phase 1: Create each chunk branch in order ---
        current_parent = source_parent
        from shortcake._trailers import Trailers

        # Track accumulated addition lines per file (for building file content)
        accumulated_lines: dict[str, set[int]] = {}

        for i, (chunk, branch_name) in enumerate(zip(chunks, new_branch_names)):
            parent_head = git.get_branch_head(repo, current_parent)
            git.create_branch(repo, branch_name, parent_head)
            created_branches.append(branch_name)
            git.switch_branch(repo, branch_name)

            # Handle additions: write file content with accumulated lines
            addition_lines = chunk_addition_lines[i]
            files_modified: set[str] = set()
            for file_path, new_line_nums in addition_lines.items():
                accumulated_lines.setdefault(file_path, set())
                accumulated_lines[file_path] |= new_line_nums

                orig = original_file_contents[file_path]
                content_lines = [
                    orig[ln - 1]
                    for ln in sorted(accumulated_lines[file_path])
                    if ln - 1 < len(orig)
                ]
                full_path = repo_path / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text("".join(content_lines))
                files_modified.add(file_path)

            # Handle deletions: forward-apply sub-patches
            for sel in chunk.selections:
                if sel.side == "deletions":
                    sub_patch = extract_sub_patch(
                        sel.file_patch, sel.start_line, sel.end_line, sel.side
                    )
                    _git_apply(repo_path, sub_patch, reverse=False)
                    files_modified.add(sel.file_path)

            # Stage and commit
            if files_modified:
                subprocess.run(
                    ["git", "add", "--", *sorted(files_modified)],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )

            trailers = Trailers(parent_branch=current_parent)
            full_message = trailers.apply_to(chunk.commit_message)
            git.create_commit(repo, full_message, no_verify=True)

            current_parent = branch_name

        # --- Phase 2: Rewrite source branch onto last chunk ---
        # Move source ref to last chunk's HEAD (avoids rebase conflicts)
        last_chunk_head = git.get_branch_head(repo, current_parent)
        git.update_branch(repo, source_branch, last_chunk_head.decode())
        git.switch_branch(repo, source_branch, force=True)

        # Write original file contents back (source should have ALL lines)
        for fp in affected_files:
            if fp in original_file_contents:
                full_path = repo_path / fp
                full_path.write_text("".join(original_file_contents[fp]))

        # Stage affected files
        if affected_files:
            subprocess.run(
                ["git", "add", "--", *sorted(affected_files)],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

        # Check if there are actual changes to commit (non-selected lines exist)
        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        has_remaining_changes = status.returncode != 0

        # Build new message with updated trailer
        old_trailers = Trailers.from_message(source_message)
        clean_message = old_trailers.remove_from(source_message)
        new_trailers = Trailers(parent_branch=current_parent)
        new_source_message = new_trailers.apply_to(clean_message)

        if has_remaining_changes:
            no_verify_args = ["--no-verify"] if no_verify else []
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    new_source_message,
                    *no_verify_args,
                ],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            # All lines were selected; create an empty commit to keep the branch
            no_verify_args = ["--no-verify"] if no_verify else []
            subprocess.run(
                [
                    "git",
                    "commit",
                    "--allow-empty",
                    "-m",
                    new_source_message,
                    *no_verify_args,
                ],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

        # --- Phase 3: Restack source's descendants ---
        all_tracked_new = _get_tracked_branches_in_order(repo)
        restacked: list[str] = []
        plan = _plan_restack(repo, all_tracked_new)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:
                _rollback()
                raise MoveError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked.append(step.branch)

        # --- Cleanup ---
        git.switch_branch(repo, original_branch or source_branch, force=True)

        return SplitLinesBatchResult(
            source_branch=source_branch,
            new_branches=list(new_branch_names),
            restacked_branches=restacked,
        )

    except MoveError:
        raise
    except Exception as e:  # pragma: no cover
        _rollback()
        raise MoveError(f"Unexpected error: {e}") from e


def _move_hunks(
    repo: Repo,
    source_branch: str,
    target_branch: str,
    hunks: list[HunkSelection],
    *,
    no_verify: bool = False,
) -> MoveHunksResult:
    """Move selected hunks from source_branch to target_branch.

    Reverse-applies the combined hunk patch on the source (removes changes),
    amends the source commit, restacks, then forward-applies on the target,
    amends the target commit, and restacks again.

    Raises MoveError on any failure (with rollback of modified refs).
    """
    repo_path = Path(repo.path)

    # --- Preconditions ---
    if not hunks:
        raise MoveError("No hunks selected")

    if source_branch == target_branch:
        raise MoveError("Source and target branches must be different")

    if git.has_uncommitted_changes(repo):
        raise MoveError("You have uncommitted changes. Commit or stash them first.")

    if git.is_rebase_in_progress(repo):
        raise MoveError("Git rebase in progress. Complete or abort it first.")

    if not git.branch_exists(repo, source_branch):
        raise MoveError(f"Branch '{source_branch}' does not exist")

    if not git.branch_exists(repo, target_branch):
        raise MoveError(f"Branch '{target_branch}' does not exist")

    all_branches = set(git.get_all_local_branches(repo))

    source_parent = git.get_branch_parent(repo, source_branch, all_branches)
    if source_parent is None:
        raise MoveError(f"Branch '{source_branch}' is not tracked by Shortcake")

    target_parent = git.get_branch_parent(repo, target_branch, all_branches)
    if target_parent is None:
        raise MoveError(f"Branch '{target_branch}' is not tracked by Shortcake")

    # --- Validate and extract each hunk patch ---
    individual_patches: list[tuple[str, str]] = []
    for hunk in hunks:
        patch = _extract_hunk_patch(hunk.file_patch, hunk.hunk_index)
        individual_patches.append((hunk.file_path, patch))

    # --- Build combined patch ---
    combined_patch = _combine_patches(individual_patches)

    # --- Save state for rollback ---
    original_branch = git.get_current_branch(repo)
    all_tracked = _get_tracked_branches_in_order(repo)
    original_refs: dict[str, str] = {}
    for b in all_tracked:
        original_refs[b] = git.get_branch_head(repo, b).decode()
    source_modified = False

    def _rollback() -> None:
        """Restore all modified branch refs and abort any in-progress rebase."""
        if git.is_rebase_in_progress(repo):
            with contextlib.suppress(Exception):
                git.rebase_abort(repo)
        for b, sha in original_refs.items():
            with contextlib.suppress(Exception):
                git.update_branch(repo, b, sha)
        with contextlib.suppress(Exception):
            git.switch_branch(repo, original_branch or source_branch, force=True)

    try:
        # --- Phase 1: Remove from source branch ---
        git.switch_branch(repo, source_branch)
        _git_apply(repo_path, combined_patch, reverse=True)
        _stage_patch_files(repo_path, combined_patch)
        source_head = git.get_branch_head(repo, source_branch)
        source_message = git.get_commit_message(repo, source_head)
        git.amend_commit(repo, source_message, no_verify=no_verify)
        source_modified = True

        # --- Phase 2: Restack after source changes ---
        restacked_phase1: list[str] = []
        plan = _plan_restack(repo, all_tracked)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:
                _rollback()
                raise MoveError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked_phase1.append(step.branch)

        # --- Phase 3: Add to target branch ---
        git.switch_branch(repo, target_branch)
        try:
            _git_apply(repo_path, combined_patch, reverse=False)
        except MoveError:  # pragma: no cover
            _rollback()
            raise
        _stage_patch_files(repo_path, combined_patch)
        target_head = git.get_branch_head(repo, target_branch)
        target_message = git.get_commit_message(repo, target_head)
        git.amend_commit(repo, target_message, no_verify=no_verify)

        # --- Phase 4: Restack after target changes ---
        restacked_phase2: list[str] = []
        plan = _plan_restack(repo, all_tracked)
        for step in plan:
            result = _rebase_branch(repo, step.branch, step.onto, step.merge_base)
            if not result.success:  # pragma: no cover
                _rollback()
                raise MoveError(
                    f"Restack failed for '{step.branch}': {result.error_output}"
                )
            restacked_phase2.append(step.branch)

        # --- Cleanup ---
        all_restacked = list(dict.fromkeys(restacked_phase1 + restacked_phase2))
        git.switch_branch(repo, original_branch or source_branch, force=True)

        file_paths = list(dict.fromkeys(h.file_path for h in hunks))

        return MoveHunksResult(
            source_branch=source_branch,
            target_branch=target_branch,
            file_paths=file_paths,
            restacked_branches=all_restacked,
        )

    except MoveError:
        raise
    except Exception as e:  # pragma: no cover
        if source_modified:
            _rollback()
        else:
            with contextlib.suppress(Exception):
                git.switch_branch(repo, original_branch or source_branch, force=True)
        raise MoveError(f"Unexpected error: {e}") from e

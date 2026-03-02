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


def _stage_all(repo_path: Path) -> None:
    """Stage all changes (git add -A to catch deletions and new files)."""
    subprocess.run(
        ["git", "add", "-A"],
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

        _stage_all(repo_path)
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

        _stage_all(repo_path)
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
        _stage_all(repo_path)
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

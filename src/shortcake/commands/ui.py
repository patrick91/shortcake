from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import parse_qs, unquote, urlparse

import typer

from shortcake import _git as git
from shortcake._recap import (
    RecapError,
    build_branch_patch,
    build_working_patch,
    list_recaps,
    load_recap,
    stored_recap_payload,
)

if TYPE_CHECKING:
    from shortcake._git._core import Repo
from shortcake._github import (
    BranchGitHubInfo,
    GitHubClient,
    get_github_token,
    get_repo_info,
)
from shortcake._tree import BranchNode, StackTree
from shortcake.commands._review import (
    ReviewResult,
    _get_available_models,
    _run_review,
    _run_synthesis,
)
from shortcake.commands._suggest import _compute_suggestions
from shortcake.commands.move_lines import (
    HunkSelection,
    LineSelection,
    MoveError,
    SplitChunk,
    _accept_working_hunks,
    _move_hunks,
    _move_lock,
    _split_hunks,
    _split_lines_batch,
)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

DEFAULT_UI_PORT = 8765
DEFAULT_DEV_WEB_PORT = 6173
BACKGROUND_START_TIMEOUT_SECONDS = 30.0
UI_STATE_VERSION = 1
UI_STATE_FILE = "ui-state.json"
UI_SESSION_FILE = "ui-session.json"
DIFF_STYLES = {"unified", "split"}


@dataclass(frozen=True)
class StackDiffBranch:
    name: str
    parent: str
    depth: int
    is_current: bool
    commit_count: int
    commit: str
    commit_short: str
    commit_subject: str
    commit_time: int


@dataclass(frozen=True)
class UISession:
    host: str
    port: int
    pid: int
    repo_path: str
    origin: str
    mode: str


def _empty_persisted_ui_state() -> dict[str, Any]:
    return {
        "version": UI_STATE_VERSION,
        "diffStyle": "unified",
        "viewedFiles": {},
    }


def _get_shortcake_state_dir(repo: Repo) -> Path:
    state_dir = Path(repo.path) / "shortcake"
    state_dir.mkdir(exist_ok=True)
    return state_dir


def _get_persisted_ui_state_path(repo: Repo) -> Path:
    return _get_shortcake_state_dir(repo) / UI_STATE_FILE


@contextlib.contextmanager
def _locked_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _get_persisted_ui_state_lock_path(repo: Repo) -> Path:
    return _get_shortcake_state_dir(repo) / "ui-state.lock"


def _normalize_persisted_ui_state(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("version") != UI_STATE_VERSION:
        return _empty_persisted_ui_state()

    diff_style = data.get("diffStyle")
    if diff_style not in DIFF_STYLES:
        diff_style = "unified"

    viewed_files: dict[str, dict[str, str]] = {}
    raw_viewed_files = data.get("viewedFiles", {})
    if isinstance(raw_viewed_files, dict):
        for raw_scope, raw_files in raw_viewed_files.items():
            if not isinstance(raw_scope, str) or not isinstance(raw_files, dict):
                continue
            files = {
                path: patch_key
                for path, patch_key in raw_files.items()
                if isinstance(path, str) and isinstance(patch_key, str)
            }
            if files:
                viewed_files[raw_scope] = files

    return {
        "version": UI_STATE_VERSION,
        "diffStyle": diff_style,
        "viewedFiles": viewed_files,
    }


def _load_persisted_ui_state(repo: Repo) -> dict[str, Any]:
    state_path = _get_persisted_ui_state_path(repo)
    if not state_path.exists():
        return _empty_persisted_ui_state()

    try:
        with open(state_path) as f:
            return _normalize_persisted_ui_state(json.load(f))
    except (json.JSONDecodeError, OSError, TypeError):
        return _empty_persisted_ui_state()


def _save_persisted_ui_state(repo: Repo, state: dict[str, Any]) -> None:
    state_path = _get_persisted_ui_state_path(repo)
    tmp_path = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(_normalize_persisted_ui_state(state), f, indent=2)
            f.write("\n")
        os.replace(tmp_path, state_path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        pass


def _update_persisted_ui_state(repo: Repo, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")

    with _locked_file(_get_persisted_ui_state_lock_path(repo)):
        state = _load_persisted_ui_state(repo)

        if "diffStyle" in body:
            diff_style = body["diffStyle"]
            if diff_style not in DIFF_STYLES:
                raise ValueError("diffStyle must be 'unified' or 'split'")
            state["diffStyle"] = diff_style

        if "viewedScope" in body or "viewedFiles" in body:
            scope = body.get("viewedScope")
            raw_files = body.get("viewedFiles")
            if not isinstance(scope, str) or not isinstance(raw_files, dict):
                raise ValueError("viewedScope and viewedFiles are required")

            files = {
                path: patch_key
                for path, patch_key in raw_files.items()
                if isinstance(path, str) and isinstance(patch_key, str)
            }
            viewed_files = state.setdefault("viewedFiles", {})
            if files:
                viewed_files[scope] = files
            elif isinstance(viewed_files, dict) and scope in viewed_files:
                del viewed_files[scope]

        _save_persisted_ui_state(repo, state)
        return _load_persisted_ui_state(repo)


def _tracked_branch_parents(repo: Repo) -> dict[str, str]:
    """Return tracked branches and their parent branch names."""
    all_branches = set(git.get_all_local_branches(repo))
    branch_heads = {name: git.get_branch_head(repo, name) for name in all_branches}

    tracked: dict[str, str] = {}
    for branch in all_branches:
        parent = git.get_branch_parent(repo, branch, all_branches, branch_heads)
        if parent is not None:
            tracked[branch] = parent

    return tracked


def _collect_stack_nodes(tree: StackTree) -> list[tuple[BranchNode, int]]:
    """Return stack nodes in deterministic pre-order with depth info."""
    nodes: list[tuple[BranchNode, int]] = []

    def visit(node: BranchNode, depth: int) -> None:
        nodes.append((node, depth))
        for child in node.children:
            visit(child, depth + 1)

    for root in tree.roots:
        visit(root, 0)

    return nodes


def _get_stack_diff_branches(repo: Repo) -> list[StackDiffBranch]:
    tracked = _tracked_branch_parents(repo)
    if not tracked:
        return []

    all_branches = set(git.get_all_local_branches(repo))
    current = git.get_current_branch(repo)
    tree = StackTree.build(tracked, all_branches, current)
    ordered_nodes = _collect_stack_nodes(tree)

    result: list[StackDiffBranch] = []
    for node, depth in ordered_nodes:
        parent = tracked.get(node.name)
        if parent is None:
            continue

        commit_count = 0
        if parent in all_branches:
            branch_head = git.get_branch_head(repo, node.name)
            parent_head = git.get_branch_head(repo, parent)
            commit_count = len(git.get_commits_between(repo, branch_head, parent_head))
        latest_commit = git.get_branch_latest_commit(repo, node.name)

        result.append(
            StackDiffBranch(
                name=node.name,
                parent=parent,
                depth=depth,
                is_current=node.is_current,
                commit_count=commit_count,
                commit=latest_commit.sha,
                commit_short=latest_commit.short_sha,
                commit_subject=latest_commit.subject,
                commit_time=latest_commit.time,
            )
        )

    return result


def _working_diff_stats(patch: str) -> dict[str, int]:
    """Summarize a unified diff patch: changed files and added/deleted lines."""
    files = additions = deletions = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"files": files, "additions": additions, "deletions": deletions}


def _build_stack_payload(
    repo: Repo, working_patch: str | None = None
) -> dict[str, Any]:
    """Build payload for stack visualization endpoint."""
    branches = _get_stack_diff_branches(repo)
    if working_patch is None:
        try:
            working_patch = _git_working_diff(Path(repo.workdir))
        except ValueError:
            working_patch = None
    return {
        "currentBranch": git.get_current_branch(repo),
        "branches": [
            {
                "name": item.name,
                "parent": item.parent,
                "depth": item.depth,
                "isCurrent": item.is_current,
                "commitCount": item.commit_count,
                "commit": item.commit,
                "commitShort": item.commit_short,
                "commitSubject": item.commit_subject,
                "commitTime": item.commit_time,
            }
            for item in branches
        ],
        "workingStats": (
            None if working_patch is None else _working_diff_stats(working_patch)
        ),
    }


def _git_diff_patch(repo_path: Path, parent: str, branch: str) -> str:
    """Return git patch for branch compared to parent (PR-style triple-dot diff)."""
    try:
        return build_branch_patch(repo_path, parent, branch)
    except RecapError as exc:
        raise ValueError(str(exc)) from exc


def _build_diff_payload(repo: Repo, branch: str) -> dict[str, Any]:
    tracked = _tracked_branch_parents(repo)
    if branch not in tracked:
        raise ValueError(f"Branch '{branch}' is not tracked")

    parent = tracked[branch]
    all_branches = set(git.get_all_local_branches(repo))
    if parent not in all_branches:
        raise ValueError(f"Parent branch '{parent}' does not exist locally")

    patch = _git_diff_patch(Path(repo.workdir), parent, branch)
    return {
        "branch": branch,
        "parent": parent,
        "patch": patch,
    }


def _git_working_diff(repo_path: Path) -> str:
    """Return git diff for uncommitted changes."""
    try:
        return build_working_patch(repo_path)
    except RecapError as exc:
        raise ValueError(str(exc)) from exc


def _build_working_diff_payload(repo: Repo) -> dict[str, Any]:
    """Build payload for working tree diff endpoint."""
    patch = _git_working_diff(Path(repo.workdir))
    return {"patch": patch}


def _git_working_diff_key(repo_path: Path) -> str:
    """Return a stable fingerprint of the current working tree diff."""
    patch = _git_working_diff(repo_path)
    return hashlib.sha256(patch.encode()).hexdigest()


def _build_ui_state_payload(repo: Repo) -> dict[str, Any]:
    """Build lightweight polling payload for stack and working tree changes."""
    patch = _git_working_diff(Path(repo.workdir))
    payload = _build_stack_payload(repo, working_patch=patch)
    payload["workingDiffKey"] = hashlib.sha256(patch.encode()).hexdigest()
    return payload


def _build_suggestions_payload(
    repo: Repo, mode: str, source_branch: str | None = None
) -> dict[str, Any]:
    """Build payload for branch suggestion endpoint."""
    tracked = _tracked_branch_parents(repo)
    repo_path = Path(repo.workdir)

    # Get source patch
    if mode == "working":
        source_patch = _git_working_diff(repo_path)
        exclude_branch = None
    elif mode == "branch":
        if not source_branch:
            raise ValueError("Missing required parameter: source")
        if source_branch not in tracked:
            raise ValueError(f"Branch '{source_branch}' is not tracked")
        parent = tracked[source_branch]
        all_branches = set(git.get_all_local_branches(repo))
        if parent not in all_branches:
            raise ValueError(f"Parent branch '{parent}' does not exist locally")
        source_patch = _git_diff_patch(repo_path, parent, source_branch)
        exclude_branch = source_branch
    else:
        raise ValueError(f"Invalid mode: {mode}")

    # Get patches for all tracked branches (concurrently)
    branch_patches: dict[str, str] = {}
    all_branches = set(git.get_all_local_branches(repo))
    diffable = [
        (branch, parent) for branch, parent in tracked.items() if parent in all_branches
    ]

    def _diff_branch(args: tuple[str, str]) -> tuple[str, str | None]:
        branch, parent = args
        try:
            return branch, _git_diff_patch(repo_path, parent, branch)
        except ValueError:
            return branch, None

    with ThreadPoolExecutor(max_workers=min(8, len(diffable) or 1)) as pool:
        for branch, patch in pool.map(_diff_branch, diffable):
            if patch is not None:
                branch_patches[branch] = patch

    suggestions = _compute_suggestions(source_patch, branch_patches, exclude_branch)

    return {
        "suggestions": [
            {
                "file": s.file,
                "hunkIndex": s.hunk_index,
                "suggestedBranch": s.suggested_branch,
                "reason": s.reason,
            }
            for s in suggestions
        ],
    }


def _build_github_info_payload(repo: Repo, branch_names: list[str]) -> dict[str, Any]:
    """Build payload with PR + CI info for all tracked branches."""
    token = get_github_token()
    if not token:
        return {"branches": {}}

    repo_info = get_repo_info(repo)
    if not repo_info:
        return {"branches": {}}

    owner, repo_name = repo_info

    try:
        client = GitHubClient(token, owner, repo_name)
    except Exception:
        return {"branches": {}}

    def _fetch_info(branch: str) -> tuple[str, BranchGitHubInfo]:
        return branch, client.get_branch_github_info(branch)

    result: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(branch_names) or 1)) as pool:
        for branch, info in pool.map(_fetch_info, branch_names):
            result[branch] = {
                "prNumber": info.pr_number,
                "prUrl": info.pr_url,
                "prIsDraft": info.pr_is_draft,
                "prState": info.pr_state,
                "checkStatus": info.check_status,
            }

    client.client.close()
    return {"branches": result}


def _write_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, Any],
) -> None:
    try:
        body = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionError):  # pragma: no cover
        pass


def _write_bytes(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: bytes,
    *,
    content_type: str,
    cache_control: str = "no-store",
) -> None:
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionError):  # pragma: no cover
        pass


def _safe_static_path(static_dir: Path, request_path: str) -> Path | None:
    if request_path in ("", "/"):
        request_path = "/index.html"

    relative = unquote(request_path).lstrip("/")
    candidate = (static_dir / relative).resolve()
    try:
        candidate.relative_to(static_dir.resolve())
    except ValueError:
        return None

    if candidate.is_file():
        return candidate
    return None


def _serve_static_ui(
    handler: BaseHTTPRequestHandler,
    static_dir: Path,
    request_path: str,
) -> None:
    target = _safe_static_path(static_dir, request_path)
    if target is None and not request_path.startswith("/api/"):
        target = static_dir / "index.html"

    if target is None or not target.is_file():
        _write_json(handler, 404, {"error": "Not found"})
        return

    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    cache_control = (
        "public, max-age=31536000, immutable"
        if "/assets/" in target.as_posix()
        else "no-store"
    )
    try:
        body = target.read_bytes()
    except OSError as exc:
        _write_json(handler, 500, {"error": str(exc)})
        return

    _write_bytes(
        handler,
        200,
        body,
        content_type=content_type,
        cache_control=cache_control,
    )


def _build_request_handler(
    repo_path: Path,
    static_dir: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    def _open_repo() -> Repo:
        return git.open_repo(repo_path)

    class StackUIRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path == "/api/health":
                _write_json(self, 200, {"ok": True, "repoPath": str(repo_path)})
                return

            if parsed.path == "/api/stack":
                try:
                    repo = _open_repo()
                    _write_json(self, 200, _build_stack_payload(repo))
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/state":
                try:
                    repo = _open_repo()
                    _write_json(self, 200, _build_ui_state_payload(repo))
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/review-state":
                try:
                    repo = _open_repo()
                    _write_json(self, 200, _load_persisted_ui_state(repo))
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/github-info":
                try:
                    repo = _open_repo()
                    tracked = _tracked_branch_parents(repo)
                    branch_names = list(tracked.keys())
                    _write_json(
                        self, 200, _build_github_info_payload(repo, branch_names)
                    )
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/recaps":
                try:
                    repo = _open_repo()
                    _write_json(
                        self,
                        200,
                        {
                            "recaps": [
                                meta.model_dump(mode="json", by_alias=True)
                                for meta in list_recaps(repo)
                            ]
                        },
                    )
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path.startswith("/api/recaps/"):
                recap_id = unquote(parsed.path.removeprefix("/api/recaps/"))
                if not recap_id:
                    _write_json(self, 400, {"error": "Missing recap id"})
                    return

                try:
                    repo = _open_repo()
                    _write_json(
                        self,
                        200,
                        stored_recap_payload(load_recap(repo, recap_id)),
                    )
                except FileNotFoundError as exc:
                    _write_json(self, 404, {"error": str(exc)})
                except RecapError as exc:
                    _write_json(self, 400, {"error": str(exc)})
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/diff":
                branch = parse_qs(parsed.query).get("branch", [None])[0]
                if not branch:
                    _write_json(
                        self,
                        400,
                        {"error": "Missing required query parameter: branch"},
                    )
                    return

                try:
                    repo = _open_repo()
                    _write_json(self, 200, _build_diff_payload(repo, branch))
                except ValueError as exc:
                    _write_json(self, 400, {"error": str(exc)})
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/diff/working":
                try:
                    repo = _open_repo()
                    _write_json(self, 200, _build_working_diff_payload(repo))
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/suggestions":
                qs = parse_qs(parsed.query)
                mode = qs.get("mode", [None])[0]
                if not mode or mode not in ("working", "branch"):
                    _write_json(
                        self,
                        400,
                        {"error": "Missing or invalid query parameter: mode"},
                    )
                    return
                source = qs.get("source", [None])[0]
                try:
                    repo = _open_repo()
                    _write_json(
                        self,
                        200,
                        _build_suggestions_payload(repo, mode, source),
                    )
                except ValueError as exc:
                    _write_json(self, 400, {"error": str(exc)})
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/review/models":
                _write_json(self, 200, {"models": _get_available_models()})
                return

            if static_dir is not None:
                _serve_static_ui(self, static_dir, parsed.path)
                return

            _write_json(self, 404, {"error": "Not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path == "/api/review-state":
                content_length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    _write_json(self, 400, {"error": "Invalid JSON body"})
                    return

                try:
                    repo = _open_repo()
                    _write_json(self, 200, _update_persisted_ui_state(repo, body))
                except ValueError as exc:
                    _write_json(self, 400, {"error": str(exc)})
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/move-hunks":
                content_length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    _write_json(self, 400, {"error": "Invalid JSON body"})
                    return

                required = ["sourceBranch", "targetBranch", "hunks"]
                missing = [f for f in required if f not in body]
                if missing:
                    _write_json(
                        self,
                        400,
                        {"error": f"Missing required fields: {', '.join(missing)}"},
                    )
                    return

                raw_hunks = body["hunks"]
                if not isinstance(raw_hunks, list) or len(raw_hunks) == 0:
                    _write_json(self, 400, {"error": "hunks must be a non-empty array"})
                    return

                hunk_selections: list[HunkSelection] = []
                for h in raw_hunks:
                    if not isinstance(h, dict):
                        _write_json(self, 400, {"error": "Each hunk must be an object"})
                        return
                    hunk_required = ["filePath", "filePatch", "hunkIndex"]
                    hunk_missing = [f for f in hunk_required if f not in h]
                    if hunk_missing:
                        msg = f"Hunk missing fields: {', '.join(hunk_missing)}"
                        _write_json(self, 400, {"error": msg})
                        return
                    hunk_selections.append(
                        HunkSelection(
                            file_path=h["filePath"],
                            file_patch=h["filePatch"],
                            hunk_index=h["hunkIndex"],
                        )
                    )

                with _move_lock:
                    try:
                        repo = _open_repo()
                        result = _move_hunks(
                            repo,
                            source_branch=body["sourceBranch"],
                            target_branch=body["targetBranch"],
                            hunks=hunk_selections,
                            no_verify=True,
                        )
                        _write_json(
                            self,
                            200,
                            {
                                "sourceBranch": result.source_branch,
                                "targetBranch": result.target_branch,
                                "filePaths": result.file_paths,
                                "restackedBranches": result.restacked_branches,
                            },
                        )
                    except MoveError as exc:
                        _write_json(self, 400, {"error": str(exc)})
                    except Exception as exc:
                        _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/accept-working-hunks":
                content_length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    _write_json(self, 400, {"error": "Invalid JSON body"})
                    return

                required = ["targetBranch", "hunks"]
                missing = [f for f in required if f not in body]
                if missing:
                    _write_json(
                        self,
                        400,
                        {"error": f"Missing required fields: {', '.join(missing)}"},
                    )
                    return

                raw_hunks = body["hunks"]
                if not isinstance(raw_hunks, list) or len(raw_hunks) == 0:
                    _write_json(self, 400, {"error": "hunks must be a non-empty array"})
                    return

                hunk_selections: list[HunkSelection] = []
                for h in raw_hunks:
                    if not isinstance(h, dict):
                        _write_json(self, 400, {"error": "Each hunk must be an object"})
                        return
                    hunk_required = ["filePath", "filePatch", "hunkIndex"]
                    hunk_missing = [f for f in hunk_required if f not in h]
                    if hunk_missing:
                        msg = f"Hunk missing fields: {', '.join(hunk_missing)}"
                        _write_json(self, 400, {"error": msg})
                        return
                    hunk_selections.append(
                        HunkSelection(
                            file_path=h["filePath"],
                            file_patch=h["filePatch"],
                            hunk_index=h["hunkIndex"],
                        )
                    )

                with _move_lock:
                    try:
                        repo = _open_repo()
                        result = _accept_working_hunks(
                            repo,
                            target_branch=body["targetBranch"],
                            hunks=hunk_selections,
                        )
                        _write_json(
                            self,
                            200,
                            {
                                "targetBranch": result.target_branch,
                                "filePaths": result.file_paths,
                                "restackedBranches": result.restacked_branches,
                            },
                        )
                    except MoveError as exc:
                        _write_json(self, 400, {"error": str(exc)})
                    except Exception as exc:
                        _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/split-hunks":
                content_length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    _write_json(self, 400, {"error": "Invalid JSON body"})
                    return

                required = ["sourceBranch", "commitMessage", "placement", "hunks"]
                missing = [f for f in required if f not in body]
                if missing:
                    _write_json(
                        self,
                        400,
                        {"error": f"Missing required fields: {', '.join(missing)}"},
                    )
                    return

                placement = body["placement"]
                if placement not in ("before", "after"):
                    _write_json(
                        self,
                        400,
                        {"error": "placement must be 'before' or 'after'"},
                    )
                    return

                raw_hunks = body["hunks"]
                if not isinstance(raw_hunks, list) or len(raw_hunks) == 0:
                    _write_json(self, 400, {"error": "hunks must be a non-empty array"})
                    return

                hunk_selections: list[HunkSelection] = []
                for h in raw_hunks:
                    if not isinstance(h, dict):
                        _write_json(self, 400, {"error": "Each hunk must be an object"})
                        return
                    hunk_required = ["filePath", "filePatch", "hunkIndex"]
                    hunk_missing = [f for f in hunk_required if f not in h]
                    if hunk_missing:
                        msg = f"Hunk missing fields: {', '.join(hunk_missing)}"
                        _write_json(self, 400, {"error": msg})
                        return
                    hunk_selections.append(
                        HunkSelection(
                            file_path=h["filePath"],
                            file_patch=h["filePatch"],
                            hunk_index=h["hunkIndex"],
                        )
                    )

                with _move_lock:
                    try:
                        repo = _open_repo()
                        result = _split_hunks(
                            repo,
                            source_branch=body["sourceBranch"],
                            commit_message=body["commitMessage"],
                            placement=placement,
                            hunks=hunk_selections,
                            no_verify=True,
                        )
                        _write_json(
                            self,
                            200,
                            {
                                "sourceBranch": result.source_branch,
                                "newBranch": result.new_branch,
                                "placement": result.placement,
                                "filePaths": result.file_paths,
                                "restackedBranches": result.restacked_branches,
                            },
                        )
                    except MoveError as exc:
                        _write_json(self, 400, {"error": str(exc)})
                    except Exception as exc:
                        _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/split-lines":
                content_length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    _write_json(self, 400, {"error": "Invalid JSON body"})
                    return

                required = ["sourceBranch", "chunks"]
                missing = [f for f in required if f not in body]
                if missing:
                    _write_json(
                        self,
                        400,
                        {"error": f"Missing required fields: {', '.join(missing)}"},
                    )
                    return

                raw_chunks = body["chunks"]
                if not isinstance(raw_chunks, list) or len(raw_chunks) == 0:
                    _write_json(
                        self, 400, {"error": "chunks must be a non-empty array"}
                    )
                    return

                split_chunks: list[SplitChunk] = []
                for c in raw_chunks:
                    if not isinstance(c, dict):
                        _write_json(
                            self, 400, {"error": "Each chunk must be an object"}
                        )
                        return
                    if "commitMessage" not in c or "selections" not in c:
                        msg = "Each chunk must have 'commitMessage' and 'selections'"
                        _write_json(self, 400, {"error": msg})
                        return
                    raw_sels = c["selections"]
                    if not isinstance(raw_sels, list) or len(raw_sels) == 0:
                        _write_json(
                            self,
                            400,
                            {"error": "selections must be a non-empty array"},
                        )
                        return
                    selections: list[LineSelection] = []
                    for s in raw_sels:
                        if not isinstance(s, dict):
                            _write_json(
                                self,
                                400,
                                {"error": "Each selection must be an object"},
                            )
                            return
                        sel_required = [
                            "filePath",
                            "filePatch",
                            "startLine",
                            "endLine",
                            "side",
                        ]
                        sel_missing = [f for f in sel_required if f not in s]
                        if sel_missing:
                            msg = f"Selection missing fields: {', '.join(sel_missing)}"
                            _write_json(self, 400, {"error": msg})
                            return
                        selections.append(
                            LineSelection(
                                file_path=s["filePath"],
                                file_patch=s["filePatch"],
                                start_line=s["startLine"],
                                end_line=s["endLine"],
                                side=s["side"],
                            )
                        )
                    split_chunks.append(
                        SplitChunk(
                            commit_message=c["commitMessage"],
                            selections=selections,
                        )
                    )

                with _move_lock:
                    try:
                        repo = _open_repo()
                        result = _split_lines_batch(
                            repo,
                            source_branch=body["sourceBranch"],
                            chunks=split_chunks,
                            no_verify=True,
                        )
                        _write_json(
                            self,
                            200,
                            {
                                "sourceBranch": result.source_branch,
                                "newBranches": result.new_branches,
                                "restackedBranches": result.restacked_branches,
                            },
                        )
                    except MoveError as exc:
                        _write_json(self, 400, {"error": str(exc)})
                    except Exception as exc:
                        _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/review":
                content_length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    _write_json(self, 400, {"error": "Invalid JSON body"})
                    return

                branch = body.get("branch")
                models = body.get("models")
                if not branch or not models:
                    _write_json(
                        self,
                        400,
                        {"error": "Missing required fields: branch, models"},
                    )
                    return

                try:
                    repo = _open_repo()
                    if branch == "__working__":
                        patch = _git_working_diff(repo_path)
                    else:
                        tracked = _tracked_branch_parents(repo)
                        if branch not in tracked:
                            _write_json(
                                self,
                                400,
                                {"error": f"Branch '{branch}' is not tracked"},
                            )
                            return
                        parent = tracked[branch]
                        patch = _git_diff_patch(repo_path, parent, branch)
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                    return

                synthesize = body.get("synthesize")

                # SSE response
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()

                    from concurrent.futures import as_completed

                    def _write_result_event(
                        event_type: str,
                        result: ReviewResult,
                    ) -> None:
                        event_data = json.dumps(
                            {
                                "model": result.model,
                                "summary": result.summary,
                                "comments": [
                                    {
                                        "file": c.file,
                                        "start_line": c.start_line,
                                        "end_line": c.end_line,
                                        "side": c.side,
                                        "text": c.text,
                                        "severity": c.severity,
                                    }
                                    for c in result.comments
                                ],
                                "error": result.error,
                                "fix_prompt": result.fix_prompt,
                            }
                        )
                        self.wfile.write(
                            f"event: {event_type}\ndata: {event_data}\n\n".encode()
                        )
                        self.wfile.flush()

                    completed_reviews: list[ReviewResult] = []

                    with ThreadPoolExecutor(
                        max_workers=len(models),
                    ) as executor:
                        futures = {
                            executor.submit(_run_review, patch, m): m for m in models
                        }
                        for future in as_completed(futures):
                            try:
                                result = future.result()
                                completed_reviews.append(result)
                                _write_result_event("review", result)
                            except Exception:
                                pass

                    # Synthesis pass
                    if (
                        synthesize
                        and isinstance(synthesize, str)
                        and len(completed_reviews) > 0
                    ):
                        self.wfile.write(b"event: synthesis-start\ndata: {}\n\n")
                        self.wfile.flush()
                        try:
                            synth = _run_synthesis(
                                patch,
                                completed_reviews,
                                synthesize,
                            )
                            _write_result_event("synthesis", synth)
                        except Exception:
                            pass

                    self.wfile.write(b"event: done\ndata: {}\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionError):  # pragma: no cover
                    pass
                return

            _write_json(self, 404, {"error": "Not found"})

        def do_OPTIONS(self) -> None:
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    return StackUIRequestHandler


def _start_api_server(
    repo_path: Path,
    host: str,
    port: int,
    static_dir: Path | None = None,
) -> ThreadingHTTPServer:
    handler = _build_request_handler(repo_path, static_dir=static_dir)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_api_server_on_available_port(
    repo_path: Path,
    host: str,
    start_port: int,
    *,
    static_dir: Path | None = None,
    max_tries: int = 100,
) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for offset in range(max_tries):
        candidate = start_port + offset
        try:
            server = _start_api_server(
                repo_path,
                host,
                candidate,
                static_dir=static_dir,
            )
        except OSError as exc:
            last_error = exc
            continue
        return server, candidate

    message = f"Could not bind an available port on {host} starting at {start_port}"
    if last_error is not None:
        message = f"{message}: {last_error}"
    raise ValueError(message)


def _get_ui_session_path(repo: Repo) -> Path:
    return _get_shortcake_state_dir(repo) / UI_SESSION_FILE


def _get_ui_session_lock_path(repo: Repo) -> Path:
    return _get_shortcake_state_dir(repo) / "ui-session.lock"


def _ui_origin(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _ui_url(origin: str, route_hash: str = "") -> str:
    if not route_hash:
        return origin
    return f"{origin}/{route_hash}"


def _read_ui_session(repo: Repo) -> UISession | None:
    path = _get_ui_session_path(repo)
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None
    try:
        host = raw["host"]
        port = raw["port"]
        pid = raw["pid"]
        repo_path = raw["repoPath"]
        origin = raw["origin"]
        mode = raw.get("mode", "static")
    except KeyError:
        return None

    if (
        not isinstance(host, str)
        or not isinstance(port, int)
        or not isinstance(pid, int)
        or not isinstance(repo_path, str)
        or not isinstance(origin, str)
        or not isinstance(mode, str)
    ):
        return None

    return UISession(
        host=host,
        port=port,
        pid=pid,
        repo_path=repo_path,
        origin=origin,
        mode=mode,
    )


def _write_ui_session(repo: Repo, session: UISession) -> None:
    path = _get_ui_session_path(repo)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "host": session.host,
        "port": session.port,
        "pid": session.pid,
        "repoPath": session.repo_path,
        "origin": session.origin,
        "mode": session.mode,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp_path, path)


def _clear_ui_session(repo: Repo, session: UISession) -> None:
    path = _get_ui_session_path(repo)
    current = _read_ui_session(repo)
    if current != session:
        return
    with contextlib.suppress(OSError):
        path.unlink()


def _session_health_payload(session: UISession) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(
            f"{session.origin}/api/health",
            timeout=0.35,
        ) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode())
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None

    return payload if isinstance(payload, dict) else None


def _live_ui_session_unlocked(repo: Repo, host: str) -> UISession | None:
    session = _read_ui_session(repo)
    if session is None or session.host != host:
        return None

    payload = _session_health_payload(session)
    repo_path = str(Path(repo.workdir).resolve())
    if payload and payload.get("ok") is True and payload.get("repoPath") == repo_path:
        return session

    with contextlib.suppress(OSError):
        _get_ui_session_path(repo).unlink()
    return None


def _live_ui_session(repo: Repo, host: str) -> UISession | None:
    with _locked_file(_get_ui_session_lock_path(repo)):
        return _live_ui_session_unlocked(repo, host)


def _resolve_js_runtime() -> str | None:
    """Resolve js runtime. Prefer pybun when available."""
    if shutil.which("pybun"):
        return "pybun"
    if shutil.which("bun"):
        return "bun"
    return None


def _runtime_candidates(runtime: str) -> list[str]:
    if runtime == "pybun" and shutil.which("bun"):
        return ["pybun", "bun"]
    return [runtime]


def _resolve_frontend_dir(repo_path: Path) -> Path | None:
    """Find frontend source directory for Vite app."""
    explicit = os.environ.get("SHORTCAKE_UI_DIR")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    # Development workflow: use package-layout frontend as source of truth.
    candidates.append(repo_path / "src" / "shortcake" / "_web")

    # Installed package fallback: shortcake._web
    package_path: Path | None = None
    try:
        import shortcake._web as web_module

        package_path = Path(web_module.__file__).resolve().parent
    except Exception:
        package_path = None
    if package_path is not None:
        candidates.append(package_path)

    # Fallback for editable/local layouts.
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        candidates.append(parent / "src" / "shortcake" / "_web")

    for candidate in candidates:
        has_package = (candidate / "package.json").is_file()
        has_index = (candidate / "index.html").is_file()
        if has_package and has_index:
            return candidate
    return None


def _resolve_static_ui_dir(repo_path: Path) -> Path | None:
    explicit = os.environ.get("SHORTCAKE_UI_DIST_DIR")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    frontend_dir = _resolve_frontend_dir(repo_path)
    if frontend_dir is not None:
        candidates.append(frontend_dir / "dist")

    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _run_build(runtime: str, frontend_dir: Path) -> str:
    candidates = _runtime_candidates(runtime)
    for candidate in candidates:
        result = subprocess.run(
            [candidate, "run", "build"],
            cwd=frontend_dir,
            check=False,
        )
        if result.returncode == 0:
            return candidate

    joined = " or ".join(f"'{candidate} run build'" for candidate in candidates)
    raise ValueError(f"UI build failed with {joined}")


def _config_int(repo: Repo, key: str) -> int | None:
    try:
        value = repo.config[key]
    except (KeyError, TypeError):
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _resolve_ui_port(repo: Repo, requested_port: int | None) -> int:
    return (
        requested_port
        or _env_int("SHORTCAKE_UI_PORT")
        or _config_int(repo, "shortcake.uiPort")
        or DEFAULT_UI_PORT
    )


def _resolve_dev_web_port(repo: Repo, requested_port: int | None) -> int:
    return (
        requested_port
        or _env_int("SHORTCAKE_UI_DEV_PORT")
        or _config_int(repo, "shortcake.uiDevPort")
        or DEFAULT_DEV_WEB_PORT
    )


def _prepare_static_ui_dir(
    repo_path: Path,
    *,
    build_ui: bool,
    skip_install: bool,
) -> Path:
    frontend_dir = _resolve_frontend_dir(repo_path)
    static_dir = _resolve_static_ui_dir(repo_path)

    if build_ui or static_dir is None:
        if frontend_dir is None:
            raise ValueError("frontend directory not found")

        runtime = _resolve_js_runtime()
        if runtime is None:
            raise ValueError(
                "built UI assets were not found, and neither 'pybun' nor 'bun' "
                "was found in PATH to build them"
            )

        if not skip_install:
            runtime = _run_install(runtime, frontend_dir)
        _run_build(runtime, frontend_dir)
        static_dir = frontend_dir / "dist"

    if static_dir is None or not (static_dir / "index.html").is_file():
        raise ValueError("built UI assets not found")
    return static_dir


def _run_install(runtime: str, frontend_dir: Path) -> str:
    candidates = _runtime_candidates(runtime)
    for candidate in candidates:
        result = subprocess.run(
            [candidate, "install"],
            cwd=frontend_dir,
            check=False,
        )
        if result.returncode == 0:
            return candidate

    joined = " or ".join(f"'{candidate} install'" for candidate in candidates)
    raise ValueError(f"Dependency install failed with {joined}")


def _run_dev_server(
    runtime: str,
    frontend_dir: Path,
    host: str,
    port: int,
    api_origin: str,
    open_browser: bool,
) -> int:
    env = dict(os.environ)
    env["SHORTCAKE_API_ORIGIN"] = api_origin
    candidates = _runtime_candidates(runtime)

    last_return_code = 1
    for candidate in candidates:
        command = [
            candidate,
            "run",
            "dev",
            "--host",
            host,
            "--port",
            str(port),
            "--strictPort",
        ]

        if open_browser:
            open_browser = False  # Only open once across retries.
            threading.Timer(
                1.5,
                webbrowser.open,
                args=(f"http://{host}:{port}",),
            ).start()

        process = subprocess.run(
            command,
            cwd=frontend_dir,
            env=env,
            check=False,
        )
        last_return_code = process.returncode
        # 0 and 130 are considered normal exits (130 is Ctrl+C)
        if last_return_code in (0, 130):
            return last_return_code
        # Retry with fallback runtime only when command exits immediately with error.
        if candidate != candidates[-1]:
            continue

    return last_return_code


def _find_open_port(host: str, start_port: int, max_tries: int = 100) -> int:
    """Find an available port, starting at start_port."""
    for offset in range(max_tries):
        candidate = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise ValueError(
        f"Could not find an available port on {host} starting at {start_port}"
    )


def _wait_for_interrupt() -> None:  # pragma: no cover
    while True:
        time.sleep(3600)


def _shortcake_cli_command() -> list[str]:
    executable = shutil.which("shortcake") or shutil.which("sc")
    if executable:
        return [executable]
    return [sys.argv[0]]


def _start_static_ui_background(
    repo: Repo,
    *,
    host: str,
    port: int,
    build_ui: bool,
    skip_install: bool,
) -> UISession:
    repo_path = Path(repo.workdir).resolve()
    log_path = _get_shortcake_state_dir(repo) / "ui-background.log"
    command = [
        *_shortcake_cli_command(),
        "ui",
        "--host",
        host,
        "--ui-port",
        str(port),
        "--no-open-browser",
    ]
    if build_ui:
        command.append("--build-ui")
    if skip_install:
        command.append("--skip-install")

    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=repo_path,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )

    deadline = time.monotonic() + BACKGROUND_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        session = _live_ui_session(repo, host)
        if session is not None:
            return session
        if process.poll() is not None:
            raise ValueError(
                "background UI server exited before becoming healthy "
                f"(exit code {process.returncode}); see {log_path}"
            )
        time.sleep(0.15)

    raise ValueError(
        "background UI server did not become healthy within "
        f"{BACKGROUND_START_TIMEOUT_SECONDS:g}s; see {log_path}"
    )


def _open_or_start_static_ui(
    repo: Repo,
    *,
    host: str,
    port: int,
    route_hash: str = "",
    open_browser: bool = True,
    build_ui: bool = False,
    skip_install: bool = False,
    background: bool = False,
    label: str = "UI",
) -> None:
    repo_path = Path(repo.workdir).resolve()

    existing = _live_ui_session(repo, host)
    if existing is not None:
        url = _ui_url(existing.origin, route_hash)
        typer.echo(f"Using running Shortcake UI at {url}")
        if open_browser:
            webbrowser.open(url)
        return

    if background:
        try:
            session = _start_static_ui_background(
                repo,
                host=host,
                port=port,
                build_ui=build_ui,
                skip_install=skip_install,
            )
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc

        url = _ui_url(session.origin, route_hash)
        typer.echo(f"Shortcake {label} running in background at {url}")
        if open_browser:
            webbrowser.open(url)
        return

    try:
        static_dir = _prepare_static_ui_dir(
            repo_path,
            build_ui=build_ui,
            skip_install=skip_install,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    with _locked_file(_get_ui_session_lock_path(repo)):
        existing = _live_ui_session_unlocked(repo, host)
        if existing is not None:
            url = _ui_url(existing.origin, route_hash)
            typer.echo(f"Using running Shortcake UI at {url}")
            if open_browser:
                webbrowser.open(url)
            return

        try:
            server, selected_port = _start_api_server_on_available_port(
                repo_path,
                host,
                port,
                static_dir=static_dir,
            )
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc

        origin = _ui_origin(host, selected_port)
        session = UISession(
            host=host,
            port=selected_port,
            pid=os.getpid(),
            repo_path=str(repo_path),
            origin=origin,
            mode="static",
        )
        _write_ui_session(repo, session)

    url = _ui_url(origin, route_hash)
    if selected_port != port:
        typer.echo(f"Port {port} is in use, using {selected_port} for UI.")

    typer.echo(f"Shortcake {label} running at {url}")
    typer.echo("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)

    try:
        _wait_for_interrupt()
    except KeyboardInterrupt:
        return
    finally:
        server.shutdown()
        server.server_close()
        with _locked_file(_get_ui_session_lock_path(repo)):
            _clear_ui_session(repo, session)


def ui(
    host: Annotated[
        str,
        typer.Option(help="Host for API and Vite dev server."),
    ] = "127.0.0.1",
    ui_port: Annotated[
        int | None,
        typer.Option(
            "--ui-port",
            "--api-port",
            help=(
                "Port for the built Shortcake UI/API server. Defaults to "
                "SHORTCAKE_UI_PORT, git config shortcake.uiPort, or 8765."
            ),
        ),
    ] = None,
    web_port: Annotated[
        int | None,
        typer.Option(help="Port for Vite React dev server when --dev is used."),
    ] = None,
    skip_install: Annotated[
        bool,
        typer.Option(
            "--skip-install",
            help="Skip 'bun install'/'pybun install' before --dev or --build-ui.",
        ),
    ] = False,
    open_browser: Annotated[
        bool,
        typer.Option(
            "--open-browser/--no-open-browser",
            help="Open the UI in the default browser after startup.",
        ),
    ] = True,
    dev: Annotated[
        bool,
        typer.Option("--dev", help="Run the Vite dev server instead of built assets."),
    ] = False,
    build_ui: Annotated[
        bool,
        typer.Option("--build-ui", help="Build the static UI once before serving it."),
    ] = False,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            help="Start the built UI server in a detached background process.",
        ),
    ] = False,
) -> None:
    """Launch stack diff UI with a local API server."""
    repo = git.open_repo()
    repo_path = Path(repo.workdir)
    selected_ui_port = _resolve_ui_port(repo, ui_port)

    if not dev:
        _open_or_start_static_ui(
            repo,
            host=host,
            port=selected_ui_port,
            open_browser=open_browser,
            build_ui=build_ui,
            skip_install=skip_install,
            background=background,
        )
        return

    if background:
        typer.echo("Error: --background is only supported for the built UI.", err=True)
        raise typer.Exit(1)

    frontend_dir = _resolve_frontend_dir(repo_path)

    if frontend_dir is None:
        typer.echo(
            "Error: frontend directory not found. "
            "Expected a 'src/shortcake/_web' folder in the current repo, "
            "or set SHORTCAKE_UI_DIR.",
            err=True,
        )
        raise typer.Exit(1)

    runtime = _resolve_js_runtime()
    if runtime is None:
        typer.echo(
            "Error: Neither 'pybun' nor 'bun' was found in PATH. Install bun or pybun.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        server, selected_api_port = _start_api_server_on_available_port(
            repo_path.resolve(),
            host,
            selected_ui_port,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    selected_web_port = _find_open_port(host, _resolve_dev_web_port(repo, web_port))
    api_origin = f"http://{host}:{selected_api_port}"

    if selected_api_port != selected_ui_port:
        typer.echo(
            f"Port {selected_ui_port} is in use, using {selected_api_port} for API."
        )
    default_web_port = _resolve_dev_web_port(repo, web_port)
    if selected_web_port != default_web_port:
        typer.echo(
            f"Port {default_web_port} is in use, using {selected_web_port} for UI."
        )

    typer.echo(f"Stack API running at {api_origin}")
    typer.echo(f"Starting UI dev server at http://{host}:{selected_web_port}")
    typer.echo("Press Ctrl+C to stop.")

    try:
        if not skip_install:
            runtime = _run_install(runtime, frontend_dir)

        return_code = _run_dev_server(
            runtime,
            frontend_dir,
            host,
            selected_web_port,
            api_origin,
            open_browser,
        )
        if return_code not in (0, 130):
            typer.echo("Error: frontend dev server exited unexpectedly.", err=True)
            raise typer.Exit(1)
    except KeyboardInterrupt:
        return
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        server.shutdown()
        server.server_close()

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

import typer
from dulwich.repo import Repo

from shortcake import _git as git
from shortcake._tree import BranchNode, StackTree
from shortcake.commands.move_lines import (
    HunkSelection,
    MoveError,
    _accept_working_hunks,
    _move_lines,
    _move_lock,
)


@dataclass(frozen=True)
class StackDiffBranch:
    name: str
    parent: str
    depth: int
    is_current: bool
    commit_count: int


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

        result.append(
            StackDiffBranch(
                name=node.name,
                parent=parent,
                depth=depth,
                is_current=node.is_current,
                commit_count=commit_count,
            )
        )

    return result


def _build_stack_payload(repo: Repo) -> dict[str, Any]:
    """Build payload for stack visualization endpoint."""
    branches = _get_stack_diff_branches(repo)
    return {
        "currentBranch": git.get_current_branch(repo),
        "branches": [
            {
                "name": item.name,
                "parent": item.parent,
                "depth": item.depth,
                "isCurrent": item.is_current,
                "commitCount": item.commit_count,
            }
            for item in branches
        ],
    }


def _git_diff_patch(repo_path: Path, parent: str, branch: str) -> str:
    """Return git patch for branch compared to parent (PR-style triple-dot diff)."""
    result = subprocess.run(
        [
            "git",
            "diff",
            "--no-color",
            "--find-renames",
            "--full-index",
            f"{parent}...{branch}",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "Failed to build diff patch"
        raise ValueError(message)
    return result.stdout


def _build_diff_payload(repo: Repo, branch: str) -> dict[str, Any]:
    tracked = _tracked_branch_parents(repo)
    if branch not in tracked:
        raise ValueError(f"Branch '{branch}' is not tracked")

    parent = tracked[branch]
    all_branches = set(git.get_all_local_branches(repo))
    if parent not in all_branches:
        raise ValueError(f"Parent branch '{parent}' does not exist locally")

    patch = _git_diff_patch(Path(repo.path), parent, branch)
    return {
        "branch": branch,
        "parent": parent,
        "patch": patch,
    }


def _git_working_diff(repo_path: Path) -> str:
    """Return git diff for uncommitted changes (staged + unstaged vs HEAD)."""
    result = subprocess.run(
        ["git", "diff", "--no-color", "--find-renames", "--full-index", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "Failed to build working diff"
        raise ValueError(message)
    return result.stdout


def _build_working_diff_payload(repo: Repo) -> dict[str, Any]:
    """Build payload for working tree diff endpoint."""
    patch = _git_working_diff(Path(repo.path))
    return {"patch": patch}


def _write_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, Any],
) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_request_handler(repo: Repo) -> type[BaseHTTPRequestHandler]:
    class StackUIRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path == "/api/health":
                _write_json(self, 200, {"ok": True})
                return

            if parsed.path == "/api/stack":
                try:
                    _write_json(self, 200, _build_stack_payload(repo))
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
                    _write_json(self, 200, _build_diff_payload(repo, branch))
                except ValueError as exc:
                    _write_json(self, 400, {"error": str(exc)})
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            if parsed.path == "/api/diff/working":
                try:
                    _write_json(self, 200, _build_working_diff_payload(repo))
                except Exception as exc:
                    _write_json(self, 500, {"error": str(exc)})
                return

            _write_json(self, 404, {"error": "Not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path == "/api/move-lines":
                content_length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    _write_json(self, 400, {"error": "Invalid JSON body"})
                    return

                required = [
                    "sourceBranch",
                    "targetBranch",
                    "filePatch",
                    "filePath",
                    "startLine",
                    "endLine",
                    "side",
                ]
                missing = [f for f in required if f not in body]
                if missing:
                    _write_json(
                        self,
                        400,
                        {"error": f"Missing required fields: {', '.join(missing)}"},
                    )
                    return

                with _move_lock:
                    try:
                        result = _move_lines(
                            repo,
                            source_branch=body["sourceBranch"],
                            target_branch=body["targetBranch"],
                            file_patch=body["filePatch"],
                            file_path=body["filePath"],
                            start_line=body["startLine"],
                            end_line=body["endLine"],
                            side=body["side"],
                        )
                        _write_json(
                            self,
                            200,
                            {
                                "sourceBranch": result.source_branch,
                                "targetBranch": result.target_branch,
                                "filePath": result.file_path,
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
    repo: Repo,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    handler = _build_request_handler(repo)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


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


def ui(
    host: Annotated[
        str,
        typer.Option(help="Host for API and Vite dev server."),
    ] = "127.0.0.1",
    api_port: Annotated[
        int,
        typer.Option(help="Port for Shortcake stack-diff API."),
    ] = 8765,
    web_port: Annotated[
        int,
        typer.Option(help="Port for Vite React dev server."),
    ] = 5173,
    skip_install: Annotated[
        bool,
        typer.Option("--skip-install", help="Skip 'bun install'/'pybun install'."),
    ] = False,
    open_browser: Annotated[
        bool,
        typer.Option(
            "--open-browser/--no-open-browser",
            help="Open the UI in the default browser after startup.",
        ),
    ] = True,
) -> None:
    """Launch stack diff UI (React + Vite) with a local API server."""
    repo = git.open_repo()
    repo_path = Path(repo.path)
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

    selected_api_port = _find_open_port(host, api_port)
    selected_web_port = _find_open_port(host, web_port)
    api_origin = f"http://{host}:{selected_api_port}"
    server = _start_api_server(repo, host, selected_api_port)

    if selected_api_port != api_port:
        typer.echo(f"Port {api_port} is in use, using {selected_api_port} for API.")
    if selected_web_port != web_port:
        typer.echo(f"Port {web_port} is in use, using {selected_web_port} for UI.")

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
        repo.close()

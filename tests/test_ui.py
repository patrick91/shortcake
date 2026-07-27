import io
import json
import shutil
import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from shortcake import _git as git
from shortcake._github import BranchGitHubInfo
from shortcake.commands.ui import (
    UISession,
    _build_diff_payload,
    _build_github_info_payload,
    _build_request_handler,
    _build_stack_payload,
    _build_suggestions_payload,
    _build_ui_state_payload,
    _build_working_diff_payload,
    _clear_ui_session,
    _find_open_port,
    _git_diff_patch,
    _git_working_diff,
    _git_working_diff_key,
    _live_ui_session,
    _live_ui_session_unlocked,
    _load_persisted_ui_state,
    _normalize_persisted_ui_state,
    _open_or_start_static_ui,
    _prepare_static_ui_dir,
    _read_ui_session,
    _resolve_dev_web_port,
    _resolve_frontend_dir,
    _resolve_js_runtime,
    _resolve_static_ui_dir,
    _resolve_ui_port,
    _run_build,
    _run_dev_server,
    _run_install,
    _runtime_candidates,
    _safe_static_path,
    _save_persisted_ui_state,
    _session_health_payload,
    _shortcake_cli_command,
    _start_api_server,
    _start_api_server_on_available_port,
    _start_static_ui_background,
    _update_persisted_ui_state,
    _working_diff_stats,
    _write_json,
    _write_ui_session,
    ui,
)
from tests._git_helpers import Repo


def test_build_stack_payload_linear_stack(repo_with_stack: Repo) -> None:
    """Tracked branches are returned in stack order with metadata."""
    payload = _build_stack_payload(repo_with_stack)

    assert payload["currentBranch"] == "branch_b"
    assert [branch["name"] for branch in payload["branches"]] == [
        "branch_a",
        "branch_b",
    ]

    branch_a = payload["branches"][0]
    branch_b = payload["branches"][1]

    assert branch_a["parent"] == "main"
    assert branch_a["commitCount"] == 1
    assert (
        branch_a["commit"] == git.get_branch_head(repo_with_stack, "branch_a").decode()
    )
    assert branch_a["commitShort"] == branch_a["commit"][:7]
    assert branch_a["commitSubject"] == "feat: branch a"
    assert branch_a["isCurrent"] is False

    assert branch_b["parent"] == "branch_a"
    assert branch_b["commitCount"] == 1
    assert (
        branch_b["commit"] == git.get_branch_head(repo_with_stack, "branch_b").decode()
    )
    assert branch_b["commitShort"] == branch_b["commit"][:7]
    assert branch_b["commitSubject"] == "feat: branch b"
    assert branch_b["isCurrent"] is True
    assert branch_b["commitTime"] > 0

    # Clean working tree — stats are present but empty.
    assert payload["workingStats"] == {"files": 0, "additions": 0, "deletions": 0}


def test_build_stack_payload_no_tracked_branches(temp_repo: Repo) -> None:
    """Untracked repositories return an empty stack."""
    payload = _build_stack_payload(temp_repo)

    assert payload["branches"] == []


def test_build_stack_payload_counts_working_stats() -> None:
    """A provided working patch is summarized into file and line counts."""
    patch = (
        "diff --git a/one.py b/one.py\n"
        "--- a/one.py\n"
        "+++ b/one.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old line\n"
        "+new line\n"
        "+added line\n"
        "diff --git a/two.py b/two.py\n"
        "--- a/two.py\n"
        "+++ b/two.py\n"
        "@@ -1 +0,0 @@\n"
        "-removed\n"
    )
    assert _working_diff_stats(patch) == {
        "files": 2,
        "additions": 2,
        "deletions": 2,
    }


def test_build_stack_payload_working_stats_none_on_error(temp_repo: Repo) -> None:
    """Stack payload degrades to null stats when the working diff fails."""
    with patch(
        "shortcake.commands.ui._git_working_diff",
        side_effect=ValueError("boom"),
    ):
        payload = _build_stack_payload(temp_repo)

    assert payload["workingStats"] is None


def test_build_diff_payload_for_tracked_branch(
    repo_with_stack: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diff payload includes patch for the requested tracked branch."""
    monkeypatch.chdir(tmp_path)

    payload = _build_diff_payload(repo_with_stack, "branch_b")

    assert payload["branch"] == "branch_b"
    assert payload["parent"] == "branch_a"
    assert "diff --git a/b.txt b/b.txt" in payload["patch"]
    assert "+branch b content" in payload["patch"]


def test_build_diff_payload_untracked_branch_error(temp_repo: Repo) -> None:
    """Diff endpoint rejects untracked branches."""
    with pytest.raises(ValueError, match="not tracked"):
        _build_diff_payload(temp_repo, "main")


def test_build_diff_payload_missing_parent_error(
    repo_with_stack: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diff endpoint rejects when parent branch is missing locally."""
    # Delete the parent branch so it doesn't exist
    repo_with_stack.references.delete("refs/heads/main")

    with pytest.raises(ValueError, match="does not exist locally"):
        _build_diff_payload(repo_with_stack, "branch_a")


def test_resolve_js_runtime_prefer_pybun(monkeypatch: pytest.MonkeyPatch) -> None:
    """pybun should be preferred when both runtimes are present."""

    def fake_which(name: str) -> str | None:
        if name == "pybun":
            return "/usr/local/bin/pybun"
        if name == "bun":
            return "/usr/local/bin/bun"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    assert _resolve_js_runtime() == "pybun"


def test_resolve_js_runtime_fallback_and_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime resolution falls back to bun, then none."""
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/local/bin/bun" if name == "bun" else None,
    )
    assert _resolve_js_runtime() == "bun"

    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert _resolve_js_runtime() is None


def test_runtime_candidates_pybun_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """pybun runtime should try bun as fallback when available."""
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/local/bin/bun" if name == "bun" else None,
    )

    assert _runtime_candidates("pybun") == ["pybun", "bun"]
    assert _runtime_candidates("bun") == ["bun"]


def test_resolve_frontend_dir_prefers_repo_src_web(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repo-local src/shortcake/_web directory should be preferred."""
    repo_path = Path(temp_repo.workdir)
    src_web_dir = repo_path / "src" / "shortcake" / "_web"
    src_web_dir.mkdir(parents=True)
    (src_web_dir / "package.json").write_text("{}")
    (src_web_dir / "index.html").write_text("<!doctype html>")

    monkeypatch.delenv("SHORTCAKE_UI_DIR", raising=False)
    assert _resolve_frontend_dir(repo_path) == src_web_dir


def test_resolve_frontend_dir_from_packaged_fallback(temp_repo: Repo) -> None:
    """If repo has no web/, fall back to packaged shortcake._web assets."""
    repo_path = Path(temp_repo.workdir)
    frontend_dir = _resolve_frontend_dir(repo_path)
    assert frontend_dir is not None
    assert (frontend_dir / "package.json").is_file()
    assert (frontend_dir / "index.html").is_file()


def test_resolve_frontend_dir_explicit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHORTCAKE_UI_DIR env var overrides all other candidates."""
    ui_dir = tmp_path / "custom_ui"
    ui_dir.mkdir()
    (ui_dir / "package.json").write_text("{}")
    (ui_dir / "index.html").write_text("<!doctype html>")

    monkeypatch.setenv("SHORTCAKE_UI_DIR", str(ui_dir))
    assert _resolve_frontend_dir(tmp_path / "nonexistent") == ui_dir


def test_resolve_frontend_dir_returns_none_when_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returns None when no valid frontend directory exists anywhere."""
    monkeypatch.delenv("SHORTCAKE_UI_DIR", raising=False)
    import builtins

    real_import = builtins.__import__

    def fail_web_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "shortcake._web":
            raise ImportError("no web")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_web_import)
    # Patch the module-level __file__ so parent path fallback also yields nothing
    monkeypatch.setattr(
        "shortcake.commands.ui.__file__",
        str(tmp_path / "fake" / "commands" / "ui.py"),
    )

    result = _resolve_frontend_dir(tmp_path / "no" / "repo" / "here")
    assert result is None


def test_resolve_static_ui_dir_uses_frontend_dist(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Built UI assets are resolved from the frontend dist directory."""
    frontend_dir = tmp_path / "web"
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir(parents=True)
    (frontend_dir / "package.json").write_text("{}")
    (frontend_dir / "index.html").write_text("<!doctype html>")
    (dist_dir / "index.html").write_text("<!doctype html>")

    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend_dir
    )

    assert _resolve_static_ui_dir(Path(temp_repo.workdir)) == dist_dir


def test_resolve_static_ui_dir_prefers_explicit_dist(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!doctype html>")

    monkeypatch.setenv("SHORTCAKE_UI_DIST_DIR", str(dist_dir))
    monkeypatch.setattr("shortcake.commands.ui._resolve_frontend_dir", lambda _: None)

    assert _resolve_static_ui_dir(Path(temp_repo.workdir)) == dist_dir


def test_resolve_static_ui_dir_returns_none_without_assets(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    empty_dist = tmp_path / "empty-dist"
    empty_dist.mkdir()

    monkeypatch.setenv("SHORTCAKE_UI_DIST_DIR", str(empty_dist))
    monkeypatch.setattr("shortcake.commands.ui._resolve_frontend_dir", lambda _: None)

    assert _resolve_static_ui_dir(Path(temp_repo.workdir)) is None


def test_resolve_ui_port_prefers_explicit_env_then_git_config(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHORTCAKE_UI_PORT", raising=False)
    temp_repo.config["shortcake.uiPort"] = "9001"

    assert _resolve_ui_port(temp_repo, 9100) == 9100
    assert _resolve_ui_port(temp_repo, None) == 9001

    monkeypatch.setenv("SHORTCAKE_UI_PORT", "9002")
    assert _resolve_ui_port(temp_repo, None) == 9002


def test_resolve_ports_ignore_invalid_env_and_config(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHORTCAKE_UI_PORT", "not-a-port")
    monkeypatch.setenv("SHORTCAKE_UI_DEV_PORT", "-1")
    temp_repo.config["shortcake.uiPort"] = "also-bad"
    temp_repo.config["shortcake.uiDevPort"] = "0"

    assert _resolve_ui_port(temp_repo, None) == 8765
    assert _resolve_dev_web_port(temp_repo, None) == 6173


def test_find_open_port_uses_requested_port_when_available() -> None:
    """When the starting port is free, it should be returned as-is."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, free_port = sock.getsockname()

    assert _find_open_port(host, free_port, max_tries=1) == free_port


def test_find_open_port_falls_forward_when_taken() -> None:
    """When the starting port is occupied, search should advance."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, occupied_port = sock.getsockname()
        sock.listen(1)

        selected = _find_open_port(host, occupied_port, max_tries=20)

    assert selected != occupied_port
    assert selected > occupied_port


def test_find_open_port_exhausted_raises() -> None:
    """Raises ValueError when no port is available within max_tries."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, occupied_port = sock.getsockname()
        sock.listen(1)

        with pytest.raises(ValueError, match="Could not find an available port"):
            _find_open_port(host, occupied_port, max_tries=1)


# --- _write_json ---


def test_write_json() -> None:
    """_write_json writes correct HTTP response."""
    handler = MagicMock()
    handler.wfile = io.BytesIO()

    _write_json(handler, 200, {"ok": True})

    handler.send_response.assert_called_once_with(200)
    handler.send_header.assert_any_call("Content-Type", "application/json")
    handler.send_header.assert_any_call("Cache-Control", "no-store")
    handler.send_header.assert_any_call("Access-Control-Allow-Origin", "*")
    handler.end_headers.assert_called_once()

    body = handler.wfile.getvalue()
    assert json.loads(body) == {"ok": True}


# --- _git_diff_patch / _git_working_diff ---


def test_git_diff_patch(repo_with_stack: Repo) -> None:
    """_git_diff_patch returns patch text for a branch diff."""
    repo_path = Path(repo_with_stack.workdir)
    patch = _git_diff_patch(repo_path, "branch_a", "branch_b")
    assert "diff --git" in patch
    assert "b.txt" in patch


def test_git_diff_patch_error(temp_repo: Repo) -> None:
    """_git_diff_patch raises ValueError on git failure."""
    with pytest.raises(ValueError):
        _git_diff_patch(Path(temp_repo.workdir), "nonexistent", "also-nonexistent")


def test_git_working_diff(repo_with_stack: Repo, tmp_path: Path) -> None:
    """_git_working_diff returns diff for uncommitted changes."""
    repo_path = Path(repo_with_stack.workdir)
    # Create an uncommitted change
    (tmp_path / "uncommitted.txt").write_text("new content")
    subprocess.run(["git", "add", "uncommitted.txt"], cwd=tmp_path, check=True)

    patch = _git_working_diff(repo_path)
    assert "uncommitted.txt" in patch


def test_git_working_diff_no_changes(repo_with_stack: Repo) -> None:
    """_git_working_diff returns empty string when there are no changes."""
    repo_path = Path(repo_with_stack.workdir)
    patch = _git_working_diff(repo_path)
    assert patch == ""


def test_git_working_diff_error(tmp_path: Path) -> None:
    """_git_working_diff raises ValueError on git failure (non-repo dir)."""
    with pytest.raises(ValueError):
        _git_working_diff(tmp_path)


def test_git_working_diff_skips_blank_and_unreadable_untracked_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable untracked files are ignored when building working diffs."""
    calls = 0

    def fake_run(cmd: list[str], **kw: object) -> MagicMock:
        nonlocal calls
        calls += 1
        if calls == 1:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="\nmissing.txt\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_working_diff(tmp_path) == ""


def test_build_working_diff_payload(repo_with_stack: Repo) -> None:
    """_build_working_diff_payload returns patch in expected format."""
    payload = _build_working_diff_payload(repo_with_stack)
    assert "patch" in payload
    assert isinstance(payload["patch"], str)


def test_git_working_diff_key_changes_when_content_changes(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    """Working diff key changes for content edits without stack commit changes."""
    repo_path = Path(repo_with_stack.workdir)
    before = _git_working_diff_key(repo_path)

    (tmp_path / "live-edit.txt").write_text("first edit\n")
    first = _git_working_diff_key(repo_path)
    assert first != before

    (tmp_path / "live-edit.txt").write_text("second edit\n")
    second = _git_working_diff_key(repo_path)
    assert second != first


def test_build_ui_state_payload_includes_stack_and_working_key(
    repo_with_stack: Repo,
) -> None:
    """Polling state includes both stack data and working diff fingerprint."""
    payload = _build_ui_state_payload(repo_with_stack)

    assert payload["currentBranch"] == "branch_b"
    assert len(payload["branches"]) == 2
    assert isinstance(payload["workingDiffKey"], str)
    assert len(payload["workingDiffKey"]) == 64


def test_persisted_ui_state_defaults(repo_with_stack: Repo) -> None:
    """Persistent UI review state defaults to unified with no viewed files."""
    payload = _load_persisted_ui_state(repo_with_stack)

    assert payload == {
        "version": 1,
        "diffStyle": "unified",
        "viewedFiles": {},
    }


def test_update_persisted_ui_state_writes_git_shortcake_file(
    repo_with_stack: Repo,
) -> None:
    """Persistent UI review state is stored under .git/shortcake."""
    payload = _update_persisted_ui_state(
        repo_with_stack,
        {
            "diffStyle": "split",
            "viewedScope": "branch:branch_b",
            "viewedFiles": {"b.txt": "patch-key"},
        },
    )

    assert payload["diffStyle"] == "split"
    assert payload["viewedFiles"] == {"branch:branch_b": {"b.txt": "patch-key"}}

    state_path = Path(repo_with_stack.path) / "shortcake" / "ui-state.json"
    assert state_path.exists()
    assert _load_persisted_ui_state(repo_with_stack) == payload


def test_update_persisted_ui_state_removes_empty_viewed_scope(
    repo_with_stack: Repo,
) -> None:
    """Posting an empty viewed scope prunes stale viewed entries."""
    _update_persisted_ui_state(
        repo_with_stack,
        {"viewedScope": "branch:branch_b", "viewedFiles": {"b.txt": "old"}},
    )

    payload = _update_persisted_ui_state(
        repo_with_stack,
        {"viewedScope": "branch:branch_b", "viewedFiles": {}},
    )

    assert payload["viewedFiles"] == {}


def test_persisted_ui_state_invalid_json_returns_defaults(
    repo_with_stack: Repo,
) -> None:
    """Invalid persisted UI state is ignored."""
    state_dir = Path(repo_with_stack.path) / "shortcake"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "ui-state.json").write_text("not json")

    assert _load_persisted_ui_state(repo_with_stack) == {
        "version": 1,
        "diffStyle": "unified",
        "viewedFiles": {},
    }


def test_persisted_ui_state_normalizes_invalid_fields() -> None:
    """Malformed persisted fields are ignored rather than echoed back."""
    assert _normalize_persisted_ui_state(None) == {
        "version": 1,
        "diffStyle": "unified",
        "viewedFiles": {},
    }

    payload = _normalize_persisted_ui_state(
        {
            "version": 1,
            "diffStyle": "sideways",
            "viewedFiles": {
                123: {"ignored.py": "ignored"},
                "bad": ["not", "a", "mapping"],
                "branch:branch_b": {
                    "b.txt": "patch-key",
                    "ignored.txt": None,
                    456: "ignored",
                },
            },
        }
    )

    assert payload == {
        "version": 1,
        "diffStyle": "unified",
        "viewedFiles": {"branch:branch_b": {"b.txt": "patch-key"}},
    }


def test_save_persisted_ui_state_ignores_write_errors(
    repo_with_stack: Repo,
) -> None:
    """Persistence failures should not interrupt the UI server."""
    with patch("builtins.open", side_effect=OSError("read-only")):
        _save_persisted_ui_state(
            repo_with_stack,
            {
                "version": 1,
                "diffStyle": "split",
                "viewedFiles": {"branch:branch_b": {"b.txt": "patch-key"}},
            },
        )


def test_update_persisted_ui_state_rejects_bad_diff_style(
    repo_with_stack: Repo,
) -> None:
    """Only supported diff layout values are persisted."""
    with pytest.raises(ValueError, match="diffStyle"):
        _update_persisted_ui_state(repo_with_stack, {"diffStyle": "sideways"})


def test_update_persisted_ui_state_rejects_bad_body(
    repo_with_stack: Repo,
) -> None:
    """Review state updates must be JSON objects with complete viewed fields."""
    with pytest.raises(ValueError, match="object"):
        _update_persisted_ui_state(repo_with_stack, ["not", "an", "object"])

    with pytest.raises(ValueError, match="viewedScope"):
        _update_persisted_ui_state(
            repo_with_stack,
            {"viewedScope": "branch:branch_b"},
        )


# --- HTTP request handler ---


class FakeHandler:
    """Minimal stand-in for BaseHTTPRequestHandler for testing do_GET."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._status: int | None = None
        self._headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, code: int) -> None:
        self._status = code

    def send_header(self, key: str, value: str) -> None:
        self._headers.append((key, value))

    def end_headers(self) -> None:
        pass

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def response_json(self) -> dict:
        return json.loads(self.wfile.getvalue())


def _make_handler(repo: Repo, path: str) -> FakeHandler:
    """Create a handler class from repo and invoke do_GET with the given path."""
    handler_cls = _build_request_handler(Path(repo.workdir))
    fake = FakeHandler(path)
    # Bind the do_GET method to our fake handler
    handler_cls.do_GET(fake)  # type: ignore[arg-type]
    return fake


def test_handler_health(temp_repo: Repo) -> None:
    fake = _make_handler(temp_repo, "/api/health")
    assert fake._status == 200
    payload = fake.response_json()
    assert payload["ok"] is True
    assert payload["repoPath"] == str(Path(temp_repo.workdir))


def test_handler_serves_static_index_and_assets(
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    static_dir = tmp_path / "dist"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><div id='root'></div>")
    (assets_dir / "app.js").write_text("console.log('shortcake')")

    handler_cls = _build_request_handler(Path(temp_repo.workdir), static_dir=static_dir)

    index = FakeHandler("/")
    handler_cls.do_GET(index)  # type: ignore[arg-type]
    assert index._status == 200
    assert ("Content-Type", "text/html") in index._headers
    assert b"root" in index.wfile.getvalue()

    fallback = FakeHandler("/missing")
    handler_cls.do_GET(fallback)  # type: ignore[arg-type]
    assert fallback._status == 200
    assert b"root" in fallback.wfile.getvalue()

    asset = FakeHandler("/assets/app.js")
    handler_cls.do_GET(asset)  # type: ignore[arg-type]
    assert asset._status == 200
    assert b"shortcake" in asset.wfile.getvalue()


def test_safe_static_path_rejects_path_traversal(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")

    assert _safe_static_path(static_dir, "/../secret.txt") is None


def test_handler_static_errors(
    temp_repo: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html>")
    handler_cls = _build_request_handler(Path(temp_repo.workdir), static_dir=static_dir)

    missing_api = FakeHandler("/api/nope")
    handler_cls.do_GET(missing_api)  # type: ignore[arg-type]
    assert missing_api._status == 404

    def fail_read_bytes(self: Path) -> bytes:
        raise OSError("cannot read")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    read_error = FakeHandler("/")
    handler_cls.do_GET(read_error)  # type: ignore[arg-type]
    assert read_error._status == 500
    assert "cannot read" in read_error.response_json()["error"]


def test_handler_stack(repo_with_stack: Repo) -> None:
    fake = _make_handler(repo_with_stack, "/api/stack")
    assert fake._status == 200
    data = fake.response_json()
    assert "branches" in data
    assert len(data["branches"]) == 2


def test_handler_stack_error(temp_repo: Repo) -> None:
    """Stack endpoint returns 500 on unexpected error."""
    with patch(
        "shortcake.commands.ui._build_stack_payload",
        side_effect=RuntimeError("boom"),
    ):
        fake = _make_handler(temp_repo, "/api/stack")
    assert fake._status == 500
    assert "boom" in fake.response_json()["error"]


def test_handler_diff_missing_branch_param(temp_repo: Repo) -> None:
    fake = _make_handler(temp_repo, "/api/diff")
    assert fake._status == 400
    assert "Missing" in fake.response_json()["error"]


def test_handler_diff_success(repo_with_stack: Repo) -> None:
    fake = _make_handler(repo_with_stack, "/api/diff?branch=branch_b")
    assert fake._status == 200
    data = fake.response_json()
    assert data["branch"] == "branch_b"


def test_handler_diff_value_error(temp_repo: Repo) -> None:
    """Diff endpoint returns 400 for ValueError (untracked branch)."""
    fake = _make_handler(temp_repo, "/api/diff?branch=main")
    assert fake._status == 400
    assert "not tracked" in fake.response_json()["error"]


def test_handler_diff_unexpected_error(repo_with_stack: Repo) -> None:
    """Diff endpoint returns 500 for unexpected exceptions."""
    with patch(
        "shortcake.commands.ui._build_diff_payload",
        side_effect=RuntimeError("unexpected"),
    ):
        fake = _make_handler(repo_with_stack, "/api/diff?branch=branch_b")
    assert fake._status == 500
    assert "unexpected" in fake.response_json()["error"]


def test_handler_working_diff(repo_with_stack: Repo) -> None:
    fake = _make_handler(repo_with_stack, "/api/diff/working")
    assert fake._status == 200
    assert "patch" in fake.response_json()


def test_handler_ui_state(repo_with_stack: Repo) -> None:
    fake = _make_handler(repo_with_stack, "/api/state")
    assert fake._status == 200
    data = fake.response_json()
    assert "branches" in data
    assert "workingDiffKey" in data


def test_handler_ui_state_error(temp_repo: Repo) -> None:
    """State endpoint returns 500 on unexpected errors."""
    with patch(
        "shortcake.commands.ui._build_ui_state_payload",
        side_effect=RuntimeError("state failed"),
    ):
        fake = _make_handler(temp_repo, "/api/state")
    assert fake._status == 500
    assert "state failed" in fake.response_json()["error"]


def test_handler_review_state_get(repo_with_stack: Repo) -> None:
    fake = _make_handler(repo_with_stack, "/api/review-state")
    assert fake._status == 200
    data = fake.response_json()
    assert data["diffStyle"] == "unified"
    assert data["viewedFiles"] == {}


def test_handler_review_state_get_error(temp_repo: Repo) -> None:
    """Review state GET returns 500 on unexpected errors."""
    with patch(
        "shortcake.commands.ui._load_persisted_ui_state",
        side_effect=RuntimeError("review state failed"),
    ):
        fake = _make_handler(temp_repo, "/api/review-state")
    assert fake._status == 500
    assert "review state failed" in fake.response_json()["error"]


def test_handler_working_diff_error(temp_repo: Repo) -> None:
    """Working diff endpoint returns 500 on error."""
    with patch(
        "shortcake.commands.ui._build_working_diff_payload",
        side_effect=RuntimeError("fail"),
    ):
        fake = _make_handler(temp_repo, "/api/diff/working")
    assert fake._status == 500
    assert "fail" in fake.response_json()["error"]


def test_handler_404(temp_repo: Repo) -> None:
    fake = _make_handler(temp_repo, "/api/nonexistent")
    assert fake._status == 404
    assert "Not found" in fake.response_json()["error"]


def test_handler_log_message_suppressed(temp_repo: Repo) -> None:
    """log_message is overridden to suppress output."""
    handler_cls = _build_request_handler(Path(temp_repo.workdir))
    fake = FakeHandler("/api/health")
    # Calling log_message should not raise
    handler_cls.log_message(fake, "test %s", "arg")  # type: ignore[arg-type]


# --- do_POST handler tests ---


def _make_post_handler(
    repo: Repo, path: str, body: dict | str | None = None
) -> FakeHandler:
    """Create a handler class and invoke do_POST with given path+body."""
    handler_cls = _build_request_handler(Path(repo.workdir))
    fake = FakeHandler(path)
    if body is not None:
        raw = json.dumps(body) if isinstance(body, dict) else body
        fake.rfile = io.BytesIO(raw.encode())
        fake.headers = {"Content-Length": str(len(raw.encode()))}
    else:
        fake.rfile = io.BytesIO(b"")
        fake.headers = {"Content-Length": "0"}
    handler_cls.do_POST(fake)  # type: ignore[arg-type]
    return fake


def test_post_review_state_updates_layout_and_viewed_files(
    repo_with_stack: Repo,
) -> None:
    fake = _make_post_handler(
        repo_with_stack,
        "/api/review-state",
        {
            "diffStyle": "split",
            "viewedScope": "branch:branch_b",
            "viewedFiles": {"b.txt": "patch-key"},
        },
    )

    assert fake._status == 200
    data = fake.response_json()
    assert data["diffStyle"] == "split"
    assert data["viewedFiles"] == {"branch:branch_b": {"b.txt": "patch-key"}}


def test_post_review_state_invalid_json(temp_repo: Repo) -> None:
    fake = _make_post_handler(temp_repo, "/api/review-state", "not json{{")
    assert fake._status == 400
    assert "Invalid JSON" in fake.response_json()["error"]


def test_post_review_state_invalid_diff_style(temp_repo: Repo) -> None:
    fake = _make_post_handler(
        temp_repo,
        "/api/review-state",
        {"diffStyle": "sideways"},
    )
    assert fake._status == 400
    assert "diffStyle" in fake.response_json()["error"]


def test_post_review_state_unexpected_error(temp_repo: Repo) -> None:
    """Review state POST returns 500 on unexpected write errors."""
    with patch(
        "shortcake.commands.ui._update_persisted_ui_state",
        side_effect=RuntimeError("write failed"),
    ):
        fake = _make_post_handler(
            temp_repo,
            "/api/review-state",
            {"diffStyle": "split"},
        )
    assert fake._status == 500
    assert "write failed" in fake.response_json()["error"]


def test_post_move_hunks_invalid_json(temp_repo: Repo) -> None:
    """POST /api/move-hunks with invalid JSON returns 400."""
    handler_cls = _build_request_handler(Path(temp_repo.workdir))
    fake = FakeHandler("/api/move-hunks")
    fake.rfile = io.BytesIO(b"not json")
    fake.headers = {"Content-Length": "8"}
    handler_cls.do_POST(fake)  # type: ignore[arg-type]
    assert fake._status == 400
    assert "Invalid JSON" in fake.response_json()["error"]


def test_post_move_hunks_missing_fields(temp_repo: Repo) -> None:
    """POST /api/move-hunks with missing fields returns 400."""
    fake = _make_post_handler(temp_repo, "/api/move-hunks", {"sourceBranch": "a"})
    assert fake._status == 400
    assert "Missing required fields" in fake.response_json()["error"]


def test_post_move_hunks_empty_hunks(temp_repo: Repo) -> None:
    """POST /api/move-hunks with empty hunks array returns 400."""
    body = {"sourceBranch": "a", "targetBranch": "b", "hunks": []}
    fake = _make_post_handler(temp_repo, "/api/move-hunks", body)
    assert fake._status == 400
    assert "non-empty" in fake.response_json()["error"]


def test_post_move_hunks_non_dict_hunk(temp_repo: Repo) -> None:
    """POST /api/move-hunks with non-dict hunk element returns 400."""
    body = {"sourceBranch": "a", "targetBranch": "b", "hunks": [42]}
    fake = _make_post_handler(temp_repo, "/api/move-hunks", body)
    assert fake._status == 400
    assert "Each hunk must be an object" in fake.response_json()["error"]


def test_post_move_hunks_invalid_hunk(temp_repo: Repo) -> None:
    """POST /api/move-hunks with invalid hunk object returns 400."""
    body = {"sourceBranch": "a", "targetBranch": "b", "hunks": [{"filePath": "f.py"}]}
    fake = _make_post_handler(temp_repo, "/api/move-hunks", body)
    assert fake._status == 400
    assert "Hunk missing fields" in fake.response_json()["error"]


def test_post_move_hunks_success(repo_with_stack: Repo, tmp_path: Path) -> None:
    """POST /api/move-hunks success returns 200 with result."""
    mock_result = MagicMock()
    mock_result.source_branch = "branch_a"
    mock_result.target_branch = "branch_b"
    mock_result.file_paths = ["test.txt"]
    mock_result.restacked_branches = []

    body = {
        "sourceBranch": "branch_a",
        "targetBranch": "branch_b",
        "hunks": [
            {"filePath": "test.txt", "filePatch": "patch", "hunkIndex": 0},
        ],
    }
    with patch("shortcake.commands.ui._move_hunks", return_value=mock_result):
        fake = _make_post_handler(repo_with_stack, "/api/move-hunks", body)
    assert fake._status == 200
    data = fake.response_json()
    assert data["sourceBranch"] == "branch_a"
    assert data["targetBranch"] == "branch_b"
    assert data["filePaths"] == ["test.txt"]


def test_post_move_hunks_move_error(repo_with_stack: Repo) -> None:
    """POST /api/move-hunks MoveError returns 400."""
    from shortcake.commands.move_lines import MoveError

    body = {
        "sourceBranch": "branch_a",
        "targetBranch": "branch_b",
        "hunks": [
            {"filePath": "test.txt", "filePatch": "patch", "hunkIndex": 0},
        ],
    }
    with patch(
        "shortcake.commands.ui._move_hunks",
        side_effect=MoveError("move failed"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/move-hunks", body)
    assert fake._status == 400
    assert "move failed" in fake.response_json()["error"]


def test_post_move_hunks_unexpected_error(repo_with_stack: Repo) -> None:
    """POST /api/move-hunks unexpected error returns 500."""
    body = {
        "sourceBranch": "branch_a",
        "targetBranch": "branch_b",
        "hunks": [
            {"filePath": "test.txt", "filePatch": "patch", "hunkIndex": 0},
        ],
    }
    with patch(
        "shortcake.commands.ui._move_hunks",
        side_effect=RuntimeError("boom"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/move-hunks", body)
    assert fake._status == 500
    assert "boom" in fake.response_json()["error"]


def test_post_accept_hunks_invalid_json(temp_repo: Repo) -> None:
    """POST /api/accept-working-hunks with invalid JSON returns 400."""
    handler_cls = _build_request_handler(Path(temp_repo.workdir))
    fake = FakeHandler("/api/accept-working-hunks")
    fake.rfile = io.BytesIO(b"bad json")
    fake.headers = {"Content-Length": "8"}
    handler_cls.do_POST(fake)  # type: ignore[arg-type]
    assert fake._status == 400
    assert "Invalid JSON" in fake.response_json()["error"]


def test_post_accept_hunks_missing_fields(temp_repo: Repo) -> None:
    """POST /api/accept-working-hunks missing fields returns 400."""
    fake = _make_post_handler(
        temp_repo, "/api/accept-working-hunks", {"targetBranch": "a"}
    )
    assert fake._status == 400
    assert "Missing required fields" in fake.response_json()["error"]


def test_post_accept_hunks_empty_hunks(temp_repo: Repo) -> None:
    """POST /api/accept-working-hunks with empty hunks returns 400."""
    fake = _make_post_handler(
        temp_repo,
        "/api/accept-working-hunks",
        {"targetBranch": "a", "hunks": []},
    )
    assert fake._status == 400
    assert "non-empty" in fake.response_json()["error"]


def test_post_accept_hunks_invalid_hunk_type(temp_repo: Repo) -> None:
    """POST /api/accept-working-hunks with non-object hunk returns 400."""
    fake = _make_post_handler(
        temp_repo,
        "/api/accept-working-hunks",
        {"targetBranch": "a", "hunks": ["not an object"]},
    )
    assert fake._status == 400
    assert "must be an object" in fake.response_json()["error"]


def test_post_accept_hunks_missing_hunk_fields(temp_repo: Repo) -> None:
    """POST /api/accept-working-hunks hunk missing fields returns 400."""
    fake = _make_post_handler(
        temp_repo,
        "/api/accept-working-hunks",
        {"targetBranch": "a", "hunks": [{"filePath": "x"}]},
    )
    assert fake._status == 400
    assert "Hunk missing fields" in fake.response_json()["error"]


def test_post_accept_hunks_success(repo_with_stack: Repo) -> None:
    """POST /api/accept-working-hunks success returns 200."""
    mock_result = MagicMock()
    mock_result.target_branch = "branch_a"
    mock_result.file_paths = ["test.txt"]
    mock_result.restacked_branches = []

    body = {
        "targetBranch": "branch_a",
        "hunks": [
            {
                "filePath": "test.txt",
                "filePatch": "patch",
                "hunkIndex": 0,
            }
        ],
    }
    with patch(
        "shortcake.commands.ui._accept_working_hunks",
        return_value=mock_result,
    ):
        fake = _make_post_handler(repo_with_stack, "/api/accept-working-hunks", body)
    assert fake._status == 200
    data = fake.response_json()
    assert data["targetBranch"] == "branch_a"
    assert data["filePaths"] == ["test.txt"]


def test_post_accept_hunks_move_error(repo_with_stack: Repo) -> None:
    """POST /api/accept-working-hunks MoveError returns 400."""
    from shortcake.commands.move_lines import MoveError

    body = {
        "targetBranch": "branch_a",
        "hunks": [
            {
                "filePath": "test.txt",
                "filePatch": "patch",
                "hunkIndex": 0,
            }
        ],
    }
    with patch(
        "shortcake.commands.ui._accept_working_hunks",
        side_effect=MoveError("accept failed"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/accept-working-hunks", body)
    assert fake._status == 400
    assert "accept failed" in fake.response_json()["error"]


def test_post_accept_hunks_unexpected_error(
    repo_with_stack: Repo,
) -> None:
    """POST /api/accept-working-hunks unexpected error returns 500."""
    body = {
        "targetBranch": "branch_a",
        "hunks": [
            {
                "filePath": "test.txt",
                "filePatch": "patch",
                "hunkIndex": 0,
            }
        ],
    }
    with patch(
        "shortcake.commands.ui._accept_working_hunks",
        side_effect=RuntimeError("boom"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/accept-working-hunks", body)
    assert fake._status == 500
    assert "boom" in fake.response_json()["error"]


def test_post_404(temp_repo: Repo) -> None:
    """POST to unknown endpoint returns 404."""
    fake = _make_post_handler(temp_repo, "/api/unknown", {"foo": "bar"})
    assert fake._status == 404
    assert "Not found" in fake.response_json()["error"]


# --- do_OPTIONS handler tests ---


def test_options_handler(temp_repo: Repo) -> None:
    """OPTIONS returns 200 with CORS headers."""
    handler_cls = _build_request_handler(Path(temp_repo.workdir))
    fake = FakeHandler("/api/move-hunks")
    handler_cls.do_OPTIONS(fake)  # type: ignore[arg-type]
    assert fake._status == 200
    header_dict = dict(fake._headers)
    assert header_dict["Access-Control-Allow-Origin"] == "*"
    assert "POST" in header_dict["Access-Control-Allow-Methods"]
    assert header_dict["Content-Length"] == "0"


# --- _start_api_server ---


def test_start_api_server(temp_repo: Repo) -> None:
    """_start_api_server starts a server that responds to health checks."""
    import urllib.request

    server = _start_api_server(Path(temp_repo.workdir), "127.0.0.1", 0)
    try:
        port = server.server_address[1]
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health")
        data = json.loads(resp.read())
        assert data["ok"] is True
        assert data["repoPath"] == str(Path(temp_repo.workdir))
    finally:
        server.shutdown()
        server.server_close()


def test_start_api_server_on_available_port_skips_taken_port(
    temp_repo: Repo,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        host, occupied_port = sock.getsockname()
        sock.listen(1)

        server, port = _start_api_server_on_available_port(
            Path(temp_repo.workdir),
            host,
            occupied_port,
            max_tries=20,
        )

    try:
        assert port != occupied_port
        assert port > occupied_port
    finally:
        server.shutdown()
        server.server_close()


def test_start_api_server_on_available_port_raises_after_exhaustion(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("in use")),
    )

    with pytest.raises(ValueError, match="in use"):
        _start_api_server_on_available_port(
            Path(temp_repo.workdir),
            "127.0.0.1",
            8765,
            max_tries=1,
        )


def test_run_build_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0))

    assert _run_build("bun", tmp_path) == "bun"

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: MagicMock(returncode=1))
    with pytest.raises(ValueError, match="UI build failed"):
        _run_build("bun", tmp_path)


def test_prepare_static_ui_dir_builds_when_dist_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend_dir = tmp_path / "web"
    dist_dir = frontend_dir / "dist"
    frontend_dir.mkdir()
    (frontend_dir / "package.json").write_text("{}")
    (frontend_dir / "index.html").write_text("<!doctype html>")

    def fake_run_build(runtime: str, frontend_dir_arg: Path) -> str:
        assert runtime == "bun"
        assert frontend_dir_arg == frontend_dir
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<!doctype html>")
        return runtime

    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend_dir
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_static_ui_dir", lambda _: None)
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr("shortcake.commands.ui._run_build", fake_run_build)

    assert (
        _prepare_static_ui_dir(tmp_path, build_ui=False, skip_install=True) == dist_dir
    )


def test_prepare_static_ui_dir_runs_install_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend_dir = tmp_path / "web"
    dist_dir = frontend_dir / "dist"
    frontend_dir.mkdir()
    (frontend_dir / "package.json").write_text("{}")
    (frontend_dir / "index.html").write_text("<!doctype html>")
    calls: list[str] = []

    def fake_run_install(runtime: str, frontend_dir_arg: Path) -> str:
        calls.append(f"install:{runtime}:{frontend_dir_arg.name}")
        return "bun"

    def fake_run_build(runtime: str, frontend_dir_arg: Path) -> str:
        calls.append(f"build:{runtime}:{frontend_dir_arg.name}")
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<!doctype html>")
        return runtime

    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend_dir
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_static_ui_dir", lambda _: None)
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "pybun")
    monkeypatch.setattr("shortcake.commands.ui._run_install", fake_run_install)
    monkeypatch.setattr("shortcake.commands.ui._run_build", fake_run_build)

    assert (
        _prepare_static_ui_dir(tmp_path, build_ui=False, skip_install=False) == dist_dir
    )
    assert calls == ["install:pybun:web", "build:bun:web"]


def test_prepare_static_ui_dir_errors_without_frontend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shortcake.commands.ui._resolve_frontend_dir", lambda _: None)
    monkeypatch.setattr("shortcake.commands.ui._resolve_static_ui_dir", lambda _: None)

    with pytest.raises(ValueError, match="frontend directory not found"):
        _prepare_static_ui_dir(tmp_path, build_ui=False, skip_install=True)


def test_prepare_static_ui_dir_errors_without_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend_dir = tmp_path / "web"
    frontend_dir.mkdir()
    (frontend_dir / "package.json").write_text("{}")
    (frontend_dir / "index.html").write_text("<!doctype html>")

    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend_dir
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_static_ui_dir", lambda _: None)
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: None)

    with pytest.raises(ValueError, match="neither 'pybun' nor 'bun'"):
        _prepare_static_ui_dir(tmp_path, build_ui=False, skip_install=True)


def test_prepare_static_ui_dir_errors_when_build_does_not_create_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend_dir = tmp_path / "web"
    frontend_dir.mkdir()
    (frontend_dir / "package.json").write_text("{}")
    (frontend_dir / "index.html").write_text("<!doctype html>")

    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend_dir
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_static_ui_dir", lambda _: None)
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr("shortcake.commands.ui._run_build", lambda *args: "bun")

    with pytest.raises(ValueError, match="built UI assets not found"):
        _prepare_static_ui_dir(tmp_path, build_ui=False, skip_install=True)


# --- _run_install ---


def test_run_install_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_install returns the successful runtime."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=0),
    )
    result = _run_install("bun", tmp_path)
    assert result == "bun"


def test_run_install_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_install tries fallback runtimes."""
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/bin/bun" if name == "bun" else None,
    )

    call_count = 0

    def fake_run(cmd: list[str], **kw: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        # First candidate (pybun) fails, second (bun) succeeds
        return MagicMock(returncode=1 if call_count == 1 else 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_install("pybun", tmp_path)
    assert result == "bun"


def test_run_install_all_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_install raises ValueError when all candidates fail."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=1),
    )

    with pytest.raises(ValueError, match="Dependency install failed"):
        _run_install("bun", tmp_path)


# --- _run_dev_server ---


def test_run_dev_server_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_dev_server returns 0 on successful exit."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=0),
    )
    result = _run_dev_server(
        "bun", tmp_path, "127.0.0.1", 5173, "http://localhost:8765", False
    )
    assert result == 0


def test_run_dev_server_ctrl_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_dev_server returns 130 (Ctrl+C) as normal exit."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=130),
    )
    result = _run_dev_server(
        "bun", tmp_path, "127.0.0.1", 5173, "http://localhost:8765", False
    )
    assert result == 130


def test_run_dev_server_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_dev_server retries with fallback runtime."""
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/bin/bun" if name == "bun" else None,
    )

    call_count = 0

    def fake_run(cmd: list[str], **kw: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return MagicMock(returncode=1 if call_count == 1 else 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _run_dev_server(
        "pybun", tmp_path, "127.0.0.1", 5173, "http://localhost:8765", False
    )
    assert result == 0


def test_run_dev_server_all_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_dev_server returns last error code when all runtimes fail."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=42),
    )
    result = _run_dev_server(
        "bun", tmp_path, "127.0.0.1", 5173, "http://localhost:8765", False
    )
    assert result == 42


def test_run_dev_server_open_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_dev_server opens browser when requested."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=0),
    )
    timer_calls: list[tuple] = []

    def fake_timer(interval: float, fn: object, args: tuple = ()) -> MagicMock:
        timer_calls.append((interval, fn, args))
        mock = MagicMock()
        return mock

    monkeypatch.setattr("threading.Timer", fake_timer)
    _run_dev_server("bun", tmp_path, "127.0.0.1", 5173, "http://localhost:8765", True)
    assert len(timer_calls) == 1
    assert "5173" in timer_calls[0][2][0]


def test_ui_session_file_roundtrip_and_invalid_payloads(temp_repo: Repo) -> None:
    assert _read_ui_session(temp_repo) is None

    session_path = Path(temp_repo.path) / "shortcake" / "ui-session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{bad json")
    assert _read_ui_session(temp_repo) is None

    session_path.write_text("[]")
    assert _read_ui_session(temp_repo) is None

    session_path.write_text(json.dumps({"host": "127.0.0.1"}))
    assert _read_ui_session(temp_repo) is None

    session_path.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": "8765",
                "pid": 123,
                "repoPath": str(Path(temp_repo.workdir)),
                "origin": "http://127.0.0.1:8765",
                "mode": "static",
            }
        )
    )
    assert _read_ui_session(temp_repo) is None

    session = UISession(
        host="127.0.0.1",
        port=8765,
        pid=123,
        repo_path=str(Path(temp_repo.workdir)),
        origin="http://127.0.0.1:8765",
        mode="static",
    )
    _write_ui_session(temp_repo, session)
    assert _read_ui_session(temp_repo) == session

    _clear_ui_session(
        temp_repo,
        UISession(
            host="127.0.0.1",
            port=8766,
            pid=123,
            repo_path=str(Path(temp_repo.workdir)),
            origin="http://127.0.0.1:8766",
            mode="static",
        ),
    )
    assert _read_ui_session(temp_repo) == session

    _clear_ui_session(temp_repo, session)
    assert _read_ui_session(temp_repo) is None


def test_session_health_payload_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = UISession(
        host="127.0.0.1",
        port=8765,
        pid=123,
        repo_path="/repo",
        origin="http://127.0.0.1:8765",
        mode="static",
    )

    class FakeResponse:
        def __init__(self, status: int, body: str) -> None:
            self.status = status
            self.body = body

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.body.encode()

    monkeypatch.setattr(
        "shortcake.commands.ui.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(200, '{"ok": true}'),
    )
    assert _session_health_payload(session) == {"ok": True}

    monkeypatch.setattr(
        "shortcake.commands.ui.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(500, '{"ok": false}'),
    )
    assert _session_health_payload(session) is None

    monkeypatch.setattr(
        "shortcake.commands.ui.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(200, "[]"),
    )
    assert _session_health_payload(session) is None

    monkeypatch.setattr(
        "shortcake.commands.ui.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("down")),
    )
    assert _session_health_payload(session) is None


def test_live_ui_session_unlocked_returns_only_healthy_matching_repo(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = str(Path(temp_repo.workdir).resolve())
    session = UISession(
        host="127.0.0.1",
        port=8765,
        pid=123,
        repo_path=repo_path,
        origin="http://127.0.0.1:8765",
        mode="static",
    )
    _write_ui_session(temp_repo, session)

    monkeypatch.setattr(
        "shortcake.commands.ui._session_health_payload",
        lambda _: {"ok": True, "repoPath": repo_path},
    )
    assert _live_ui_session_unlocked(temp_repo, "127.0.0.1") == session

    monkeypatch.setattr(
        "shortcake.commands.ui._session_health_payload",
        lambda _: {"ok": True, "repoPath": "/other"},
    )
    assert _live_ui_session_unlocked(temp_repo, "127.0.0.1") is None
    assert _read_ui_session(temp_repo) is None

    _write_ui_session(temp_repo, session)
    assert _live_ui_session_unlocked(temp_repo, "0.0.0.0") is None


def test_live_ui_session_uses_lock(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = UISession(
        host="127.0.0.1",
        port=8765,
        pid=123,
        repo_path=str(Path(temp_repo.workdir)),
        origin="http://127.0.0.1:8765",
        mode="static",
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._live_ui_session_unlocked",
        lambda repo, host: session,
    )

    assert _live_ui_session(temp_repo, "127.0.0.1") == session


def test_shortcake_cli_command_prefers_shortcake_then_sc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/bin/shortcake" if name == "shortcake" else None,
    )
    assert _shortcake_cli_command() == ["/bin/shortcake"]

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/bin/sc" if name == "sc" else None,
    )
    assert _shortcake_cli_command() == ["/bin/sc"]

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr("shortcake.commands.ui.sys.argv", ["python"])
    assert _shortcake_cli_command() == ["python"]


def test_start_static_ui_background_returns_healthy_session(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = UISession(
        host="127.0.0.1",
        port=8766,
        pid=456,
        repo_path=str(Path(temp_repo.workdir)),
        origin="http://127.0.0.1:8766",
        mode="static",
    )
    popen_calls: list[list[str]] = []

    class FakeProcess:
        returncode = None

        def poll(self) -> None:
            return None

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        popen_calls.append(command)
        return FakeProcess()

    monkeypatch.setattr("shortcake.commands.ui._shortcake_cli_command", lambda: ["sc"])
    monkeypatch.setattr("shortcake.commands.ui.subprocess.Popen", fake_popen)
    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *args: session)

    assert (
        _start_static_ui_background(
            temp_repo,
            host="127.0.0.1",
            port=8765,
            build_ui=True,
            skip_install=True,
        )
        == session
    )
    assert popen_calls[0][-2:] == ["--build-ui", "--skip-install"]


def test_start_static_ui_background_reports_early_exit(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        returncode = 7

        def poll(self) -> int:
            return 7

    monkeypatch.setattr("shortcake.commands.ui._shortcake_cli_command", lambda: ["sc"])
    monkeypatch.setattr(
        "shortcake.commands.ui.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *args: None)

    with pytest.raises(ValueError, match="exited before becoming healthy"):
        _start_static_ui_background(
            temp_repo,
            host="127.0.0.1",
            port=8765,
            build_ui=False,
            skip_install=False,
        )


def test_start_static_ui_background_reports_timeout(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        returncode = None

        def poll(self) -> None:
            return None

    monkeypatch.setattr("shortcake.commands.ui._shortcake_cli_command", lambda: ["sc"])
    monkeypatch.setattr(
        "shortcake.commands.ui.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *args: None)
    monkeypatch.setattr("shortcake.commands.ui.BACKGROUND_START_TIMEOUT_SECONDS", 0)

    with pytest.raises(ValueError, match="did not become healthy"):
        _start_static_ui_background(
            temp_repo,
            host="127.0.0.1",
            port=8765,
            build_ui=False,
            skip_install=False,
        )


def test_start_static_ui_background_sleeps_while_waiting(
    temp_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        returncode = None

        def poll(self) -> None:
            return None

    monotonic_values = iter([0.0, 0.1, 2.0])
    sleeps: list[float] = []

    monkeypatch.setattr("shortcake.commands.ui._shortcake_cli_command", lambda: ["sc"])
    monkeypatch.setattr(
        "shortcake.commands.ui.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *args: None)
    monkeypatch.setattr("shortcake.commands.ui.BACKGROUND_START_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(
        "shortcake.commands.ui.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "shortcake.commands.ui.time.sleep",
        lambda value: sleeps.append(value),
    )

    with pytest.raises(ValueError, match="did not become healthy"):
        _start_static_ui_background(
            temp_repo,
            host="127.0.0.1",
            port=8765,
            build_ui=False,
            skip_install=False,
        )

    assert sleeps == [0.15]


def test_open_or_start_static_ui_reuses_live_session(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
) -> None:
    session = UISession(
        host="127.0.0.1",
        port=8765,
        pid=123,
        repo_path=str(Path(temp_repo.workdir)),
        origin="http://127.0.0.1:8765",
        mode="static",
    )
    opened: list[str] = []

    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: session)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    _open_or_start_static_ui(
        temp_repo,
        host="127.0.0.1",
        port=8765,
        route_hash="#/recap/example",
    )

    assert opened == ["http://127.0.0.1:8765/#/recap/example"]


def test_open_or_start_static_ui_reuses_session_found_inside_lock(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
) -> None:
    session = UISession(
        host="127.0.0.1",
        port=8765,
        pid=123,
        repo_path=str(Path(temp_repo.workdir)),
        origin="http://127.0.0.1:8765",
        mode="static",
    )
    opened: list[str] = []

    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: None)
    monkeypatch.setattr(
        "shortcake.commands.ui._prepare_static_ui_dir",
        lambda *args, **kwargs: Path(temp_repo.workdir),
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._live_ui_session_unlocked",
        lambda *args: session,
    )
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    _open_or_start_static_ui(
        temp_repo,
        host="127.0.0.1",
        port=8765,
        route_hash="#/recap/example",
    )

    assert opened == ["http://127.0.0.1:8765/#/recap/example"]


def test_open_or_start_static_ui_starts_static_server(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html>")
    mock_server = MagicMock()

    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: None)
    monkeypatch.setattr(
        "shortcake.commands.ui._live_ui_session_unlocked", lambda *a: None
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._prepare_static_ui_dir", lambda *a, **kw: static_dir
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8766),
    )
    monkeypatch.setattr("shortcake.commands.ui._wait_for_interrupt", lambda: None)

    _open_or_start_static_ui(
        temp_repo,
        host="127.0.0.1",
        port=8765,
        open_browser=False,
    )

    mock_server.shutdown.assert_called_once()
    mock_server.server_close.assert_called_once()


def test_open_or_start_static_ui_opens_foreground_url(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html>")
    mock_server = MagicMock()
    opened: list[str] = []

    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: None)
    monkeypatch.setattr(
        "shortcake.commands.ui._live_ui_session_unlocked", lambda *a: None
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._prepare_static_ui_dir", lambda *a, **kw: static_dir
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8765),
    )
    monkeypatch.setattr("shortcake.commands.ui._wait_for_interrupt", lambda: None)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    _open_or_start_static_ui(
        temp_repo,
        host="127.0.0.1",
        port=8765,
        route_hash="#/recap/example",
    )

    assert opened == ["http://127.0.0.1:8765/#/recap/example"]


def test_open_or_start_static_ui_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html>")
    mock_server = MagicMock()

    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: None)
    monkeypatch.setattr(
        "shortcake.commands.ui._live_ui_session_unlocked", lambda *a: None
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._prepare_static_ui_dir", lambda *a, **kw: static_dir
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8765),
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._wait_for_interrupt",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    _open_or_start_static_ui(
        temp_repo,
        host="127.0.0.1",
        port=8765,
        open_browser=False,
    )

    mock_server.shutdown.assert_called_once()
    mock_server.server_close.assert_called_once()


def test_open_or_start_static_ui_background_starts_detached_server(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
) -> None:
    session = UISession(
        host="127.0.0.1",
        port=8766,
        pid=456,
        repo_path=str(Path(temp_repo.workdir)),
        origin="http://127.0.0.1:8766",
        mode="static",
    )
    opened: list[str] = []
    started: list[dict[str, object]] = []

    def fake_start_background(*args: object, **kwargs: object) -> UISession:
        started.append(kwargs)
        return session

    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: None)
    monkeypatch.setattr(
        "shortcake.commands.ui._start_static_ui_background",
        fake_start_background,
    )
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    _open_or_start_static_ui(
        temp_repo,
        host="127.0.0.1",
        port=8765,
        route_hash="#/recap/example",
        background=True,
        label="recap",
    )

    assert started == [
        {
            "host": "127.0.0.1",
            "port": 8765,
            "build_ui": False,
            "skip_install": False,
        }
    ]
    assert opened == ["http://127.0.0.1:8766/#/recap/example"]


def test_open_or_start_static_ui_reports_background_start_errors(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
) -> None:
    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: None)
    monkeypatch.setattr(
        "shortcake.commands.ui._start_static_ui_background",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("no server")),
    )

    with pytest.raises(typer.Exit):
        _open_or_start_static_ui(
            temp_repo,
            host="127.0.0.1",
            port=8765,
            background=True,
        )


def test_open_or_start_static_ui_reports_prepare_and_bind_errors(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("shortcake.commands.ui._live_ui_session", lambda *a: None)
    monkeypatch.setattr(
        "shortcake.commands.ui._prepare_static_ui_dir",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("no assets")),
    )

    with pytest.raises(typer.Exit):
        _open_or_start_static_ui(
            temp_repo,
            host="127.0.0.1",
            port=8765,
            open_browser=False,
        )

    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(
        "shortcake.commands.ui._prepare_static_ui_dir",
        lambda *args, **kwargs: static_dir,
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._live_ui_session_unlocked",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("port busy")),
    )

    with pytest.raises(typer.Exit):
        _open_or_start_static_ui(
            temp_repo,
            host="127.0.0.1",
            port=8765,
            open_browser=False,
        )


# --- ui() command ---


def test_ui_no_frontend_dir(
    monkeypatch: pytest.MonkeyPatch, temp_repo: Repo, tmp_path: Path
) -> None:
    """ui() exits with error when frontend directory is not found."""
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr("shortcake.commands.ui._resolve_frontend_dir", lambda _: None)

    from typer import Exit

    with pytest.raises(Exit):
        ui(dev=True)


def test_ui_no_runtime(
    monkeypatch: pytest.MonkeyPatch, temp_repo: Repo, tmp_path: Path
) -> None:
    """ui() exits with error when no JS runtime is found."""
    frontend = tmp_path / "web"
    frontend.mkdir()
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: None)

    from typer import Exit

    with pytest.raises(Exit):
        ui(dev=True)


def test_ui_static_mode_delegates_to_static_helper(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr("shortcake.commands.ui._resolve_ui_port", lambda *args: 9000)
    monkeypatch.setattr(
        "shortcake.commands.ui._open_or_start_static_ui",
        lambda *args, **kwargs: calls.append(kwargs),
    )

    ui(open_browser=False, build_ui=True, background=True)

    assert calls == [
        {
            "host": "127.0.0.1",
            "port": 9000,
            "open_browser": False,
            "build_ui": True,
            "skip_install": False,
            "background": True,
        }
    ]


def test_ui_dev_mode_rejects_background(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
) -> None:
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr("shortcake.commands.ui._resolve_ui_port", lambda *args: 9000)

    with pytest.raises(typer.Exit):
        ui(dev=True, background=True)


def test_ui_dev_mode_reports_api_bind_error(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr("shortcake.commands.ui._resolve_ui_port", lambda *args: 9000)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: tmp_path
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bind failed")),
    )

    with pytest.raises(typer.Exit):
        ui(dev=True)


def test_ui_success(
    monkeypatch: pytest.MonkeyPatch, temp_repo: Repo, tmp_path: Path
) -> None:
    """ui() starts server and dev server, then shuts down cleanly."""
    frontend = tmp_path / "web"
    frontend.mkdir()

    mock_server = MagicMock()
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8765),
    )
    monkeypatch.setattr("shortcake.commands.ui._run_install", lambda *a: "bun")
    monkeypatch.setattr("shortcake.commands.ui._run_dev_server", lambda *a: 0)

    ui(dev=True)

    mock_server.shutdown.assert_called_once()
    mock_server.server_close.assert_called_once()


def test_ui_dev_server_error(
    monkeypatch: pytest.MonkeyPatch, temp_repo: Repo, tmp_path: Path
) -> None:
    """ui() exits with error when dev server exits unexpectedly."""
    frontend = tmp_path / "web"
    frontend.mkdir()

    mock_server = MagicMock()
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8765),
    )
    monkeypatch.setattr("shortcake.commands.ui._run_install", lambda *a: "bun")
    monkeypatch.setattr("shortcake.commands.ui._run_dev_server", lambda *a: 1)

    from typer import Exit

    with pytest.raises(Exit):
        ui(dev=True)

    mock_server.shutdown.assert_called_once()


def test_ui_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, temp_repo: Repo, tmp_path: Path
) -> None:
    """ui() handles KeyboardInterrupt gracefully."""
    frontend = tmp_path / "web"
    frontend.mkdir()

    mock_server = MagicMock()
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8765),
    )

    def raise_interrupt(*a: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("shortcake.commands.ui._run_install", raise_interrupt)

    ui(dev=True)

    mock_server.shutdown.assert_called_once()


def test_ui_value_error(
    monkeypatch: pytest.MonkeyPatch, temp_repo: Repo, tmp_path: Path
) -> None:
    """ui() handles ValueError from install/dev server."""
    frontend = tmp_path / "web"
    frontend.mkdir()

    mock_server = MagicMock()
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8765),
    )

    def raise_value_error(*a: object) -> None:
        raise ValueError("install failed")

    monkeypatch.setattr("shortcake.commands.ui._run_install", raise_value_error)

    from typer import Exit

    with pytest.raises(Exit):
        ui(dev=True)

    mock_server.shutdown.assert_called_once()


def test_ui_skip_install(
    monkeypatch: pytest.MonkeyPatch, temp_repo: Repo, tmp_path: Path
) -> None:
    """ui() skips install when --skip-install is passed."""
    frontend = tmp_path / "web"
    frontend.mkdir()

    install_called = False

    def track_install(*a: object) -> str:
        nonlocal install_called
        install_called = True
        return "bun"

    mock_server = MagicMock()
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8765),
    )
    monkeypatch.setattr("shortcake.commands.ui._run_install", track_install)
    monkeypatch.setattr("shortcake.commands.ui._run_dev_server", lambda *a: 0)

    ui(skip_install=True, dev=True)

    assert not install_called


def test_ui_port_fallback_messages(
    monkeypatch: pytest.MonkeyPatch,
    temp_repo: Repo,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ui() prints messages when ports fall forward."""
    frontend = tmp_path / "web"
    frontend.mkdir()

    call_count = 0

    def fake_find_port(host: str, port: int, max_tries: int = 100) -> int:
        nonlocal call_count
        call_count += 1
        return port + 1  # Always shift forward

    mock_server = MagicMock()
    monkeypatch.setattr("shortcake.commands.ui.git.open_repo", lambda: temp_repo)
    monkeypatch.setattr(
        "shortcake.commands.ui._resolve_frontend_dir", lambda _: frontend
    )
    monkeypatch.setattr("shortcake.commands.ui._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr("shortcake.commands.ui._find_open_port", fake_find_port)
    monkeypatch.setattr(
        "shortcake.commands.ui._start_api_server_on_available_port",
        lambda *a, **kw: (mock_server, 8766),
    )
    monkeypatch.setattr("shortcake.commands.ui._run_install", lambda *a: "bun")
    monkeypatch.setattr("shortcake.commands.ui._run_dev_server", lambda *a: 0)

    ui(dev=True)

    captured = capsys.readouterr()
    assert "is in use" in captured.out


# --- GET /api/suggestions handler tests ---


def test_handler_suggestions_missing_mode(temp_repo: Repo) -> None:
    """GET /api/suggestions without mode returns 400."""
    fake = _make_handler(temp_repo, "/api/suggestions")
    assert fake._status == 400
    assert "mode" in fake.response_json()["error"]


def test_handler_suggestions_invalid_mode(temp_repo: Repo) -> None:
    """GET /api/suggestions with invalid mode returns 400."""
    fake = _make_handler(temp_repo, "/api/suggestions?mode=invalid")
    assert fake._status == 400
    assert "mode" in fake.response_json()["error"]


def test_handler_suggestions_working_mode(repo_with_stack: Repo) -> None:
    """GET /api/suggestions?mode=working returns valid JSON."""
    fake = _make_handler(repo_with_stack, "/api/suggestions?mode=working")
    assert fake._status == 200
    data = fake.response_json()
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)


def test_handler_suggestions_branch_mode(repo_with_stack: Repo) -> None:
    """GET /api/suggestions?mode=branch&source=branch_b returns valid JSON."""
    fake = _make_handler(
        repo_with_stack, "/api/suggestions?mode=branch&source=branch_b"
    )
    assert fake._status == 200
    data = fake.response_json()
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)


def test_handler_suggestions_branch_mode_missing_source(
    repo_with_stack: Repo,
) -> None:
    """GET /api/suggestions?mode=branch without source returns 400."""
    fake = _make_handler(repo_with_stack, "/api/suggestions?mode=branch")
    assert fake._status == 400
    assert "source" in fake.response_json()["error"].lower()


def test_handler_suggestions_branch_mode_untracked(temp_repo: Repo) -> None:
    """GET /api/suggestions?mode=branch&source=main returns 400 for untracked."""
    fake = _make_handler(temp_repo, "/api/suggestions?mode=branch&source=main")
    assert fake._status == 400
    assert "not tracked" in fake.response_json()["error"]


def test_handler_suggestions_error(repo_with_stack: Repo) -> None:
    """GET /api/suggestions returns 500 on unexpected error."""
    with patch(
        "shortcake.commands.ui._build_suggestions_payload",
        side_effect=RuntimeError("boom"),
    ):
        fake = _make_handler(repo_with_stack, "/api/suggestions?mode=working")
    assert fake._status == 500
    assert "boom" in fake.response_json()["error"]


# --- _build_suggestions_payload ---


def test_build_suggestions_payload_working(repo_with_stack: Repo) -> None:
    """_build_suggestions_payload returns suggestions for working mode."""
    payload = _build_suggestions_payload(repo_with_stack, "working")
    assert "suggestions" in payload
    assert isinstance(payload["suggestions"], list)


def test_build_suggestions_payload_branch(repo_with_stack: Repo) -> None:
    """_build_suggestions_payload returns suggestions for branch mode."""
    payload = _build_suggestions_payload(repo_with_stack, "branch", "branch_b")
    assert "suggestions" in payload
    assert isinstance(payload["suggestions"], list)


def test_build_suggestions_payload_invalid_mode(repo_with_stack: Repo) -> None:
    """_build_suggestions_payload raises for invalid mode."""
    with pytest.raises(ValueError, match="Invalid mode"):
        _build_suggestions_payload(repo_with_stack, "bad")


def test_build_suggestions_payload_branch_no_source(
    repo_with_stack: Repo,
) -> None:
    """_build_suggestions_payload raises when branch mode has no source."""
    with pytest.raises(ValueError, match="Missing required parameter"):
        _build_suggestions_payload(repo_with_stack, "branch")


def test_build_suggestions_payload_branch_parent_missing(
    repo_with_stack: Repo,
) -> None:
    """_build_suggestions_payload raises when parent branch doesn't exist locally."""
    # Delete branch_a so branch_b's parent is missing
    repo_with_stack.references.delete("refs/heads/branch_a")
    with pytest.raises(ValueError, match="does not exist locally"):
        _build_suggestions_payload(repo_with_stack, "branch", "branch_b")


def test_build_suggestions_payload_diff_error(repo_with_stack: Repo) -> None:
    """_build_suggestions_payload handles ValueError from _git_diff_patch gracefully."""
    original = _git_diff_patch

    def _failing_diff(repo_path, parent, branch):
        if branch == "branch_a":
            raise ValueError("diff failed")
        return original(repo_path, parent, branch)

    with patch("shortcake.commands.ui._git_diff_patch", side_effect=_failing_diff):
        payload = _build_suggestions_payload(repo_with_stack, "working")
    assert "suggestions" in payload


# Tests for _build_github_info_payload


def test_build_github_info_payload_no_token(temp_repo: Repo) -> None:
    """Returns empty branches when no GitHub token is available."""
    with patch("shortcake.commands.ui.get_github_token", return_value=None):
        payload = _build_github_info_payload(temp_repo, ["branch_a"])
    assert payload == {"branches": {}}


def test_build_github_info_payload_no_repo_info(temp_repo: Repo) -> None:
    """Returns empty branches when repo info cannot be determined."""
    with (
        patch("shortcake.commands.ui.get_github_token", return_value="token"),
        patch("shortcake.commands.ui.get_repo_info", return_value=None),
    ):
        payload = _build_github_info_payload(temp_repo, ["branch_a"])
    assert payload == {"branches": {}}


def test_build_github_info_payload_fetches_info(temp_repo: Repo) -> None:
    """Returns PR + CI info for branches."""
    mock_info = BranchGitHubInfo(
        pr_number=42,
        pr_url="https://github.com/owner/repo/pull/42",
        pr_is_draft=False,
        pr_state="open",
        check_status="success",
    )
    with (
        patch("shortcake.commands.ui.get_github_token", return_value="token"),
        patch("shortcake.commands.ui.get_repo_info", return_value=("owner", "repo")),
        patch("shortcake.commands.ui.GitHubClient") as mock_client_cls,
    ):
        mock_client = mock_client_cls.return_value
        mock_client.get_branch_github_info.return_value = mock_info
        mock_client.client = MagicMock()
        payload = _build_github_info_payload(temp_repo, ["feat"])

    assert payload == {
        "branches": {
            "feat": {
                "prNumber": 42,
                "prUrl": "https://github.com/owner/repo/pull/42",
                "prIsDraft": False,
                "prState": "open",
                "checkStatus": "success",
            }
        }
    }


def test_build_github_info_payload_client_error(temp_repo: Repo) -> None:
    """Returns empty branches when GitHubClient constructor fails."""
    with (
        patch("shortcake.commands.ui.get_github_token", return_value="token"),
        patch("shortcake.commands.ui.get_repo_info", return_value=("owner", "repo")),
        patch(
            "shortcake.commands.ui.GitHubClient",
            side_effect=RuntimeError("bad"),
        ),
    ):
        payload = _build_github_info_payload(temp_repo, ["feat"])
    assert payload == {"branches": {}}


# Tests for /api/github-info handler


def test_handler_github_info(repo_with_stack: Repo) -> None:
    """GitHub info endpoint returns branch info."""
    mock_info = BranchGitHubInfo(
        pr_number=10,
        pr_url="https://github.com/o/r/pull/10",
        pr_is_draft=True,
        pr_state="open",
        check_status="pending",
    )
    with patch(
        "shortcake.commands.ui._build_github_info_payload",
        return_value={
            "branches": {
                "branch_a": {
                    "prNumber": mock_info.pr_number,
                    "prUrl": mock_info.pr_url,
                    "prIsDraft": mock_info.pr_is_draft,
                    "checkStatus": mock_info.check_status,
                }
            }
        },
    ):
        fake = _make_handler(repo_with_stack, "/api/github-info")
    assert fake._status == 200
    data = fake.response_json()
    assert "branches" in data
    assert data["branches"]["branch_a"]["prNumber"] == 10


def test_handler_github_info_error(repo_with_stack: Repo) -> None:
    """GitHub info endpoint returns 500 on error."""
    with patch(
        "shortcake.commands.ui._build_github_info_payload",
        side_effect=RuntimeError("boom"),
    ):
        fake = _make_handler(repo_with_stack, "/api/github-info")
    assert fake._status == 500
    assert "error" in fake.response_json()


# --- /api/split-hunks POST handler tests ---


def test_post_split_hunks_invalid_json(temp_repo: Repo) -> None:
    """POST /api/split-hunks with invalid JSON returns 400."""
    fake = _make_post_handler(temp_repo, "/api/split-hunks", "not json")
    assert fake._status == 400
    assert "Invalid JSON" in fake.response_json()["error"]


def test_post_split_hunks_missing_fields(temp_repo: Repo) -> None:
    """POST /api/split-hunks with missing required fields returns 400."""
    fake = _make_post_handler(temp_repo, "/api/split-hunks", {"sourceBranch": "a"})
    assert fake._status == 400
    assert "Missing required fields" in fake.response_json()["error"]


def test_post_split_hunks_invalid_placement(temp_repo: Repo) -> None:
    """POST /api/split-hunks with invalid placement returns 400."""
    body = {
        "sourceBranch": "a",
        "commitMessage": "msg",
        "placement": "invalid",
        "hunks": [{"filePath": "f.py", "filePatch": "p", "hunkIndex": 0}],
    }
    fake = _make_post_handler(temp_repo, "/api/split-hunks", body)
    assert fake._status == 400
    assert "placement" in fake.response_json()["error"]


def test_post_split_hunks_empty_hunks(temp_repo: Repo) -> None:
    """POST /api/split-hunks with empty hunks array returns 400."""
    body = {
        "sourceBranch": "a",
        "commitMessage": "msg",
        "placement": "before",
        "hunks": [],
    }
    fake = _make_post_handler(temp_repo, "/api/split-hunks", body)
    assert fake._status == 400
    assert "non-empty" in fake.response_json()["error"]


def test_post_split_hunks_non_dict_hunk(temp_repo: Repo) -> None:
    """POST /api/split-hunks with non-dict hunk element returns 400."""
    body = {
        "sourceBranch": "a",
        "commitMessage": "msg",
        "placement": "before",
        "hunks": [42],
    }
    fake = _make_post_handler(temp_repo, "/api/split-hunks", body)
    assert fake._status == 400
    assert "Each hunk must be an object" in fake.response_json()["error"]


def test_post_split_hunks_missing_hunk_fields(temp_repo: Repo) -> None:
    """POST /api/split-hunks with hunk missing fields returns 400."""
    body = {
        "sourceBranch": "a",
        "commitMessage": "msg",
        "placement": "before",
        "hunks": [{"filePath": "f.py"}],
    }
    fake = _make_post_handler(temp_repo, "/api/split-hunks", body)
    assert fake._status == 400
    assert "Hunk missing fields" in fake.response_json()["error"]


def test_post_split_hunks_success(repo_with_stack: Repo) -> None:
    """POST /api/split-hunks success returns 200 with result."""
    mock_result = MagicMock()
    mock_result.source_branch = "branch_a"
    mock_result.new_branch = "new-branch"
    mock_result.placement = "before"
    mock_result.file_paths = ["test.txt"]
    mock_result.restacked_branches = []

    body = {
        "sourceBranch": "branch_a",
        "commitMessage": "feat: split",
        "placement": "before",
        "hunks": [{"filePath": "test.txt", "filePatch": "patch", "hunkIndex": 0}],
    }
    with patch("shortcake.commands.ui._split_hunks", return_value=mock_result):
        fake = _make_post_handler(repo_with_stack, "/api/split-hunks", body)
    assert fake._status == 200
    data = fake.response_json()
    assert data["sourceBranch"] == "branch_a"
    assert data["newBranch"] == "new-branch"
    assert data["placement"] == "before"


def test_post_split_hunks_move_error(repo_with_stack: Repo) -> None:
    """POST /api/split-hunks MoveError returns 400."""
    from shortcake.commands.move_lines import MoveError

    body = {
        "sourceBranch": "branch_a",
        "commitMessage": "feat: split",
        "placement": "before",
        "hunks": [{"filePath": "test.txt", "filePatch": "patch", "hunkIndex": 0}],
    }
    with patch(
        "shortcake.commands.ui._split_hunks",
        side_effect=MoveError("split failed"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/split-hunks", body)
    assert fake._status == 400
    assert "split failed" in fake.response_json()["error"]


def test_post_split_hunks_unexpected_error(repo_with_stack: Repo) -> None:
    """POST /api/split-hunks unexpected error returns 500."""
    body = {
        "sourceBranch": "branch_a",
        "commitMessage": "feat: split",
        "placement": "before",
        "hunks": [{"filePath": "test.txt", "filePatch": "patch", "hunkIndex": 0}],
    }
    with patch(
        "shortcake.commands.ui._split_hunks",
        side_effect=RuntimeError("boom"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/split-hunks", body)
    assert fake._status == 500
    assert "boom" in fake.response_json()["error"]


# --- /api/split-lines POST handler tests ---

_VALID_SELECTION = {
    "filePath": "app.py",
    "filePatch": "fake-patch",
    "startLine": 1,
    "endLine": 2,
    "side": "additions",
}

_VALID_CHUNK = {
    "commitMessage": "feat: chunk",
    "selections": [_VALID_SELECTION],
}


def test_post_split_lines_invalid_json(temp_repo: Repo) -> None:
    """POST /api/split-lines with invalid JSON returns 400."""
    fake = _make_post_handler(temp_repo, "/api/split-lines", "not json")
    assert fake._status == 400
    assert "Invalid JSON" in fake.response_json()["error"]


def test_post_split_lines_missing_fields(temp_repo: Repo) -> None:
    """POST /api/split-lines with missing required fields returns 400."""
    fake = _make_post_handler(temp_repo, "/api/split-lines", {"sourceBranch": "a"})
    assert fake._status == 400
    assert "Missing required fields" in fake.response_json()["error"]


def test_post_split_lines_empty_chunks(temp_repo: Repo) -> None:
    """POST /api/split-lines with empty chunks array returns 400."""
    fake = _make_post_handler(
        temp_repo, "/api/split-lines", {"sourceBranch": "a", "chunks": []}
    )
    assert fake._status == 400
    assert "non-empty" in fake.response_json()["error"]


def test_post_split_lines_non_dict_chunk(temp_repo: Repo) -> None:
    """POST /api/split-lines with non-dict chunk returns 400."""
    fake = _make_post_handler(
        temp_repo, "/api/split-lines", {"sourceBranch": "a", "chunks": [42]}
    )
    assert fake._status == 400
    assert "Each chunk must be an object" in fake.response_json()["error"]


def test_post_split_lines_chunk_missing_fields(temp_repo: Repo) -> None:
    """POST /api/split-lines with chunk missing required fields returns 400."""
    fake = _make_post_handler(
        temp_repo,
        "/api/split-lines",
        {"sourceBranch": "a", "chunks": [{"commitMessage": "x"}]},
    )
    assert fake._status == 400
    assert "commitMessage" in fake.response_json()["error"]


def test_post_split_lines_empty_selections(temp_repo: Repo) -> None:
    """POST /api/split-lines with empty selections list returns 400."""
    body = {
        "sourceBranch": "a",
        "chunks": [{"commitMessage": "feat: x", "selections": []}],
    }
    fake = _make_post_handler(temp_repo, "/api/split-lines", body)
    assert fake._status == 400
    assert "non-empty" in fake.response_json()["error"]


def test_post_split_lines_non_dict_selection(temp_repo: Repo) -> None:
    """POST /api/split-lines with non-dict selection returns 400."""
    body = {
        "sourceBranch": "a",
        "chunks": [{"commitMessage": "feat: x", "selections": [99]}],
    }
    fake = _make_post_handler(temp_repo, "/api/split-lines", body)
    assert fake._status == 400
    assert "Each selection must be an object" in fake.response_json()["error"]


def test_post_split_lines_selection_missing_fields(temp_repo: Repo) -> None:
    """POST /api/split-lines with selection missing fields returns 400."""
    body = {
        "sourceBranch": "a",
        "chunks": [
            {
                "commitMessage": "feat: x",
                "selections": [{"filePath": "app.py"}],
            }
        ],
    }
    fake = _make_post_handler(temp_repo, "/api/split-lines", body)
    assert fake._status == 400
    assert "Selection missing fields" in fake.response_json()["error"]


def test_post_split_lines_success(repo_with_stack: Repo) -> None:
    """POST /api/split-lines success returns 200 with result."""
    mock_result = MagicMock()
    mock_result.source_branch = "branch_a"
    mock_result.new_branches = ["chunk-one"]
    mock_result.restacked_branches = []

    body = {"sourceBranch": "branch_a", "chunks": [_VALID_CHUNK]}
    with patch("shortcake.commands.ui._split_lines_batch", return_value=mock_result):
        fake = _make_post_handler(repo_with_stack, "/api/split-lines", body)
    assert fake._status == 200
    data = fake.response_json()
    assert data["sourceBranch"] == "branch_a"
    assert data["newBranches"] == ["chunk-one"]


def test_post_split_lines_move_error(repo_with_stack: Repo) -> None:
    """POST /api/split-lines MoveError returns 400."""
    from shortcake.commands.move_lines import MoveError

    body = {"sourceBranch": "branch_a", "chunks": [_VALID_CHUNK]}
    with patch(
        "shortcake.commands.ui._split_lines_batch",
        side_effect=MoveError("lines failed"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/split-lines", body)
    assert fake._status == 400
    assert "lines failed" in fake.response_json()["error"]


def test_post_split_lines_unexpected_error(repo_with_stack: Repo) -> None:
    """POST /api/split-lines unexpected error returns 500."""
    body = {"sourceBranch": "branch_a", "chunks": [_VALID_CHUNK]}
    with patch(
        "shortcake.commands.ui._split_lines_batch",
        side_effect=RuntimeError("oops"),
    ):
        fake = _make_post_handler(repo_with_stack, "/api/split-lines", body)
    assert fake._status == 500
    assert "oops" in fake.response_json()["error"]


# --- review endpoints ---


def test_handler_review_models(temp_repo: Repo) -> None:
    """GET /api/review/models returns available models."""
    with patch("shortcake.commands._review.shutil.which", return_value=None):
        fake = _make_handler(temp_repo, "/api/review/models")
    assert fake._status == 200
    data = fake.response_json()
    assert "models" in data
    assert isinstance(data["models"], list)
    assert all(not m["available"] for m in data["models"])


def test_post_review_missing_fields(temp_repo: Repo) -> None:
    """POST /api/review with missing fields returns 400."""
    fake = _make_post_handler(temp_repo, "/api/review", {"branch": "x"})
    assert fake._status == 400
    assert "Missing" in fake.response_json()["error"]


def test_post_review_invalid_json(temp_repo: Repo) -> None:
    """POST /api/review with invalid JSON returns 400."""
    fake = _make_post_handler(temp_repo, "/api/review", "not json{{{")
    assert fake._status == 400
    assert "Invalid JSON" in fake.response_json()["error"]


def test_post_review_untracked_branch(temp_repo: Repo) -> None:
    """POST /api/review for untracked branch returns 400."""
    fake = _make_post_handler(
        temp_repo,
        "/api/review",
        {"branch": "nope", "models": ["claude:sonnet"]},
    )
    assert fake._status == 400
    assert "not tracked" in fake.response_json()["error"]


def _parse_sse_events(raw: bytes) -> list[tuple[str, dict]]:
    """Parse SSE events from raw bytes into (event_type, data) tuples."""
    events: list[tuple[str, dict]] = []
    text = raw.decode()
    event_type = ""
    for line in text.split("\n"):
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                data = {}
            events.append((event_type, data))
            event_type = ""
    return events


def test_post_review_success_sse(repo_with_stack: Repo) -> None:
    """POST /api/review streams SSE events for each model."""
    from shortcake.commands._review import ReviewComment, ReviewResult

    mock_result = ReviewResult(
        model="claude:sonnet",
        summary="Test review.",
        comments=[
            ReviewComment(
                file="a.txt",
                start_line=1,
                end_line=1,
                side="additions",
                text="Looks good.",
                severity="info",
            ),
        ],
    )
    with patch("shortcake.commands.ui._run_review", return_value=mock_result):
        fake = _make_post_handler(
            repo_with_stack,
            "/api/review",
            {"branch": "branch_b", "models": ["claude:sonnet"]},
        )
    assert fake._status == 200
    events = _parse_sse_events(fake.wfile.getvalue())
    review_events = [e for e in events if e[0] == "review"]
    done_events = [e for e in events if e[0] == "done"]
    assert len(review_events) == 1
    assert review_events[0][1]["model"] == "claude:sonnet"
    assert review_events[0][1]["summary"] == "Test review."
    assert len(done_events) == 1


def test_post_review_working_branch_uses_working_diff(repo_with_stack: Repo) -> None:
    """POST /api/review can review the synthetic working-changes branch."""
    from shortcake.commands._review import ReviewResult

    mock_result = ReviewResult(
        model="claude:sonnet",
        summary="Working review.",
        comments=[],
    )

    def fake_run_review(patch: str, model: str) -> ReviewResult:
        assert patch == "working patch"
        assert model == "claude:sonnet"
        return mock_result

    with (
        patch("shortcake.commands.ui._git_working_diff", return_value="working patch"),
        patch("shortcake.commands.ui._run_review", side_effect=fake_run_review),
    ):
        fake = _make_post_handler(
            repo_with_stack,
            "/api/review",
            {"branch": "__working__", "models": ["claude:sonnet"]},
        )

    assert fake._status == 200
    events = _parse_sse_events(fake.wfile.getvalue())
    review_events = [e for e in events if e[0] == "review"]
    assert len(review_events) == 1
    assert review_events[0][1]["summary"] == "Working review."


def test_post_review_with_synthesis(repo_with_stack: Repo) -> None:
    """POST /api/review with synthesize runs synthesis after reviews."""
    from shortcake.commands._review import ReviewResult

    mock_review = ReviewResult(
        model="claude:sonnet",
        summary="Individual.",
        comments=[],
    )
    mock_synth = ReviewResult(
        model="claude:opus",
        summary="Synthesized.",
        comments=[],
        fix_prompt="Fix all the things.",
    )
    with (
        patch("shortcake.commands.ui._run_review", return_value=mock_review),
        patch("shortcake.commands.ui._run_synthesis", return_value=mock_synth),
    ):
        fake = _make_post_handler(
            repo_with_stack,
            "/api/review",
            {
                "branch": "branch_b",
                "models": ["claude:sonnet"],
                "synthesize": "claude:opus",
            },
        )
    assert fake._status == 200
    events = _parse_sse_events(fake.wfile.getvalue())
    synth_events = [e for e in events if e[0] == "synthesis"]
    assert len(synth_events) == 1
    assert synth_events[0][1]["summary"] == "Synthesized."
    assert synth_events[0][1]["fix_prompt"] == "Fix all the things."


def test_post_review_diff_error(repo_with_stack: Repo) -> None:
    """POST /api/review returns 500 if diff generation fails."""
    with patch(
        "shortcake.commands.ui._git_diff_patch",
        side_effect=RuntimeError("diff boom"),
    ):
        fake = _make_post_handler(
            repo_with_stack,
            "/api/review",
            {"branch": "branch_b", "models": ["claude:sonnet"]},
        )
    assert fake._status == 500
    assert "diff boom" in fake.response_json()["error"]


def test_post_review_run_review_exception(repo_with_stack: Repo) -> None:
    """POST /api/review handles exception in _run_review gracefully."""
    with patch(
        "shortcake.commands.ui._run_review",
        side_effect=RuntimeError("review crash"),
    ):
        fake = _make_post_handler(
            repo_with_stack,
            "/api/review",
            {"branch": "branch_b", "models": ["claude:sonnet"]},
        )
    assert fake._status == 200
    events = _parse_sse_events(fake.wfile.getvalue())
    # No review events since it crashed, but done event should still appear
    review_events = [e for e in events if e[0] == "review"]
    done_events = [e for e in events if e[0] == "done"]
    assert len(review_events) == 0
    assert len(done_events) == 1


def test_post_review_synthesis_exception(repo_with_stack: Repo) -> None:
    """POST /api/review handles synthesis exception gracefully."""
    from shortcake.commands._review import ReviewResult

    mock_review = ReviewResult(
        model="claude:sonnet",
        summary="OK.",
        comments=[],
    )
    with (
        patch("shortcake.commands.ui._run_review", return_value=mock_review),
        patch(
            "shortcake.commands.ui._run_synthesis",
            side_effect=RuntimeError("synth crash"),
        ),
    ):
        fake = _make_post_handler(
            repo_with_stack,
            "/api/review",
            {
                "branch": "branch_b",
                "models": ["claude:sonnet"],
                "synthesize": "claude:opus",
            },
        )
    assert fake._status == 200
    events = _parse_sse_events(fake.wfile.getvalue())
    # Review event should still be there
    review_events = [e for e in events if e[0] == "review"]
    synth_events = [e for e in events if e[0] == "synthesis"]
    done_events = [e for e in events if e[0] == "done"]
    assert len(review_events) == 1
    assert len(synth_events) == 0
    assert len(done_events) == 1

import shutil
import socket
from pathlib import Path

import pytest
from dulwich.repo import Repo

from shortcake.commands.ui import (
    _build_diff_payload,
    _build_stack_payload,
    _find_open_port,
    _resolve_frontend_dir,
    _resolve_js_runtime,
    _runtime_candidates,
)


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
    assert branch_a["isCurrent"] is False

    assert branch_b["parent"] == "branch_a"
    assert branch_b["commitCount"] == 1
    assert branch_b["isCurrent"] is True


def test_build_stack_payload_no_tracked_branches(temp_repo: Repo) -> None:
    """Untracked repositories return an empty stack."""
    payload = _build_stack_payload(temp_repo)

    assert payload["branches"] == []


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
    repo_path = Path(temp_repo.path)
    src_web_dir = repo_path / "src" / "shortcake" / "_web"
    src_web_dir.mkdir(parents=True)
    (src_web_dir / "package.json").write_text("{}")
    (src_web_dir / "index.html").write_text("<!doctype html>")

    monkeypatch.delenv("SHORTCAKE_UI_DIR", raising=False)
    assert _resolve_frontend_dir(repo_path) == src_web_dir


def test_resolve_frontend_dir_from_packaged_fallback(temp_repo: Repo) -> None:
    """If repo has no web/, fall back to packaged shortcake._web assets."""
    repo_path = Path(temp_repo.path)
    frontend_dir = _resolve_frontend_dir(repo_path)
    assert frontend_dir is not None
    assert (frontend_dir / "package.json").is_file()
    assert (frontend_dir / "index.html").is_file()


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

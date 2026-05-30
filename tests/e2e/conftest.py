import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page

from shortcake._trailers import Trailers
from shortcake.commands.ui import _start_api_server
from tests._git_helpers import commit_files, create_branch, get_branch_head, init_repo


def open_diff_switcher(page: Page) -> None:
    """Open the header diff switcher and wait for its listbox."""
    page.get_by_role("button", name="Switch diff").click()
    page.wait_for_selector("#sc-diff-listbox", timeout=5_000)


def select_diff_option(page: Page, label: str) -> None:
    """Select a diff target from the header switcher."""
    open_diff_switcher(page)
    page.locator("#sc-diff-listbox [role='option']").filter(has_text=label).click()


def pytest_collection_modifyitems(config, items):
    """Auto-skip e2e tests unless explicitly targeted."""
    markexpr = config.getoption("markexpr", default="")
    args_target_e2e = any("e2e" in str(a) for a in config.args)
    marker_targets_e2e = bool(markexpr and "e2e" in markexpr)

    if args_target_e2e or marker_targets_e2e:
        return

    skip = pytest.mark.skip(reason="E2E tests: run with `pytest tests/e2e/`")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Grant clipboard permissions for comment copy tests."""
    return {
        **browser_context_args,
        "permissions": ["clipboard-read", "clipboard-write"],
    }


@pytest.fixture(scope="session")
def e2e_repo(tmp_path_factory):
    """Create a test repo with stack: main -> branch_a -> branch_b."""
    tmp_path = tmp_path_factory.mktemp("e2e_repo")
    repo = init_repo(tmp_path)

    # Initial commit on main
    commit_files(repo, {tmp_path / "README.md": "# E2E Test Repo"}, "Initial commit")

    # branch_a from main
    create_branch(repo, "branch_a", get_branch_head(repo, "main"), checkout=True)
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: add feature A")
    commit_files(
        repo,
        {
            tmp_path / "feature_a.py": (
                'def greet():\n    return "Hello from feature A"\n'
            )
        },
        message_a,
    )
    branch_a_sha = get_branch_head(repo, "branch_a")

    # branch_b from branch_a
    create_branch(repo, "branch_b", branch_a_sha, checkout=True)
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: add feature B")
    commit_files(
        repo,
        {
            tmp_path / "feature_b.py": (
                'def farewell():\n    return "Goodbye from feature B"\n'
            )
        },
        message_b,
    )

    yield repo


@pytest.fixture(scope="session")
def e2e_repo_path(e2e_repo):
    """Working directory path of the e2e repo."""
    # repo.path is the .git directory; parent is the working tree
    return Path(e2e_repo.path).parent


@pytest.fixture(scope="session")
def _vite_runtime():
    """Resolve the JS runtime for Vite."""
    for cmd in ("pybun", "bun"):
        if shutil.which(cmd):
            return cmd
    pytest.skip("No JS runtime (bun/pybun) found")


@pytest.fixture(scope="session")
def ui_url(e2e_repo, _vite_runtime):
    """Start API + Vite dev servers, yield the web URL."""
    host = "127.0.0.1"

    # API server on OS-picked port
    server = _start_api_server(Path(e2e_repo.path), host, 0)
    api_port = server.server_address[1]
    api_origin = f"http://{host}:{api_port}"

    # Frontend directory
    frontend_dir = Path(__file__).resolve().parents[2] / "src" / "shortcake" / "_web"
    assert frontend_dir.is_dir(), f"Frontend dir not found: {frontend_dir}"

    # Free port for Vite
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        web_port = sock.getsockname()[1]

    # Install frontend deps
    subprocess.run([_vite_runtime, "install"], cwd=frontend_dir, check=True)

    # Start Vite dev server
    env = {**os.environ, "SHORTCAKE_API_ORIGIN": api_origin}
    vite = subprocess.Popen(
        [
            _vite_runtime,
            "run",
            "dev",
            "--host",
            host,
            "--port",
            str(web_port),
            "--strictPort",
        ],
        cwd=frontend_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    web_url = f"http://{host}:{web_port}"

    # Wait for Vite readiness
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(web_url, timeout=2)
            if resp.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.5)
    else:
        vite.terminate()
        server.shutdown()
        pytest.fail("Vite dev server did not start within 30s")

    yield web_url

    vite.terminate()
    vite.wait(timeout=10)
    server.shutdown()
    server.server_close()


@pytest.fixture
def ui_page(page: Page, ui_url: str):
    """Navigate to the UI and wait for the stack and diff to load."""
    page.goto(ui_url)
    # Default view is working changes; select branch_b to load a branch diff.
    select_diff_option(page, "branch_b")
    page.wait_for_selector(".diff-content", timeout=10_000)
    return page

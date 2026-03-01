import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from playwright.sync_api import Page

from shortcake._trailers import Trailers
from shortcake.commands.ui import _start_api_server


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
    repo = Repo.init(tmp_path, default_branch=b"main")

    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )

    # Initial commit on main
    readme = tmp_path / "README.md"
    readme.write_text("# E2E Test Repo")
    porcelain.add(repo, paths=[str(readme)])
    porcelain.commit(repo, message=b"Initial commit")

    # branch_a from main
    main_sha = repo.refs[b"refs/heads/main"]
    repo.refs[b"refs/heads/branch_a"] = main_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_a")
    porcelain.reset(repo, "hard")

    feature_a = tmp_path / "feature_a.py"
    feature_a.write_text('def greet():\n    return "Hello from feature A"\n')
    porcelain.add(repo, paths=[str(feature_a)])
    trailers_a = Trailers(parent_branch="main")
    message_a = trailers_a.apply_to("feat: add feature A")
    porcelain.commit(repo, message=message_a.encode())
    branch_a_sha = repo.refs[b"refs/heads/branch_a"]

    # branch_b from branch_a
    repo.refs[b"refs/heads/branch_b"] = branch_a_sha
    repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/branch_b")
    porcelain.reset(repo, "hard")

    feature_b = tmp_path / "feature_b.py"
    feature_b.write_text('def farewell():\n    return "Goodbye from feature B"\n')
    porcelain.add(repo, paths=[str(feature_b)])
    trailers_b = Trailers(parent_branch="branch_a")
    message_b = trailers_b.apply_to("feat: add feature B")
    porcelain.commit(repo, message=message_b.encode())

    yield repo
    repo.close()


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
    server = _start_api_server(e2e_repo, host, 0)
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
    # Wait for stack sidebar to render branch buttons
    page.wait_for_selector("text=branch_a", timeout=15_000)
    # Wait for default branch diff to load (branch_b is auto-selected)
    page.wait_for_selector(".diff-content", timeout=10_000)
    return page

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

type GitEditorScript = Callable[[str], None]


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_home = tmp_path / "config"
    config_home.mkdir()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    return config_home


@pytest.fixture
def isolated_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    monkeypatch.chdir(repo_path)

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    readme = repo_path / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


@pytest.fixture
def git_editor_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitEditorScript:
    """Create a git editor script that writes predetermined commit messages."""
    script_path = tmp_path / "fake_editor.sh"

    def create_editor(commit_message: str) -> None:
        """Create a script that writes the given commit message."""
        script_content = f"""#!/bin/sh
echo "{commit_message}" > "$1"
"""
        script_path.write_text(script_content)
        script_path.chmod(0o755)
        monkeypatch.setenv("GIT_EDITOR", str(script_path))

    return create_editor

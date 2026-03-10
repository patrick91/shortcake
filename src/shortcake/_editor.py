import os
import subprocess
import tempfile
from pathlib import Path

import pygit2


def _get_git_editor() -> str | None:
    """Get editor from git config (core.editor)."""
    try:
        git_dir = pygit2.discover_repository(Path.cwd())
        if git_dir is None:
            return None
        repo = pygit2.Repository(git_dir)
        if "core.editor" not in repo.config:
            return None
        return repo.config["core.editor"]
    except pygit2.GitError:
        return None


def get_editor() -> str:
    """Get the user's preferred editor.

    Checks in order: VISUAL, EDITOR, git config core.editor, then falls back to vi.
    """
    return (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or _get_git_editor()
        or "vi"
    )


def open_editor(initial_content: str = "") -> str | None:
    """Open editor and return the content, or None if aborted."""
    editor = get_editor()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(initial_content)
        temp_path = Path(f.name)

    try:
        result = subprocess.run([editor, str(temp_path)])
        if result.returncode != 0:
            return None

        content = temp_path.read_text().strip()
        # Remove comment lines (lines starting with #)
        lines = [line for line in content.split("\n") if not line.startswith("#")]
        return "\n".join(lines).strip() or None
    finally:
        temp_path.unlink(missing_ok=True)

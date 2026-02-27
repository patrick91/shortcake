import os
import subprocess
import tempfile
from pathlib import Path

from dulwich.errors import NotGitRepository

from shortcake import _git as git


def _get_git_editor() -> str | None:
    """Get editor from git config (core.editor)."""
    try:
        repo = git.open_repo()
        config = repo.get_config_stack()
        return config.get((b"core",), b"editor").decode()
    except (NotGitRepository, KeyError):
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

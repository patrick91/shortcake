import os
from pathlib import Path
from unittest.mock import patch

import pygit2
import pytest

from shortcake._editor import _get_git_editor, get_editor, open_editor
from tests._git_helpers import Repo

# Editor detection tests


def test_get_editor_visual() -> None:
    """Test VISUAL env var takes priority."""
    with patch.dict(os.environ, {"VISUAL": "code", "EDITOR": "vim"}):
        assert get_editor() == "code"


def test_get_editor_editor() -> None:
    """Test EDITOR env var fallback."""
    with patch.dict(os.environ, {"EDITOR": "nano"}, clear=True):
        # Clear VISUAL
        os.environ.pop("VISUAL", None)
        assert get_editor() == "nano"


def test_get_editor_git_config(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test git config core.editor fallback."""
    # Set editor in git config
    config = temp_repo.get_config()
    config.set((b"core",), b"editor", b"emacs")
    config.write_to_path()

    # Clear env vars and change to repo dir
    monkeypatch.chdir(temp_repo.path)
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("VISUAL", None)
        os.environ.pop("EDITOR", None)
        assert get_editor() == "emacs"


def test_get_editor_default() -> None:
    """Test default to vi."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("shortcake._editor._get_git_editor", return_value=None),
    ):
        os.environ.pop("VISUAL", None)
        os.environ.pop("EDITOR", None)
        assert get_editor() == "vi"


def test_get_git_editor_no_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _get_git_editor returns None when not in a repo."""
    monkeypatch.chdir(tmp_path)
    assert _get_git_editor() is None


def test_get_git_editor_missing_config(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _get_git_editor returns None when core.editor is not configured."""

    class FakeRepo:
        config = {}

    monkeypatch.chdir(temp_repo.path)
    monkeypatch.setattr("shortcake._editor.pygit2.Repository", lambda _: FakeRepo())
    assert _get_git_editor() is None


def test_get_git_editor_git_error(
    temp_repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _get_git_editor returns None when pygit2 raises."""
    monkeypatch.chdir(temp_repo.path)
    monkeypatch.setattr(
        "shortcake._editor.pygit2.Repository",
        lambda _: (_ for _ in ()).throw(pygit2.GitError("broken repo")),
    )
    assert _get_git_editor() is None


# Editor interaction tests


def test_open_editor_strips_comments(tmp_path: Path) -> None:
    """Test that comment lines are removed."""
    # Create a fake editor that writes content with comments
    editor_script = tmp_path / "fake_editor.sh"
    editor_script.write_text(
        """#!/bin/sh
cat > "$1" << 'EOF'
feat: add feature
# This is a comment
# Another comment
Body text here
EOF
"""
    )
    editor_script.chmod(0o755)

    with patch.dict(os.environ, {"EDITOR": str(editor_script)}, clear=True):
        result = open_editor()

    # Comment lines are removed, non-comment lines are kept
    assert result == "feat: add feature\nBody text here"


def test_open_editor_returns_none_on_empty(tmp_path: Path) -> None:
    """Test returns None for empty content."""
    # Create a fake editor that writes only comments
    editor_script = tmp_path / "fake_editor.sh"
    editor_script.write_text(
        """#!/bin/sh
cat > "$1" << 'EOF'
# Only comments
# Nothing else
EOF
"""
    )
    editor_script.chmod(0o755)

    with patch.dict(os.environ, {"EDITOR": str(editor_script)}, clear=True):
        result = open_editor()

    assert result is None


def test_open_editor_preserves_initial_content(tmp_path: Path) -> None:
    """Test initial content is available to editor."""
    # Create a fake editor that appends to initial content
    editor_script = tmp_path / "fake_editor.sh"
    editor_script.write_text(
        """#!/bin/sh
echo " appended" >> "$1"
"""
    )
    editor_script.chmod(0o755)

    with patch.dict(os.environ, {"EDITOR": str(editor_script)}, clear=True):
        result = open_editor("initial")

    assert result == "initial appended"


def test_open_editor_returns_none_on_error(tmp_path: Path) -> None:
    """Test returns None when editor fails."""
    # Create a fake editor that exits with error
    editor_script = tmp_path / "fake_editor.sh"
    editor_script.write_text("#!/bin/sh\nexit 1\n")
    editor_script.chmod(0o755)

    with patch.dict(os.environ, {"EDITOR": str(editor_script)}, clear=True):
        result = open_editor()

    assert result is None

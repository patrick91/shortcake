from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from shortcake.commands._review import (
    MAX_PATCH_SIZE,
    ReviewComment,
    ReviewResult,
    _build_prompt,
    _get_available_models,
    _parse_review_response,
    _run_review,
)


# -- _get_available_models ---------------------------------------------------


def test_get_available_models_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    models = _get_available_models()
    assert len(models) == 2
    assert models[0]["id"] == "claude"
    assert models[0]["available"] is True
    assert models[1]["id"] == "codex"
    assert models[1]["available"] is True


def test_get_available_models_only_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None
    )
    models = _get_available_models()
    assert models[0]["id"] == "claude"
    assert models[0]["available"] is True
    assert models[1]["id"] == "codex"
    assert models[1]["available"] is False


def test_get_available_models_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    models = _get_available_models()
    assert models[0]["available"] is False
    assert models[1]["available"] is False


# -- _build_prompt -----------------------------------------------------------


def test_build_prompt_includes_patch() -> None:
    patch = "diff --git a/foo.py b/foo.py\n+hello"
    prompt = _build_prompt(patch)
    assert patch in prompt
    assert "<diff>" in prompt
    assert "</diff>" in prompt


def test_build_prompt_truncates_large_patch() -> None:
    patch = "x" * (MAX_PATCH_SIZE + 500)
    prompt = _build_prompt(patch)
    assert "... [patch truncated" in prompt
    # The original oversized patch should NOT appear in full
    assert patch not in prompt
    # But the first MAX_PATCH_SIZE characters should be present
    assert "x" * MAX_PATCH_SIZE in prompt


# -- _parse_review_response --------------------------------------------------


def _make_valid_response(
    summary: str = "Looks good.",
    comments: list[dict] | None = None,
) -> str:
    if comments is None:
        comments = [
            {
                "file": "foo.py",
                "start_line": 10,
                "end_line": 12,
                "side": "additions",
                "text": "Consider renaming this variable.",
                "severity": "suggestion",
            }
        ]
    return json.dumps({"summary": summary, "comments": comments})


def test_parse_review_response_valid_json() -> None:
    raw = _make_valid_response()
    result = _parse_review_response(raw, "claude")
    assert result.model == "claude"
    assert result.summary == "Looks good."
    assert result.error is None
    assert len(result.comments) == 1
    assert result.comments[0].file == "foo.py"
    assert result.comments[0].start_line == 10
    assert result.comments[0].end_line == 12
    assert result.comments[0].side == "additions"
    assert result.comments[0].text == "Consider renaming this variable."
    assert result.comments[0].severity == "suggestion"


def test_parse_review_response_markdown_fences() -> None:
    inner = _make_valid_response(summary="Fenced response.")
    raw = f"```json\n{inner}\n```"
    result = _parse_review_response(raw, "codex")
    assert result.model == "codex"
    assert result.summary == "Fenced response."
    assert result.error is None
    assert len(result.comments) == 1


def test_parse_review_response_markdown_fences_no_lang() -> None:
    inner = _make_valid_response(summary="Plain fenced.")
    raw = f"```\n{inner}\n```"
    result = _parse_review_response(raw, "claude")
    assert result.summary == "Plain fenced."
    assert result.error is None


def test_parse_review_response_malformed_json() -> None:
    raw = "this is not json at all {{{{"
    result = _parse_review_response(raw, "claude")
    assert result.model == "claude"
    assert result.error is not None
    assert "parse" in result.error.lower()


def test_parse_review_response_empty() -> None:
    result = _parse_review_response("", "claude")
    assert result.model == "claude"
    assert result.error is not None
    assert "parse" in result.error.lower()


def test_parse_review_response_missing_fields() -> None:
    raw = json.dumps({"unexpected_key": "value"})
    result = _parse_review_response(raw, "claude")
    assert result.model == "claude"
    assert result.error is None
    assert result.summary == ""
    assert result.comments == []


def test_parse_review_response_missing_comment_fields() -> None:
    raw = json.dumps({"summary": "OK", "comments": [{"file": "bar.py"}]})
    result = _parse_review_response(raw, "claude")
    assert result.error is None
    assert len(result.comments) == 1
    comment = result.comments[0]
    assert comment.file == "bar.py"
    assert comment.start_line == 0
    assert comment.end_line == 0
    assert comment.side == "additions"
    assert comment.severity == "suggestion"


def test_parse_review_response_non_dict_comment_skipped() -> None:
    raw = json.dumps({"summary": "OK", "comments": ["not a dict", 42]})
    result = _parse_review_response(raw, "claude")
    assert result.error is None
    assert result.comments == []


# -- _run_review -------------------------------------------------------------


def test_run_review_claude_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response_json = _make_valid_response(summary="Claude review.")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=["claude", "-p", "..."],
            returncode=0,
            stdout=response_json,
            stderr="",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "claude")
    assert result.model == "claude"
    assert result.summary == "Claude review."
    assert result.error is None

    # Verify the command that was invoked
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"


def test_run_review_codex_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response_json = _make_valid_response(summary="Codex review.")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=["codex", "--quiet", "..."],
            returncode=0,
            stdout=response_json,
            stderr="",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "codex")
    assert result.model == "codex"
    assert result.summary == "Codex review."
    assert result.error is None

    # Verify the command that was invoked
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "codex"
    assert cmd[1] == "--quiet"


def test_run_review_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=120)

    monkeypatch.setattr("subprocess.run", raise_timeout)

    result = _run_review("some patch", "claude")
    assert result.model == "claude"
    assert result.error is not None
    assert "timed out" in result.error.lower()


def test_run_review_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=["claude", "-p", "..."],
            returncode=1,
            stdout="",
            stderr="Something went wrong",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "claude")
    assert result.model == "claude"
    assert result.error is not None
    assert "exited with code 1" in result.error
    assert "Something went wrong" in result.error


def test_run_review_unknown_model() -> None:
    result = _run_review("some patch", "gpt-unknown")
    assert result.model == "gpt-unknown"
    assert result.error is not None
    assert "Unknown model" in result.error

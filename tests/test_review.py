from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from shortcake.cli import app
from shortcake.commands._review import (
    MAX_PATCH_SIZE,
    ReviewResult,
    _build_prompt,
    _get_available_models,
    _parse_review_response,
    _run_review,
)
from tests._git_helpers import Repo

runner = CliRunner()


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


def test_run_review_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_fnf(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr("subprocess.run", raise_fnf)

    result = _run_review("some patch", "claude")
    assert result.error is not None
    assert "not found" in result.error.lower()


def test_run_review_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_os(*args: object, **kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr("subprocess.run", raise_os)

    result = _run_review("some patch", "claude")
    assert result.error is not None
    assert "Failed to run" in result.error


def test_parse_review_response_json_in_prose() -> None:
    """JSON embedded in surrounding text (not in code fences)."""
    inner = _make_valid_response(summary="Embedded.")
    raw = f"Here is my review:\n{inner}\nHope that helps!"
    result = _parse_review_response(raw, "claude")
    assert result.summary == "Embedded."
    assert result.error is None


def test_parse_review_response_comment_with_bad_types() -> None:
    """Comment with values that cause TypeError during int()."""
    raw = json.dumps({
        "summary": "OK",
        "comments": [
            {"file": "x.py", "start_line": "not_a_number"},
        ],
    })
    result = _parse_review_response(raw, "claude")
    assert result.error is None
    # The bad comment should be skipped
    assert result.comments == []


# -- CLI command (review) ----------------------------------------------------


def test_cli_review_not_tracked(
    repo_with_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 1
    assert "not tracked" in result.output


def test_cli_review_no_models(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: None,
    )
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 1
    assert "No AI review tools found" in result.output


def test_cli_review_invalid_model(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )
    result = runner.invoke(app, ["review", "-m", "nonexistent"])
    assert result.exit_code == 1
    assert "Unavailable" in result.output


def test_cli_review_success(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )
    review_json = json.dumps({
        "summary": "Looks good overall.",
        "comments": [
            {
                "file": "feature.txt",
                "start_line": 1,
                "end_line": 1,
                "side": "additions",
                "text": "Needs docs.",
                "severity": "suggestion",
            },
        ],
    })
    monkeypatch.setattr(
        "shortcake.commands._review.subprocess.run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=review_json, stderr="",
            )
        ),
    )
    result = runner.invoke(app, ["review", "-m", "claude"])
    assert result.exit_code == 0
    assert "Looks good overall." in result.output
    assert "Needs docs." in result.output
    assert "suggestion" in result.output


def test_cli_review_no_changes(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review when branch has no diff vs parent prints message."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands.review._git_diff_patch",
        lambda *a, **kw: "",
    )
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 0
    assert "No changes to review" in result.output


def test_cli_review_diff_error(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands.review._git_diff_patch",
        lambda *a, **kw: (_ for _ in ()).throw(
            ValueError("diff failed")
        ),
    )
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 1
    assert "diff failed" in result.output


def test_cli_review_with_error_result(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review where the model returns an error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )
    monkeypatch.setattr(
        "shortcake.commands.review._run_review",
        lambda patch, model: ReviewResult(
            model=model,
            error="'claude' exited with code 1: auth error",
        ),
    )
    result = runner.invoke(app, ["review", "-m", "claude"])
    assert result.exit_code == 0
    assert "Error:" in result.output
    assert "auth error" in result.output


def test_cli_review_explicit_branch(
    repo_with_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )
    review_json = json.dumps({
        "summary": "Branch A review.",
        "comments": [],
    })
    monkeypatch.setattr(
        "shortcake.commands._review.subprocess.run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=review_json, stderr="",
            )
        ),
    )
    result = runner.invoke(
        app, ["review", "branch_a", "-m", "claude"],
    )
    assert result.exit_code == 0
    assert "branch_a" in result.output
    assert "Branch A review." in result.output


def test_cli_review_default_models(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no --model is specified, all available models are used."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )
    review_json = json.dumps({"summary": "Auto.", "comments": []})
    monkeypatch.setattr(
        "shortcake.commands._review.subprocess.run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=review_json, stderr="",
            )
        ),
    )
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 0
    assert "claude" in result.output
    assert "Auto." in result.output


def test_cli_review_multiple_models(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review with multiple models selected."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: f"/usr/bin/{cmd}",
    )
    review_json = json.dumps({"summary": "OK.", "comments": []})
    monkeypatch.setattr(
        "shortcake.commands._review.subprocess.run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=review_json, stderr="",
            )
        ),
    )
    result = runner.invoke(
        app, ["review", "-m", "claude", "-m", "codex"],
    )
    assert result.exit_code == 0
    assert "claude" in result.output
    assert "codex" in result.output


def test_cli_review_executor_exception(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _run_review raises, errors are collected."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )

    def boom(patch: str, model: str) -> ReviewResult:
        raise RuntimeError("unexpected crash")

    monkeypatch.setattr(
        "shortcake.commands.review._run_review", boom,
    )
    result = runner.invoke(app, ["review", "-m", "claude"])
    assert result.exit_code == 1
    assert "unexpected crash" in result.output


def test_print_review_result_line_range() -> None:
    """Comments spanning multiple lines show start-end range."""
    from shortcake.commands._review import ReviewComment
    from shortcake.commands.review import _print_review_result

    result = ReviewResult(
        model="claude",
        summary="Summary.",
        comments=[
            ReviewComment(
                file="a.py",
                start_line=5,
                end_line=10,
                side="additions",
                text="multi-line issue",
                severity="warning",
            ),
        ],
    )
    # Just verify it doesn't crash — output goes to typer.echo
    _print_review_result(result)

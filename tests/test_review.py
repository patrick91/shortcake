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
    _parse_model_id,
    _parse_review_response,
    _run_review,
)
from tests._git_helpers import Repo

runner = CliRunner()


# -- _get_available_models ---------------------------------------------------


def test_get_available_models_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    models = _get_available_models()
    ids = [m["id"] for m in models]
    assert "claude:sonnet" in ids
    assert "claude:opus" in ids
    assert "codex:gpt-5.4" in ids
    assert all(m["available"] for m in models)


def test_get_available_models_only_claude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )
    models = _get_available_models()
    claude = [m for m in models if m["tool"] == "claude"]
    codex = [m for m in models if m["tool"] == "codex"]
    assert all(m["available"] for m in claude)
    assert all(not m["available"] for m in codex)


def test_get_available_models_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    models = _get_available_models()
    assert all(not m["available"] for m in models)


# -- _parse_model_id --------------------------------------------------------


def test_parse_model_id_with_variant() -> None:
    assert _parse_model_id("claude:sonnet") == ("claude", "sonnet")


def test_parse_model_id_bare_tool() -> None:
    assert _parse_model_id("claude") == ("claude", None)


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
    assert patch not in prompt
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
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.model == "claude:sonnet"
    assert result.summary == "Looks good."
    assert result.error is None
    assert len(result.comments) == 1
    c = result.comments[0]
    assert c.file == "foo.py"
    assert c.start_line == 10
    assert c.end_line == 12
    assert c.side == "additions"
    assert c.text == "Consider renaming this variable."
    assert c.severity == "suggestion"


def test_parse_review_response_markdown_fences() -> None:
    inner = _make_valid_response(summary="Fenced response.")
    raw = f"```json\n{inner}\n```"
    result = _parse_review_response(raw, "codex:gpt-5.4")
    assert result.model == "codex:gpt-5.4"
    assert result.summary == "Fenced response."
    assert result.error is None


def test_parse_review_response_markdown_fences_no_lang() -> None:
    inner = _make_valid_response(summary="Plain fenced.")
    raw = f"```\n{inner}\n```"
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.summary == "Plain fenced."
    assert result.error is None


def test_parse_review_response_malformed_json() -> None:
    raw = "this is not json at all {{{{"
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.error is not None
    assert "parse" in result.error.lower()


def test_parse_review_response_empty() -> None:
    result = _parse_review_response("", "claude:sonnet")
    assert result.error is not None
    assert "parse" in result.error.lower()


def test_parse_review_response_missing_fields() -> None:
    raw = json.dumps({"unexpected_key": "value"})
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.error is None
    assert result.summary == ""
    assert result.comments == []


def test_parse_review_response_missing_comment_fields() -> None:
    raw = json.dumps({"summary": "OK", "comments": [{"file": "bar.py"}]})
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.error is None
    assert len(result.comments) == 1
    c = result.comments[0]
    assert c.file == "bar.py"
    assert c.start_line == 0


def test_parse_review_response_non_dict_comment_skipped() -> None:
    raw = json.dumps({"summary": "OK", "comments": ["not a dict", 42]})
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.error is None
    assert result.comments == []


def test_parse_review_response_json_in_prose() -> None:
    inner = _make_valid_response(summary="Embedded.")
    raw = f"Here is my review:\n{inner}\nHope that helps!"
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.summary == "Embedded."
    assert result.error is None


def test_parse_review_response_comment_with_bad_types() -> None:
    raw = json.dumps({
        "summary": "OK",
        "comments": [
            {"file": "x.py", "start_line": "not_a_number"},
        ],
    })
    result = _parse_review_response(raw, "claude:sonnet")
    assert result.error is None
    assert result.comments == []


# -- _run_review -------------------------------------------------------------


def test_run_review_claude_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_json = _make_valid_response(summary="Claude review.")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=response_json, stderr="",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "claude")
    assert result.model == "claude"
    assert result.summary == "Claude review."
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    # No --model flag when variant is None
    assert "--model" not in cmd


def test_run_review_claude_with_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_json = _make_valid_response(summary="Opus review.")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=response_json, stderr="",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "claude:opus")
    assert result.model == "claude:opus"
    assert result.summary == "Opus review."
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "claude"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_run_review_codex_with_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_json = _make_valid_response(summary="Codex review.")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=response_json, stderr="",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "codex:gpt-5.4")
    assert result.model == "codex:gpt-5.4"
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "codex"
    assert cmd[1] == "exec"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-5.4"


def test_run_review_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=300)

    monkeypatch.setattr("subprocess.run", raise_timeout)

    result = _run_review("some patch", "claude:sonnet")
    assert result.error is not None
    assert "timed out" in result.error.lower()
    assert "300" in result.error


def test_run_review_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout="", stderr="Something went wrong",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "claude:sonnet")
    assert result.error is not None
    assert "exited with code 1" in result.error
    assert "Something went wrong" in result.error


def test_run_review_nonzero_exit_no_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout="", stderr="",
        )
    )
    monkeypatch.setattr("subprocess.run", mock_run)

    result = _run_review("some patch", "claude:sonnet")
    assert result.error is not None
    assert "exited with code 1" in result.error


def test_run_review_unknown_tool() -> None:
    result = _run_review("some patch", "gpt-unknown")
    assert result.error is not None
    assert "Unknown tool" in result.error


def test_run_review_file_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_fnf(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr("subprocess.run", raise_fnf)

    result = _run_review("some patch", "claude:sonnet")
    assert result.error is not None
    assert "not found" in result.error.lower()


def test_run_review_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_os(*args: object, **kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr("subprocess.run", raise_os)

    result = _run_review("some patch", "claude:sonnet")
    assert result.error is not None
    assert "Failed to run" in result.error


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
    assert "Unknown model" in result.output


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
    result = runner.invoke(app, ["review", "-m", "claude:sonnet"])
    assert result.exit_code == 0
    assert "Looks good overall." in result.output
    assert "Needs docs." in result.output


def test_cli_review_bare_tool_name_resolves(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing -m claude should resolve to claude:sonnet."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
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
    result = runner.invoke(app, ["review", "-m", "claude"])
    assert result.exit_code == 0
    assert "claude:sonnet" in result.output


def test_cli_review_bare_variant_resolves(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing -m opus should resolve to claude:opus."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "shortcake.commands._review.shutil.which",
        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
    )
    review_json = json.dumps({"summary": "Opus.", "comments": []})
    monkeypatch.setattr(
        "shortcake.commands._review.subprocess.run",
        MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=review_json, stderr="",
            )
        ),
    )
    result = runner.invoke(app, ["review", "-m", "opus"])
    assert result.exit_code == 0
    assert "claude:opus" in result.output


def test_cli_review_no_changes(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    """When no --model is specified, first variant per tool is used."""
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
    assert "claude:sonnet" in result.output
    assert "Auto." in result.output


def test_cli_review_multiple_models(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        app,
        ["review", "-m", "claude:sonnet", "-m", "claude:opus"],
    )
    assert result.exit_code == 0
    assert "claude:sonnet" in result.output
    assert "claude:opus" in result.output


def test_cli_review_executor_exception(
    repo_with_tracked_feature: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    from shortcake.commands._review import ReviewComment
    from shortcake.commands.review import _print_review_result

    result = ReviewResult(
        model="claude:sonnet",
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
    _print_review_result(result)

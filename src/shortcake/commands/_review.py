from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field

MAX_PATCH_SIZE = 100_000  # ~100KB


@dataclass(frozen=True)
class ReviewComment:
    file: str
    start_line: int
    end_line: int
    side: str
    text: str
    severity: str


@dataclass(frozen=True)
class ReviewResult:
    model: str
    summary: str = ""
    comments: list[ReviewComment] = field(default_factory=list)
    error: str | None = None


def _get_available_models() -> list[dict]:
    """Check which AI CLI tools are available on the system."""
    return [
        {
            "id": "claude",
            "name": "Claude",
            "available": shutil.which("claude") is not None,
        },
        {
            "id": "codex",
            "name": "Codex",
            "available": shutil.which("codex") is not None,
        },
    ]


def _build_prompt(patch: str) -> str:
    """Construct the review prompt for the LLM.

    Truncates the patch at ~100KB with a note if it exceeds that size.
    """
    if len(patch) > MAX_PATCH_SIZE:
        patch = (
            patch[:MAX_PATCH_SIZE]
            + "\n\n... [patch truncated — too large for review]"
        )

    return (
        "You are an expert code reviewer. Review the following diff and respond "
        "with ONLY valid JSON:\n"
        "{\n"
        '  "summary": "2-4 sentence overview",\n'
        '  "comments": [\n'
        '    {"file": "path", "start_line": N, "end_line": N, '
        '"side": "additions", "text": "...", "severity": "suggestion"}\n'
        "  ]\n"
        "}\n"
        "Rules: line numbers = new side of diff. "
        "severity = info|warning|error|suggestion. "
        "side = additions|deletions. "
        "Only comment on meaningful issues.\n"
        "\n"
        "<diff>\n"
        f"{patch}\n"
        "</diff>"
    )


def _parse_review_response(raw: str, model: str) -> ReviewResult:
    """Parse the JSON response from an LLM review.

    Handles raw JSON, JSON wrapped in markdown code fences, and
    malformed output (returns a ReviewResult with an error message).
    """
    text = raw.strip()

    # Try direct JSON parse first
    parsed = _try_parse_json(text)
    if parsed is not None:
        return _build_result_from_parsed(parsed, model)

    # Try extracting from markdown code fences (```json ... ``` or ``` ... ```)
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        parsed = _try_parse_json(match.group(1).strip())
        if parsed is not None:
            return _build_result_from_parsed(parsed, model)

    # Try finding any JSON object in the text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        parsed = _try_parse_json(match.group(0))
        if parsed is not None:
            return _build_result_from_parsed(parsed, model)

    return ReviewResult(model=model, error="Failed to parse response")


def _try_parse_json(text: str) -> dict | None:
    """Attempt to parse text as JSON, returning None on failure."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _build_result_from_parsed(data: dict, model: str) -> ReviewResult:
    """Build a ReviewResult from a successfully parsed JSON dict."""
    summary = data.get("summary", "")
    raw_comments = data.get("comments", [])

    comments: list[ReviewComment] = []
    for c in raw_comments:
        if not isinstance(c, dict):
            continue
        try:
            comments.append(
                ReviewComment(
                    file=str(c.get("file", "")),
                    start_line=int(c.get("start_line", 0)),
                    end_line=int(c.get("end_line", 0)),
                    side=str(c.get("side", "additions")),
                    text=str(c.get("text", "")),
                    severity=str(c.get("severity", "suggestion")),
                )
            )
        except (TypeError, ValueError):
            continue

    return ReviewResult(model=model, summary=summary, comments=comments)


def _run_review(patch: str, model: str) -> ReviewResult:
    """Shell out to an AI CLI tool to review the patch.

    Supports "claude" and "codex" as model identifiers. Handles
    timeouts and process errors gracefully.
    """
    prompt = _build_prompt(patch)

    if model == "claude":
        cmd = ["claude", "-p", prompt]
    elif model == "codex":
        cmd = ["codex", "exec", prompt]
    else:
        return ReviewResult(model=model, error=f"Unknown model: {model}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return ReviewResult(model=model, error="Review timed out after 300 seconds")
    except FileNotFoundError:
        return ReviewResult(model=model, error=f"'{model}' CLI tool not found")
    except OSError as e:
        return ReviewResult(model=model, error=f"Failed to run '{model}': {e}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return ReviewResult(
            model=model,
            error=f"'{model}' exited with code {result.returncode}: {stderr}",
        )

    return _parse_review_response(result.stdout, model)

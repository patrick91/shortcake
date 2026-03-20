from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field

MAX_PATCH_SIZE = 100_000  # ~100KB

# Known model variants per CLI tool.  The first entry for each tool is
# the default (i.e. what you get when you don't pass --model).
TOOL_MODELS: dict[str, list[dict[str, str]]] = {
    "claude": [
        {"id": "sonnet", "name": "Sonnet 4.6"},
        {"id": "opus", "name": "Opus 4.6"},
        {"id": "haiku", "name": "Haiku"},
    ],
    "codex": [
        {"id": "gpt-5.4", "name": "GPT-5.4"},
        {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini"},
        {"id": "gpt-5.3-codex", "name": "GPT-5.3 Codex"},
    ],
}


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
    fix_prompt: str | None = None


@dataclass(frozen=True)
class ReviewModelInfo:
    """A selectable model option exposed to the UI / CLI."""

    id: str  # e.g. "claude:sonnet"
    name: str  # e.g. "Claude Sonnet"
    tool: str  # "claude" or "codex"
    variant: str  # "sonnet", "o3", etc.
    available: bool


def _get_available_models() -> list[dict]:
    """Return every known model variant with availability info."""
    models: list[dict] = []
    for tool, variants in TOOL_MODELS.items():
        tool_available = shutil.which(tool) is not None
        for v in variants:
            models.append(
                {
                    "id": f"{tool}:{v['id']}",
                    "name": f"{tool.title()} {v['name']}",
                    "tool": tool,
                    "variant": v["id"],
                    "available": tool_available,
                }
            )
    return models


def _build_prompt(patch: str) -> str:
    """Construct the review prompt for the LLM.

    Truncates the patch at ~100KB with a note if it exceeds that size.
    """
    if len(patch) > MAX_PATCH_SIZE:
        patch = (
            patch[:MAX_PATCH_SIZE] + "\n\n... [patch truncated — too large for review]"
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


def _build_synthesis_prompt(
    patch: str,
    reviews: list[ReviewResult],
) -> str:
    """Build a prompt for a final synthesis pass.

    The synthesis model reads the diff and all prior independent reviews,
    then produces a consolidated review that deduplicates, resolves
    disagreements, and surfaces the most important findings.
    """
    if len(patch) > MAX_PATCH_SIZE:
        patch = (
            patch[:MAX_PATCH_SIZE] + "\n\n... [patch truncated — too large for review]"
        )

    prior_section = ""
    for r in reviews:
        if r.error:
            continue
        prior_section += f"\n### {r.model}\n"
        prior_section += f"Summary: {r.summary}\n"
        for c in r.comments:
            prior_section += f"- {c.file}:{c.start_line} [{c.severity}] {c.text}\n"

    return (
        "You are an expert code reviewer performing a final synthesis. "
        "Multiple independent reviewers have already analyzed the diff below. "
        "Your job is to:\n"
        "1. Deduplicate findings — merge overlapping comments\n"
        "2. Resolve disagreements — note when reviewers conflict\n"
        "3. Prioritize — surface the most important issues first\n"
        "4. Add anything the prior reviewers missed\n"
        "5. Write a fix_prompt — a single actionable instruction "
        "that a developer or AI coding agent can follow to fix "
        "all the issues you found\n"
        "\n"
        "Respond with ONLY valid JSON:\n"
        "{\n"
        '  "summary": "2-4 sentence consolidated overview",\n'
        '  "comments": [\n'
        '    {"file": "path", "start_line": N, "end_line": N, '
        '"side": "additions", "text": "...", "severity": "suggestion"}\n'
        "  ],\n"
        '  "fix_prompt": "A concrete, actionable prompt describing '
        "all changes needed to fix the issues above. Reference specific "
        "files and line numbers. This will be pasted directly into an "
        'AI coding tool."\n'
        "}\n"
        "Rules: line numbers = new side of diff. "
        "severity = info|warning|error|suggestion. "
        "side = additions|deletions. "
        "Only include meaningful, deduplicated findings.\n"
        "\n"
        "<prior-reviews>\n"
        f"{prior_section}\n"
        "</prior-reviews>\n"
        "\n"
        "<diff>\n"
        f"{patch}\n"
        "</diff>"
    )


def _run_synthesis(
    patch: str,
    reviews: list[ReviewResult],
    model_id: str,
) -> ReviewResult:
    """Run a final synthesis pass over prior independent reviews."""
    prompt = _build_synthesis_prompt(patch, reviews)
    tool, variant = _parse_model_id(model_id)

    if tool == "claude":
        cmd = ["claude", "-p"]
        if variant:
            cmd += ["--model", variant]
        cmd.append(prompt)
    elif tool == "codex":
        cmd = ["codex", "exec"]
        if variant:
            cmd += ["--model", variant]
        cmd.append(prompt)
    else:
        return ReviewResult(
            model=model_id,
            error=f"Unknown tool: {tool}",
        )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return ReviewResult(
            model=model_id,
            error="Synthesis timed out after 300 seconds",
        )
    except FileNotFoundError:
        return ReviewResult(
            model=model_id,
            error=f"'{tool}' CLI tool not found",
        )
    except OSError as e:
        return ReviewResult(
            model=model_id,
            error=f"Failed to run '{tool}': {e}",
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return ReviewResult(
            model=model_id,
            error=f"'{tool}' exited with code {result.returncode}"
            + (f": {stderr}" if stderr else ""),
        )

    return _parse_review_response(result.stdout, model_id)


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

    fix_prompt = data.get("fix_prompt") or None

    return ReviewResult(
        model=model,
        summary=summary,
        comments=comments,
        fix_prompt=fix_prompt,
    )


def _parse_model_id(model_id: str) -> tuple[str, str | None]:
    """Parse a model ID like 'claude:sonnet' into (tool, variant).

    If no colon, treat the whole string as a tool name with no variant
    (uses the tool's default model).
    """
    if ":" in model_id:
        tool, variant = model_id.split(":", 1)
        return tool, variant
    return model_id, None


def _run_review(patch: str, model_id: str) -> ReviewResult:
    """Shell out to an AI CLI tool to review the patch.

    model_id can be:
      - "claude:sonnet", "claude:opus", "codex:o3", etc.
      - "claude" or "codex" (uses the tool's default model)
    """
    prompt = _build_prompt(patch)
    tool, variant = _parse_model_id(model_id)

    if tool == "claude":
        cmd = ["claude", "-p"]
        if variant:
            cmd += ["--model", variant]
        cmd.append(prompt)
    elif tool == "codex":
        cmd = ["codex", "exec"]
        if variant:
            cmd += ["--model", variant]
        cmd.append(prompt)
    else:
        return ReviewResult(
            model=model_id,
            error=f"Unknown tool: {tool}",
        )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return ReviewResult(
            model=model_id,
            error="Review timed out after 300 seconds",
        )
    except FileNotFoundError:
        return ReviewResult(
            model=model_id,
            error=f"'{tool}' CLI tool not found",
        )
    except OSError as e:
        return ReviewResult(
            model=model_id,
            error=f"Failed to run '{tool}': {e}",
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return ReviewResult(
            model=model_id,
            error=f"'{tool}' exited with code {result.returncode}"
            + (f": {stderr}" if stderr else ""),
        )

    return _parse_review_response(result.stdout, model_id)

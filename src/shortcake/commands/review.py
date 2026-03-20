from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake.commands._review import (
    ReviewResult,
    _get_available_models,
    _run_review,
)
from shortcake.commands.ui import _git_diff_patch, _tracked_branch_parents


def _print_review_result(result: ReviewResult) -> None:
    """Print a single model's review result with formatting."""
    typer.echo(f"=== {result.model} ===")
    typer.echo("")

    if result.error:
        typer.echo(f"Error: {result.error}")
        typer.echo("")
        return

    typer.echo(result.summary)
    typer.echo("")

    # Group comments by file
    comments_by_file: dict[str, list] = {}
    for comment in result.comments:
        file_comments = comments_by_file.setdefault(comment.file, [])
        file_comments.append(comment)

    for file_path, file_comments in comments_by_file.items():
        typer.echo(f"  {file_path}")
        for c in file_comments:
            line_ref = (
                str(c.start_line)
                if c.start_line == c.end_line
                else f"{c.start_line}-{c.end_line}"
            )
            typer.echo(f"    :{line_ref} - [{c.severity}] {c.text}")
        typer.echo("")


def _resolve_model_id(raw: str, available_ids: list[str]) -> str | None:
    """Resolve a user-supplied model string to a full model ID.

    Accepts:
      - Full IDs like "claude:sonnet" -> returned as-is if available
      - Bare tool names like "claude" -> first available variant
      - Bare variant names like "sonnet" -> first matching tool variant

    Returns None if no match is found.
    """
    # Exact match
    if raw in available_ids:
        return raw

    # Bare tool name -> first available variant for that tool
    for mid in available_ids:
        if mid.startswith(f"{raw}:"):
            return mid

    # Bare variant name -> first tool that has it
    for mid in available_ids:
        if mid.endswith(f":{raw}"):
            return mid

    return None


def review(
    branch: Annotated[
        str | None,
        typer.Argument(help="Branch to review (defaults to current)"),
    ] = None,
    model: Annotated[
        list[str] | None,
        typer.Option(
            "--model", "-m",
            help="Models to use (e.g. claude:sonnet, codex:o3)",
        ),
    ] = None,
) -> None:
    """Review a branch's changes using AI models."""
    repo = git.open_repo()

    # Resolve branch
    if branch is None:
        branch = git.get_current_branch(repo)

    # Get tracked branches and find parent
    tracked = _tracked_branch_parents(repo)
    if branch not in tracked:
        typer.echo(
            f"Error: Branch '{branch}' is not tracked by Shortcake.",
            err=True,
        )
        raise typer.Exit(1)

    parent = tracked[branch]

    # Generate diff patch
    try:
        patch = _git_diff_patch(Path(repo.workdir), parent, branch)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    if not patch.strip():
        typer.echo("No changes to review.")
        raise typer.Exit(0)

    # Check available models
    all_models = _get_available_models()
    available_ids = [m["id"] for m in all_models if m["available"]]
    if not available_ids:
        typer.echo(
            "Error: No AI review tools found. "
            "Install 'claude' or 'codex' CLI.",
            err=True,
        )
        raise typer.Exit(1)

    # Determine which models to use
    if model is not None:
        selected_models: list[str] = []
        for m in model:
            resolved = _resolve_model_id(m, available_ids)
            if resolved is None:
                typer.echo(
                    f"Error: Unknown model '{m}'. "
                    f"Available: {', '.join(available_ids)}",
                    err=True,
                )
                raise typer.Exit(1)
            selected_models.append(resolved)
    else:
        # Default: first variant of each available tool
        seen_tools: set[str] = set()
        selected_models = []
        for mid in available_ids:
            tool = mid.split(":")[0]
            if tool not in seen_tools:
                seen_tools.add(tool)
                selected_models.append(mid)

    models_str = ", ".join(selected_models)
    typer.echo(
        f"Reviewing '{branch}' (vs '{parent}') with: {models_str}",
    )
    typer.echo("")

    # Run reviews in parallel
    results: list[ReviewResult] = []
    errors: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=len(selected_models)) as executor:
        future_to_model = {
            executor.submit(_run_review, patch, m): m
            for m in selected_models
        }

        for future in as_completed(future_to_model):
            model_name = future_to_model[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                errors.append((model_name, str(e)))

    # Sort results by model name for consistent output
    results.sort(key=lambda r: r.model)

    # Print results
    for result in results:
        _print_review_result(result)

    # Print errors
    for model_name, error_msg in sorted(errors):
        typer.echo(f"Error from {model_name}: {error_msg}", err=True)

    if errors and not results:
        raise typer.Exit(1)

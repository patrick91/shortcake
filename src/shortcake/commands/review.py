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


def review(
    branch: Annotated[
        str | None,
        typer.Argument(help="Branch to review (defaults to current)"),
    ] = None,
    model: Annotated[
        list[str] | None,
        typer.Option("--model", "-m", help="Models to use (e.g. claude, codex)"),
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
        typer.echo(f"Error: Branch '{branch}' is not tracked by Shortcake.", err=True)
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
            "Error: No AI review tools found. Install 'claude' or 'codex' CLI.",
            err=True,
        )
        raise typer.Exit(1)

    # Determine which models to use
    if model is not None:
        invalid = [m for m in model if m not in available_ids]
        if invalid:
            typer.echo(
                f"Error: Unavailable model(s): {', '.join(invalid)}. "
                f"Available: {', '.join(available_ids)}",
                err=True,
            )
            raise typer.Exit(1)
        selected_models = model
    else:
        selected_models = available_ids

    typer.echo(f"Reviewing '{branch}' (vs '{parent}') with: {', '.join(selected_models)}")
    typer.echo("")

    # Run reviews in parallel
    results: list[ReviewResult] = []
    errors: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=len(selected_models)) as executor:
        future_to_model = {
            executor.submit(_run_review, patch, m): m for m in selected_models
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

from __future__ import annotations

import json
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake._recap import (
    RecapError,
    build_recap_context,
    create_recap,
    delete_recap,
    list_recaps,
    load_recap,
    recap_component_schema_payload,
    stored_recap_payload,
    validate_recap,
    validated_recap_payload,
)
from shortcake.commands.ui import (
    _find_open_port,
    _open_or_start_static_ui,
    _resolve_dev_web_port,
    _resolve_frontend_dir,
    _resolve_js_runtime,
    _resolve_ui_port,
    _run_dev_server,
    _run_install,
    _start_api_server_on_available_port,
)

recap = typer.Typer(no_args_is_help=True)


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def _read_mdx_arg(value: str) -> tuple[str, Path | None]:
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        return path.read_text(), path

    path = Path(value).expanduser()
    if path.exists():
        return path.read_text(), path

    return value, None


@recap.command("context")
def recap_context(
    branch: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Git base revision to diff against the current branch, or a "
                "tracked branch to recap. Defaults to the tracked parent or "
                "default branch."
            )
        ),
    ] = None,
    working: Annotated[
        bool,
        typer.Option("--working", help="Recap current uncommitted working changes."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable context JSON."),
    ] = False,
) -> None:
    """Build source context and an MDX template for a local visual recap."""
    repo = git.open_repo()
    try:
        payload = build_recap_context(repo, branch=branch, working=working)
    except RecapError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if json_output:
        _echo_json(payload)
    else:
        typer.echo(payload["template"])


@recap.command("create")
def recap_create(
    mdx: Annotated[
        str,
        typer.Option(
            "--mdx",
            help="MDX content, path, or @path to validate and store.",
        ),
    ],
) -> None:
    """Validate and store a local recap MDX document."""
    repo = git.open_repo()
    try:
        mdx_text, mdx_path = _read_mdx_arg(mdx)
        stored = create_recap(repo, mdx_text, mdx_path=mdx_path)
    except (OSError, RecapError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    _echo_json(stored_recap_payload(stored))


@recap.command("validate")
def recap_validate(
    mdx: Annotated[
        str,
        typer.Option(
            "--mdx",
            help="MDX content, path, or @path to validate without storing.",
        ),
    ],
) -> None:
    """Validate a local recap MDX document without storing it."""
    repo = git.open_repo()
    try:
        mdx_text, mdx_path = _read_mdx_arg(mdx)
        validated = validate_recap(repo, mdx_text, mdx_path=mdx_path)
    except (OSError, RecapError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    _echo_json(validated_recap_payload(validated))


@recap.command("show")
def recap_show(
    recap_id: Annotated[str, typer.Argument(help="Local recap id.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable recap JSON."),
    ] = False,
) -> None:
    """Show a stored local recap."""
    repo = git.open_repo()
    try:
        stored = load_recap(repo, recap_id)
    except (FileNotFoundError, RecapError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    payload = stored_recap_payload(stored)
    if json_output:
        _echo_json(payload)
        return

    typer.echo(f"{payload['id']} {payload['title']}")


@recap.command("components")
def recap_components(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable component schemas."),
    ] = False,
) -> None:
    """Show supported recap components, props, enums, and examples."""
    payload = recap_component_schema_payload()
    if json_output:
        _echo_json(payload)
        return

    for component in payload["components"]:
        typer.echo(f"<{component['name']}>")
        required = ", ".join(component["requiredProps"]) or "none"
        optional = ", ".join(component["optionalProps"]) or "none"
        typer.echo(f"  required props: {required}")
        typer.echo(f"  optional props: {optional}")
        typer.echo(f"  example: {component['example']}")

    annotation = payload["annotation"]
    typer.echo("")
    typer.echo("Annotation")
    typer.echo(f"  required: {', '.join(annotation['required'])}")
    typer.echo(f"  optional: {', '.join(annotation['optional'])}")
    typer.echo(f"  side values: {', '.join(annotation['sideValues'])}")
    typer.echo(f"  severity values: {', '.join(annotation['severityValues'])}")
    typer.echo(f"  line semantics: {annotation['lineSemantics']}")
    typer.echo("")
    typer.echo(f"Quoting: {payload['quoting']}")


@recap.command("delete")
def recap_delete(
    recap_id: Annotated[str, typer.Argument(help="Local recap id to delete.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable delete result."),
    ] = False,
) -> None:
    """Delete a stored local recap."""
    repo = git.open_repo()
    try:
        meta = delete_recap(repo, recap_id)
    except (FileNotFoundError, RecapError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    if json_output:
        _echo_json({"deleted": meta.id, "title": meta.title})
        return

    typer.echo(f"Deleted recap {meta.id}")


@recap.command("list")
def recap_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable recap list JSON."),
    ] = False,
) -> None:
    """List stored local recaps."""
    repo = git.open_repo()
    payload = {
        "recaps": [
            meta.model_dump(mode="json", by_alias=True) for meta in list_recaps(repo)
        ]
    }
    if json_output:
        _echo_json(payload)
        return

    if not payload["recaps"]:
        typer.echo("No local recaps found.")
        return

    for item in payload["recaps"]:
        typer.echo(f"{item['id']} {item['title']}")


@recap.command("open")
def recap_open(
    recap_id: Annotated[str, typer.Argument(help="Local recap id.")],
    host: Annotated[
        str,
        typer.Option(help="Host for API and Vite dev server."),
    ] = "127.0.0.1",
    ui_port: Annotated[
        int | None,
        typer.Option(
            "--ui-port",
            "--api-port",
            help=(
                "Port for the built Shortcake UI/API server. Defaults to "
                "SHORTCAKE_UI_PORT, git config shortcake.uiPort, or 8765."
            ),
        ),
    ] = None,
    web_port: Annotated[
        int | None,
        typer.Option(help="Port for Vite React dev server when --dev is used."),
    ] = None,
    skip_install: Annotated[
        bool,
        typer.Option(
            "--skip-install",
            help="Skip 'bun install'/'pybun install' before --dev or --build-ui.",
        ),
    ] = False,
    dev: Annotated[
        bool,
        typer.Option("--dev", help="Run the Vite dev server instead of built assets."),
    ] = False,
    build_ui: Annotated[
        bool,
        typer.Option("--build-ui", help="Build the static UI once before serving it."),
    ] = False,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            help="Start the built UI server in a detached background process.",
        ),
    ] = False,
) -> None:
    """Open a stored recap in the local Shortcake UI."""
    repo = git.open_repo()
    repo_path = Path(repo.workdir)
    try:
        load_recap(repo, recap_id)
    except (FileNotFoundError, RecapError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    selected_ui_port = _resolve_ui_port(repo, ui_port)
    route_hash = f"#/recap/{recap_id}"
    if not dev:
        _open_or_start_static_ui(
            repo,
            host=host,
            port=selected_ui_port,
            route_hash=route_hash,
            open_browser=True,
            build_ui=build_ui,
            skip_install=skip_install,
            background=background,
            label="recap",
        )
        return

    if background:
        typer.echo("Error: --background is only supported for the built UI.", err=True)
        raise typer.Exit(1)

    frontend_dir = _resolve_frontend_dir(repo_path)
    if frontend_dir is None:
        typer.echo("Error: frontend directory not found.", err=True)
        raise typer.Exit(1)

    runtime = _resolve_js_runtime()
    if runtime is None:
        typer.echo(
            "Error: Neither 'pybun' nor 'bun' was found in PATH. Install bun or pybun.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        server, selected_api_port = _start_api_server_on_available_port(
            repo_path.resolve(),
            host,
            selected_ui_port,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    default_web_port = _resolve_dev_web_port(repo, web_port)
    selected_web_port = _find_open_port(host, default_web_port)
    api_origin = f"http://{host}:{selected_api_port}"
    url = f"http://{host}:{selected_web_port}/{route_hash}"

    if selected_api_port != selected_ui_port:
        typer.echo(
            f"Port {selected_ui_port} is in use, using {selected_api_port} for API."
        )
    if selected_web_port != default_web_port:
        typer.echo(
            f"Port {default_web_port} is in use, using {selected_web_port} for UI."
        )

    typer.echo(f"Recap API running at {api_origin}")
    typer.echo(f"Opening recap at {url}")
    typer.echo("Press Ctrl+C to stop.")

    try:
        if not skip_install:
            runtime = _run_install(runtime, frontend_dir)
        webbrowser.open(url)
        result = _run_dev_server(
            runtime,
            frontend_dir,
            host,
            selected_web_port,
            api_origin,
            False,
        )
        if result not in (0, 130):
            raise typer.Exit(result)
    except KeyboardInterrupt:
        raise typer.Exit(0) from None
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    finally:
        server.shutdown()
        server.server_close()

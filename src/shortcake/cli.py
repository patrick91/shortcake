import typer
from typing import Annotated
from shortcake import _git as git
from shortcake.commands import adopt as adopt_cmd

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Shortcake - Stacked PR management tool."""
    pass


@app.command()
def adopt(
    branch: Annotated[str | None, typer.Argument()] = None,
    parent: Annotated[str | None, typer.Option("--parent", "-p")] = None,
) -> None:
    """Track an existing branch by adding Shortcake-Parent trailer."""
    repo = git.open_repo()
    result = adopt_cmd.adopt(repo, branch, parent)

    if not result.success:
        typer.echo(f"Error: {result.error}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Adopted '{result.branch}' with parent '{result.parent}'")


if __name__ == "__main__":
    app()

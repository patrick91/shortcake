"""CLI module for shortcake."""

import typer

app = typer.Typer(help="Shortcake CLI - A CLI built with typer and uv")


@app.command()
def hello(name: str = typer.Option("World", help="Name to greet")):
    """Say hello to someone."""
    typer.echo(f"Hello {name}!")


@app.command()
def version():
    """Show the version."""
    from shortcake import __version__
    typer.echo(f"Shortcake version {__version__}")


if __name__ == "__main__":
    app()

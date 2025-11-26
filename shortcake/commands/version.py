import typer

from shortcake import __version__

app = typer.Typer()


@app.command()
def version():
    """Show the version."""
    typer.echo(f"Shortcake version {__version__}")

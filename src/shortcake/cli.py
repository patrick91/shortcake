import typer

from shortcake.commands.adopt import adopt

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Shortcake - Stacked PR management tool."""
    pass


app.command()(adopt)

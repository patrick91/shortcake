import typer

from shortcake.commands.adopt import adopt
from shortcake.commands.ls import ls

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Shortcake - Stacked PR management tool."""
    pass


app.command()(adopt)
app.command()(ls)

import typer

from shortcake.commands.adopt import adopt
from shortcake.commands.bottom import bottom
from shortcake.commands.create import create
from shortcake.commands.down import down
from shortcake.commands.ls import ls
from shortcake.commands.modify import modify
from shortcake.commands.top import top
from shortcake.commands.up import up

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Shortcake - Stacked PR management tool."""
    pass


app.command()(adopt)
app.command()(bottom)
app.command()(create)
app.command()(down)
app.command()(ls)
app.command()(modify)
app.command()(top)
app.command()(up)

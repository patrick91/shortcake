import typer

from shortcake.commands.abort import abort
from shortcake.commands.adopt import adopt
from shortcake.commands.bottom import bottom
from shortcake.commands.checkout import checkout, co
from shortcake.commands.continue_ import continue_cmd
from shortcake.commands.create import create
from shortcake.commands.down import down
from shortcake.commands.fold import fold
from shortcake.commands.log import log
from shortcake.commands.ls import ls
from shortcake.commands.modify import modify
from shortcake.commands.pull import pull
from shortcake.commands.reorder import reorder
from shortcake.commands.restack import restack
from shortcake.commands.submit import submit
from shortcake.commands.sync import sync
from shortcake.commands.top import top
from shortcake.commands.ui import ui
from shortcake.commands.up import up

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Shortcake - Stacked PR management tool."""
    pass


app.command()(abort)
app.command()(adopt)
app.command()(bottom)
app.command()(checkout)
app.command(name="co")(co)
app.command(name="continue")(continue_cmd)
app.command()(create)
app.command()(down)
app.command()(fold)
app.command()(log)
app.command()(ls)
app.command()(modify)
app.command()(pull)
app.command()(reorder)
app.command()(restack)
app.command()(submit)
app.command()(sync)
app.command()(top)
app.command()(ui)
app.command()(up)

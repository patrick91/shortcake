"""CLI module for shortcake."""

import typer

from shortcake.commands import adopt, config_cmd, create, edit, ls, version

app = typer.Typer(help="Shortcake CLI - A CLI built with typer and uv")

# Register commands
app.command()(version.version)
app.command()(create.create)
app.command()(edit.edit)
app.command(name="modify")(edit.modify)
app.command(name="config")(config_cmd.config_cmd)
app.command()(ls.ls)
app.command()(adopt.adopt)


if __name__ == "__main__":
    app()

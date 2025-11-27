import typer

from shortcake.commands import (
    adopt,
    config,
    create,
    edit,
    ls,
    nav,
    restack,
    split,
    submit,
    sync,
    version,
)

app = typer.Typer()

# Register commands
app.command()(version.version)
app.command()(create.create)
app.command()(edit.edit)
app.command(name="modify")(edit.modify)
app.command(name="config")(config.config_cmd)
app.command()(ls.ls)
app.command()(adopt.adopt)
app.command(name="add")(adopt.adopt)
app.command()(sync.sync)
app.command()(submit.submit)
app.command()(restack.restack)
app.command()(split.split)

# Navigation commands
app.command()(nav.up)
app.command()(nav.down)
app.command()(nav.top)
app.command()(nav.bottom)
app.command()(nav.checkout)
app.command(name="co")(nav.checkout)


if __name__ == "__main__":
    app()

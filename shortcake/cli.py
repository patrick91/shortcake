import typer

from shortcake.commands import adopt, config, create, edit, ls, restack, submit, sync, version

app = typer.Typer()

# Register commands
app.command()(version.version)
app.command()(create.create)
app.command()(edit.edit)
app.command(name="modify")(edit.modify)
app.command(name="config")(config.config_cmd)
app.command()(ls.ls)
app.command()(adopt.adopt)
app.command()(sync.sync)
app.command()(submit.submit)
app.command()(restack.restack)


if __name__ == "__main__":
    app()

import typer

from shortcake import config

app = typer.Typer()


@app.command(name="config")
def config_cmd(
    action: str = typer.Argument(..., help="Action to perform: 'get', 'set', or 'list'"),
    key: str = typer.Argument(None, help="Configuration key (e.g., 'keep_emoji')"),
    value: str = typer.Argument(None, help="Configuration value (for 'set' action)"),
):
    """Manage shortcake configuration.

    Examples:
        shortcake config list - List all configuration settings
        shortcake config get keep_emoji - Get a specific setting
        shortcake config set keep_emoji true - Set a configuration value
    """
    if action == "list":
        # List all configuration settings
        cfg = config.load_config()
        typer.echo("Current configuration:")
        for field_name, field_value in cfg.model_dump().items():
            typer.echo(f"  {field_name} = {field_value}")
        typer.echo(f"\nConfiguration file: {config.get_config_path()}")

    elif action == "get":
        if not key:
            typer.echo("Error: Key is required for 'get' action", err=True)
            raise typer.Exit(1)

        cfg = config.load_config()
        cfg_dict = cfg.model_dump()
        if key in cfg_dict:
            typer.echo(f"{key} = {cfg_dict[key]}")
        else:
            typer.echo(f"Configuration key '{key}' not found")
            typer.echo(f"Available keys: {', '.join(cfg_dict.keys())}")

    elif action == "set":
        if not key or value is None:
            typer.echo("Error: Both key and value are required for 'set' action", err=True)
            raise typer.Exit(1)

        # Handle boolean values
        if key == "keep_emoji":
            if value.lower() in ("true", "1", "yes"):
                config.set_keep_emoji(True)
                typer.echo(f"Set {key} = true")
            elif value.lower() in ("false", "0", "no"):
                config.set_keep_emoji(False)
                typer.echo(f"Set {key} = false")
            else:
                typer.echo(f"Error: Invalid value for {key}. Use 'true' or 'false'", err=True)
                raise typer.Exit(1)
        else:
            typer.echo(f"Error: Unknown configuration key '{key}'", err=True)
            cfg = config.load_config()
            typer.echo(f"Available keys: {', '.join(cfg.model_dump().keys())}")
            raise typer.Exit(1)

    else:
        typer.echo(f"Error: Unknown action '{action}'. Use 'list', 'get', or 'set'", err=True)
        raise typer.Exit(1)

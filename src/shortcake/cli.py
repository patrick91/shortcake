"""Shortcake CLI.

Commands are imported when they run, not when the CLI starts. Importing all of
them up front meant every invocation paid for every command: `sc ls` pulled in
`httpx` and `yaml` through the seven GitHub-touching commands it never calls,
which was most of the startup cost.
"""

import importlib

import typer
from click import Command, Context
from typer.core import TyperGroup
from typer.models import CommandInfo

#: command name -> (module, attribute). Kept as data so nothing is imported
#: until `get_command` is asked for it.
COMMANDS: dict[str, tuple[str, str]] = {
    "abort": ("shortcake.commands.abort", "abort"),
    "adopt": ("shortcake.commands.adopt", "adopt"),
    "bottom": ("shortcake.commands.bottom", "bottom"),
    "checkout": ("shortcake.commands.checkout", "checkout"),
    "co": ("shortcake.commands.checkout", "co"),
    "continue": ("shortcake.commands.continue_", "continue_cmd"),
    "create": ("shortcake.commands.create", "create"),
    "down": ("shortcake.commands.down", "down"),
    "fold": ("shortcake.commands.fold", "fold"),
    "log": ("shortcake.commands.log", "log"),
    "ls": ("shortcake.commands.ls", "ls"),
    "modify": ("shortcake.commands.modify", "modify"),
    "move": ("shortcake.commands.move", "move"),
    "pull": ("shortcake.commands.pull", "pull"),
    "reorder": ("shortcake.commands.reorder", "reorder"),
    "restack": ("shortcake.commands.restack", "restack"),
    "review": ("shortcake.commands.review", "review"),
    "skill": ("shortcake.commands.skill", "skill"),
    "split": ("shortcake.commands.split", "split"),
    "submit": ("shortcake.commands.submit", "submit"),
    "sync": ("shortcake.commands.sync", "sync"),
    "top": ("shortcake.commands.top", "top"),
    "ui": ("shortcake.commands.ui", "ui"),
    "up": ("shortcake.commands.up", "up"),
}

#: Commands that are their own Typer app rather than a single function.
SUB_APPS: dict[str, tuple[str, str]] = {
    "recap": ("shortcake.commands.recap", "recap"),
}


class LazyGroup(TyperGroup):
    """Imports a command's module the first time that command is asked for.

    `--help` still lists everything, and Click asks each command for its short
    help, so help costs what eager loading used to. Running a single command —
    the common case — imports only what that command needs.
    """

    def list_commands(self, ctx: Context) -> list[str]:
        return sorted({*COMMANDS, *SUB_APPS})

    def get_command(self, ctx: Context, cmd_name: str) -> Command | None:
        if cmd_name in SUB_APPS:
            module, attribute = SUB_APPS[cmd_name]
            sub_app = getattr(importlib.import_module(module), attribute)
            command = typer.main.get_command(sub_app)
            command.name = cmd_name
            return command

        if cmd_name not in COMMANDS:
            return None

        module, attribute = COMMANDS[cmd_name]
        callback = getattr(importlib.import_module(module), attribute)
        return typer.main.get_command_from_info(
            CommandInfo(name=cmd_name, callback=callback),
            pretty_exceptions_short=app.pretty_exceptions_short,
            rich_markup_mode=app.rich_markup_mode,
        )


app = typer.Typer(no_args_is_help=True, cls=LazyGroup)


@app.callback()
def main() -> None:
    """Shortcake - Stacked PR management tool."""
    pass

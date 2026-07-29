from typing import Any, NoReturn

import typer
from rich_toolkit import RichToolkit

from shortcake._stack_view import (
    AppendStackView,
    LiveStackView,
    SilentStackView,
    StackRenderer,
    StackRow,
)


class ShortcakeRichToolkit(RichToolkit):
    """Toolkit with a stable success/error envelope for JSON output.

    In JSON mode a command emits exactly one JSON document on stdout:
    ``{"data": ...}`` (plus optional ``"warnings"``) on success, or
    ``{"error": {"code": ..., "message": ..., "hint": ...}}`` on failure.
    """

    def echo(self, message: str = "", *, err: bool = False) -> None:
        """typer.echo-compatible progress print, silent in JSON mode.

        Unlike print(), rich markup is NOT interpreted — messages render
        verbatim (branch names and paths may contain square brackets).
        """
        if self.mode == "json":
            return
        typer.echo(message, err=err)

    def success(self, data: Any, *, warnings: list[str] | None = None) -> None:
        if self.mode != "json":
            self.output(data)
            return

        document: dict[str, Any] = {"data": data}
        if warnings:
            document["warnings"] = warnings
        self.output(document)

    def fail(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        exit_code: int = 1,
    ) -> NoReturn:
        if self.mode == "json":
            error: dict[str, Any] = {"code": code, "message": message}
            if hint is not None:
                error["hint"] = hint
            self.output({"error": error})
        else:
            typer.echo(f"Error: {message}", err=True)
            if hint is not None:
                typer.echo(f"hint: {hint}", err=True)
        raise typer.Exit(exit_code)

    def stack_view(
        self,
        rows: list[StackRow],
        header: str,
        *,
        planning: bool = False,
    ) -> tuple[LiveStackView | AppendStackView | SilentStackView, StackRenderer]:
        """Build the stack progress view for the current mode.

        Returns the view and its renderer; commands mutate the rows and call
        ``view.sync()``. JSON mode gets a silent view, and a non-TTY gets the
        append-only one because ``Live`` emits nothing until it stops when the
        output is piped.
        """
        renderer = StackRenderer(rows, header, self.console, planning=planning)
        if self.mode == "json":
            return SilentStackView(renderer), renderer
        if not self.console.is_terminal:
            return AppendStackView(renderer, self.console), renderer
        return LiveStackView(renderer, self.console), renderer


def get_rich_toolkit(*, json_output: bool = False) -> ShortcakeRichToolkit:
    """Build the toolkit for a command, in human or JSON mode."""
    return ShortcakeRichToolkit(mode="json" if json_output else "human")

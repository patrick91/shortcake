"""Consistent output formatting for CLI messages."""

from rich.console import Console

# Use stderr for errors/warnings so they don't interfere with piped output
console = Console(stderr=True)


def print_error(message: str) -> None:
    """Print an error message in red."""
    console.print(f"[bold red]Error:[/] {message}")


def print_warning(message: str) -> None:
    """Print a warning message in yellow."""
    console.print(f"[bold yellow]Warning:[/] {message}")

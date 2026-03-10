from dataclasses import dataclass

from shortcake._constants import TRAILER_KEY


def _is_trailer_line(line: str) -> bool:
    if ": " not in line:
        return False
    key, _ = line.split(": ", 1)
    return bool(key) and all(ch.isalnum() or ch == "-" for ch in key)


def _split_trailer_block(message: str) -> tuple[str, list[str]]:
    """Split a commit message into body text and trailing trailer lines."""
    lines = message.rstrip("\n").split("\n")
    if not lines or lines == [""]:
        return message.rstrip("\n"), []

    trailer_start = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if _is_trailer_line(line):
            trailer_start = i
            continue
        if line.strip() == "" and trailer_start < len(lines):
            body = "\n".join(lines[:i]).rstrip()
            return body, lines[trailer_start:]
        break

    return message.rstrip("\n"), []


@dataclass
class Trailers:
    parent_branch: str | None = None

    @classmethod
    def from_message(cls, message: str) -> "Trailers":
        """Parse trailers from commit message."""
        _, trailer_lines = _split_trailer_block(message)
        parent_branch = None
        for line in trailer_lines:
            if line.startswith(f"{TRAILER_KEY}: "):
                parent_branch = line[len(TRAILER_KEY) + 2 :]
        return cls(parent_branch=parent_branch)

    def apply_to(self, message: str) -> str:
        """Add trailers to message."""
        if self.parent_branch is None:
            return message

        body, trailer_lines = _split_trailer_block(message)
        preserved_trailers = [
            line for line in trailer_lines if not line.startswith(f"{TRAILER_KEY}: ")
        ]
        preserved_trailers.append(f"{TRAILER_KEY}: {self.parent_branch}")

        if body:
            return f"{body}\n\n" + "\n".join(preserved_trailers)
        return "\n".join(preserved_trailers)

    def remove_from(self, message: str) -> str:
        """Remove Shortcake trailers from message.

        Returns the message with Shortcake-Parent trailer removed.
        """
        return strip_trailers(message)


def strip_trailers(message: str) -> str:
    """Remove Shortcake trailer block from message for editing.

    Returns the message with the trailing Shortcake-Parent trailer removed,
    so users don't see or accidentally modify it in the editor.
    """
    lines = message.rstrip().split("\n")

    # Find the last non-empty line that's a Shortcake trailer
    trailer_start = None
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if line.startswith(f"{TRAILER_KEY}: "):
            trailer_start = i
        elif line.strip() == "":
            # Found blank line before trailers, include it in removal
            if trailer_start is not None:
                trailer_start = i
            break
        else:
            # Non-trailer, non-blank line - stop searching
            break

    if trailer_start is None:
        return message

    # Return message without the trailer block
    return "\n".join(lines[:trailer_start]).rstrip()

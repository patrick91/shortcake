from dataclasses import dataclass

from dulwich import porcelain

from shortcake._constants import TRAILER_KEY


@dataclass
class Trailers:
    parent_branch: str | None = None

    @classmethod
    def from_message(cls, message: str) -> "Trailers":
        """Parse trailers from commit message."""
        result = porcelain.interpret_trailers(
            message, only_trailers=True, only_input=True
        )
        parent_branch = None
        for line in result.decode().strip().split("\n"):
            if line.startswith(f"{TRAILER_KEY}: "):
                parent_branch = line[len(TRAILER_KEY) + 2 :]
        return cls(parent_branch=parent_branch)

    def apply_to(self, message: str) -> str:
        """Add trailers to message."""
        trailers: list[tuple[str, str]] = []
        if self.parent_branch is not None:
            trailers.append((TRAILER_KEY, self.parent_branch))
        if not trailers:
            return message
        result = porcelain.interpret_trailers(message, trailers=trailers)
        return result.decode()


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

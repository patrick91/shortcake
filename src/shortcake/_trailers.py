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

from dulwich import porcelain


def get_trailer(message: str, key: str) -> str | None:
    """Extract trailer value from commit message."""
    result = porcelain.interpret_trailers(message, only_trailers=True, only_input=True)
    for line in result.decode().strip().split("\n"):
        if line.startswith(f"{key}: "):
            return line[len(key) + 2 :]
    return None


def add_trailer(message: str, key: str, value: str) -> str:
    """Add trailer to commit message."""
    result = porcelain.interpret_trailers(message, trailers=[(key, value)])
    return result.decode()

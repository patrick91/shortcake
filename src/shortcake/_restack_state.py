from pathlib import Path

from dulwich.repo import Repo
from pydantic import BaseModel

STATE_FILE = "shortcake-restack.json"
STATE_VERSION = 1


class RestackStep(BaseModel):
    """A single rebase operation in the restack plan."""

    branch: str
    onto: str
    merge_base: str


class RestackState(BaseModel):
    """State of an in-progress restack operation."""

    version: int
    original_branch: str
    plan: list[RestackStep]
    current_index: int
    original_refs: dict[str, str] = {}

    @classmethod
    def load(cls, repo: Repo) -> "RestackState | None":
        """Load state from .git/shortcake-restack.json, or None if not exists."""
        state_path = Path(repo.controldir()) / STATE_FILE
        if not state_path.exists():
            return None
        return cls.model_validate_json(state_path.read_bytes())

    def save(self, repo: Repo) -> None:
        """Save state to .git/shortcake-restack.json."""
        state_path = Path(repo.controldir()) / STATE_FILE
        state_path.write_bytes(self.model_dump_json(indent=2).encode())

    def delete(self, repo: Repo) -> None:
        """Delete state file."""
        state_path = Path(repo.controldir()) / STATE_FILE
        if state_path.exists():
            state_path.unlink()

    @staticmethod
    def exists(repo: Repo) -> bool:
        """Check if state file exists."""
        state_path = Path(repo.controldir()) / STATE_FILE
        return state_path.exists()

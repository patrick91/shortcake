import json
from dataclasses import dataclass, field
from pathlib import Path

from dulwich.repo import Repo

STATE_FILE = "shortcake-restack.json"
STATE_VERSION = 1


@dataclass
class RestackStep:
    """A single rebase operation in the restack plan."""

    branch: str
    onto: str
    merge_base: str


@dataclass
class RestackState:
    """State of an in-progress restack operation."""

    version: int
    original_branch: str
    plan: list[RestackStep]
    current_index: int
    original_refs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, repo: Repo) -> "RestackState | None":
        """Load state from .git/shortcake-restack.json, or None if not exists."""
        state_path = Path(repo.controldir()) / STATE_FILE
        if not state_path.exists():
            return None

        data = json.loads(state_path.read_text())
        return cls(
            version=data["version"],
            original_branch=data["original_branch"],
            plan=[
                RestackStep(
                    branch=step["branch"],
                    onto=step["onto"],
                    merge_base=step["merge_base"],
                )
                for step in data["plan"]
            ],
            current_index=data["current_index"],
            original_refs=data.get("original_refs", {}),
        )

    def save(self, repo: Repo) -> None:
        """Save state to .git/shortcake-restack.json."""
        state_path = Path(repo.controldir()) / STATE_FILE
        data = {
            "version": self.version,
            "original_branch": self.original_branch,
            "plan": [
                {
                    "branch": step.branch,
                    "onto": step.onto,
                    "merge_base": step.merge_base,
                }
                for step in self.plan
            ],
            "current_index": self.current_index,
            "original_refs": self.original_refs,
        }
        state_path.write_text(json.dumps(data, indent=2))

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

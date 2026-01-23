"""Tests for restack state persistence."""

import re

from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake._restack_state import STATE_VERSION, RestackState, RestackStep

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_restack_state_save_load(temp_repo: Repo) -> None:
    """Test state save and load roundtrip."""
    state = RestackState(
        version=STATE_VERSION,
        original_branch="branch_c",
        plan=[
            RestackStep(branch="branch_a", onto="main", merge_base="abc123"),
            RestackStep(branch="branch_b", onto="branch_a", merge_base="def456"),
        ],
        current_index=1,
        original_refs={
            "branch_a": "aaa111",
            "branch_b": "bbb222",
        },
    )

    state.save(temp_repo)
    loaded = RestackState.load(temp_repo)

    assert loaded is not None
    assert loaded.version == state.version
    assert loaded.original_branch == state.original_branch
    assert len(loaded.plan) == 2
    assert loaded.plan[0].branch == "branch_a"
    assert loaded.plan[0].onto == "main"
    assert loaded.plan[1].branch == "branch_b"
    assert loaded.current_index == 1
    assert loaded.original_refs == state.original_refs


def test_restack_state_delete(temp_repo: Repo) -> None:
    """Test state deletion."""
    state = RestackState(
        version=STATE_VERSION,
        original_branch="test",
        plan=[],
        current_index=0,
        original_refs={},
    )

    state.save(temp_repo)
    assert RestackState.exists(temp_repo)

    state.delete(temp_repo)
    assert not RestackState.exists(temp_repo)


def test_restack_state_load_nonexistent(temp_repo: Repo) -> None:
    """Test loading when no state file exists."""
    assert RestackState.load(temp_repo) is None

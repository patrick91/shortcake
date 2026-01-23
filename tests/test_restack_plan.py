"""Tests for restack planning functions."""

import re

from dulwich.repo import Repo
from typer.testing import CliRunner

from shortcake.commands.restack import (
    _get_stack_in_order,
    _needs_restack,
    _plan_restack,
)

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_needs_restack_up_to_date(repo_with_tracked_feature: Repo) -> None:
    """Branch doesn't need restack when parent hasn't changed."""
    assert not _needs_restack(repo_with_tracked_feature, "feature", "main")


def test_needs_restack_behind(repo_with_stack_behind: Repo) -> None:
    """Branch needs restack when parent has new commits."""
    assert _needs_restack(repo_with_stack_behind, "branch_a", "main")


# ============================================================================
# Unit Tests: _get_stack_in_order
# ============================================================================


def test_get_stack_in_order_linear(repo_with_stack: Repo) -> None:
    """Get topological order for linear stack."""
    order = _get_stack_in_order(repo_with_stack, "branch_b")
    assert order == ["branch_a", "branch_b"]


def test_get_stack_in_order_forked(repo_with_fork: Repo) -> None:
    """Get topological order for forked stack."""
    order = _get_stack_in_order(repo_with_fork, "branch_c")
    assert order[0] == "branch_a"
    # branch_b and branch_c can be in any order after branch_a
    assert set(order[1:]) == {"branch_b", "branch_c"}


# ============================================================================
# Unit Tests: _plan_restack
# ============================================================================


def test_plan_restack_nothing_to_do(repo_with_stack: Repo) -> None:
    """No plan when everything up to date."""
    branches = _get_stack_in_order(repo_with_stack, "branch_b")
    plan = _plan_restack(repo_with_stack, branches)
    assert plan == []


def test_plan_restack_linear_stack(repo_with_stack_behind: Repo) -> None:
    """Plan includes branches that need rebasing."""
    branches = _get_stack_in_order(repo_with_stack_behind, "branch_b")
    plan = _plan_restack(repo_with_stack_behind, branches)

    assert len(plan) == 2
    assert plan[0].branch == "branch_a"
    assert plan[0].onto == "main"
    assert plan[1].branch == "branch_b"
    assert plan[1].onto == "branch_a"

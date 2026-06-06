# Stack Comparison Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Borrow the strongest ideas from `kitlangton/stack` while preserving Shortcake's trailer-first workflow and broader branch-editing surface.

**Architecture:** Keep `Shortcake-Parent` trailers as the durable source of branch parentage. Add a small planning, rendering, and journaling layer around destructive Git/GitHub workflows so `sync`, `submit`, and the proposed `land` command can preview, apply, explain, and undo changes consistently.

**Tech Stack:** Python 3.14, Typer, Pydantic, pygit2/dulwich-backed Git helpers, httpx GitHub client, pytest, Ruff, ty.

---

## Comparison Summary

Stack inspected: `kitlangton/stack` at commit `1be576f03ef265d223f62e02b589c06d6edcf47e`.

Shortcake is better at:

- Breadth: local branch creation, navigation, reorder, move, fold, line/hunk movement, pull/checkout, AI review, and a Vite review UI.
- Local source of truth: trailers travel with commits and do not depend on a local `.git/stack/state.json` file.
- Test depth: current repo has more than 1,200 pytest-style test functions plus Playwright e2e tests and CI coverage gating.
- Git history handling: squash-merge detection is implemented and tested against tree equality, deletions, extra trunk changes, and false positives.

Stack is better at:

- Public product clarity: README, package metadata, install path, command guide, and agent skill are crisp.
- Safety model: mutating repair flows create backups, checkpoint an undo journal, and expose `history` and `undo`.
- GitHub-native repair workflow: it infers stack links from PR bases, scopes sync to the current stack, retargets children before merging roots, supports auto-merge, and can keep going across independent stacks.
- Output design: dry-runs and applied syncs render outcome-oriented tree summaries instead of phase logs.
- Service boundaries: Git, GitHub, Store, Stack orchestration, graph rendering, and stack block rendering are separated.

## File Structure

Create:

- `src/shortcake/_operation_plan.py`: typed operation plan items shared by sync, submit, and land.
- `src/shortcake/_operation_journal.py`: persisted undo journal in `.git/shortcake-undo.json`.
- `src/shortcake/_stack_render.py`: outcome-oriented tree and action rendering.
- `src/shortcake/_github_stack_inference.py`: infer candidate parent links from open GitHub PR bases.
- `src/shortcake/commands/doctor.py`: environment and repository health checks.
- `src/shortcake/commands/history.py`: print the last undo journal.
- `src/shortcake/commands/undo.py`: dry-run or apply undo journal restoration.
- `src/shortcake/commands/land.py`: dry-run or apply root PR landing with child PR retargeting and descendant repair.
- `tests/test_operation_plan.py`
- `tests/test_operation_journal.py`
- `tests/test_stack_render.py`
- `tests/test_github_stack_inference.py`
- `tests/test_doctor.py`
- `tests/test_undo.py`
- `tests/test_land.py`
- `docs/shortcake-agent-skill.md`

Modify:

- `README.md`: replace empty file with product positioning, install, happy path, commands, safety rules.
- `pyproject.toml`: replace placeholder description and add useful keywords/classifiers if supported by `uv_build`.
- `src/shortcake/cli.py`: register `doctor`, `history`, `undo`, `land`, and optional `status` alias for `ls`.
- `src/shortcake/commands/sync.py`: split plan/apply, add scoped sync, tree output, and journal writes.
- `src/shortcake/commands/submit.py`: save journal entries for pushed branches, PR creations, PR base/body updates.
- `src/shortcake/_pr_stack.py`: keep current branch-aware stack block, but centralize rendering through `_stack_render.py`.
- `.github/workflows/test.yml`: add package smoke check.

## Task 1: Public Positioning And Packaging

**Files:**

- Modify: `README.md`
- Modify: `pyproject.toml`
- Create: `docs/shortcake-agent-skill.md`
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Replace the empty README with the product contract**

Write the README around this stance:

````markdown
# Shortcake

Shortcake is a trailer-first stacked PR workflow tool.

Use Shortcake when you want local stack editing as well as GitHub PR sync:
create branches, move commits or hunks between branches, reorder stacks,
review diffs, submit PRs, and repair descendants after parent branches move.

Stack relationships are stored in the first stack commit as:

```text
Shortcake-Parent: main
```

This keeps stack intent with the branch history instead of relying only on a
local state file.
````

- [ ] **Step 2: Add the happy path commands**

Include these examples in `README.md`:

```bash
sc create -m "feat: schema source"
sc create -m "feat: openapi output"
sc ls
sc submit --dry-run
sc submit
sc sync --dry-run
sc sync --yes
```

Also document the proposed commands before implementation:

```bash
sc doctor
sc history
sc undo
sc undo --apply
sc land
sc land --apply
```

- [ ] **Step 3: Fix package metadata**

Change `pyproject.toml`:

```toml
description = "Trailer-first stacked PR workflow tool"
```

Add project URLs:

```toml
[project.urls]
Homepage = "https://github.com/patrick91/shortcake"
Issues = "https://github.com/patrick91/shortcake/issues"
```

- [ ] **Step 4: Add package smoke check to CI**

Add after the coverage step in `.github/workflows/test.yml`:

```yaml
      - name: Package smoke check
        run: uv build
```

- [ ] **Step 5: Verify docs and metadata**

Run:

```bash
uv build
uv run --group linting ruff format --check README.md pyproject.toml
```

Expected: `uv build` creates a source distribution and wheel under `dist/`. Ruff may ignore markdown; if it rejects markdown input, rerun it only on `pyproject.toml`.

## Task 2: Shared Operation Plan Types

**Files:**

- Create: `src/shortcake/_operation_plan.py`
- Create: `tests/test_operation_plan.py`

- [ ] **Step 1: Write tests for serializable plan items**

Create `tests/test_operation_plan.py`:

```python
from shortcake._operation_plan import (
    BackupBranch,
    CreatePullRequest,
    DeleteBranch,
    OperationPlan,
    PushBranch,
    RebaseBranch,
    RetargetPullRequest,
    UpdatePullRequestBody,
)


def test_operation_plan_round_trip() -> None:
    plan = OperationPlan(
        title="Sync preview",
        items=[
            BackupBranch(branch="child", backup="backup/shortcake-sync-1-child"),
            RebaseBranch(branch="child", onto="main"),
            PushBranch(branch="child"),
            RetargetPullRequest(number=42, base="main"),
            UpdatePullRequestBody(number=42),
            CreatePullRequest(branch="child", base="main"),
            DeleteBranch(branch="old-parent"),
        ],
    )

    dumped = plan.model_dump()
    loaded = OperationPlan.model_validate(dumped)

    assert loaded == plan
    assert [item.kind for item in loaded.items] == [
        "backup_branch",
        "rebase_branch",
        "push_branch",
        "retarget_pull_request",
        "update_pull_request_body",
        "create_pull_request",
        "delete_branch",
    ]
```

- [ ] **Step 2: Implement the models**

Create `src/shortcake/_operation_plan.py`:

```python
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class BackupBranch(BaseModel):
    kind: Literal["backup_branch"] = "backup_branch"
    branch: str
    backup: str


class RebaseBranch(BaseModel):
    kind: Literal["rebase_branch"] = "rebase_branch"
    branch: str
    onto: str


class PushBranch(BaseModel):
    kind: Literal["push_branch"] = "push_branch"
    branch: str


class RetargetPullRequest(BaseModel):
    kind: Literal["retarget_pull_request"] = "retarget_pull_request"
    number: int
    base: str


class UpdatePullRequestBody(BaseModel):
    kind: Literal["update_pull_request_body"] = "update_pull_request_body"
    number: int


class CreatePullRequest(BaseModel):
    kind: Literal["create_pull_request"] = "create_pull_request"
    branch: str
    base: str


class DeleteBranch(BaseModel):
    kind: Literal["delete_branch"] = "delete_branch"
    branch: str


OperationItem = Annotated[
    BackupBranch
    | RebaseBranch
    | PushBranch
    | RetargetPullRequest
    | UpdatePullRequestBody
    | CreatePullRequest
    | DeleteBranch,
    Field(discriminator="kind"),
]


class OperationPlan(BaseModel):
    title: str
    items: list[OperationItem]
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
uv run pytest tests/test_operation_plan.py -q
```

Expected: pass.

## Task 3: Durable Undo Journal

**Files:**

- Create: `src/shortcake/_operation_journal.py`
- Create: `tests/test_operation_journal.py`

- [ ] **Step 1: Write journal save/load tests**

Create `tests/test_operation_journal.py`:

```python
from shortcake._operation_journal import JournalEntry, OperationJournal


def test_journal_save_load_delete(temp_repo) -> None:
    journal = OperationJournal(
        version=1,
        original_branch="child",
        entries=[
            JournalEntry(
                branch="child",
                original_ref="abc123",
                backup_ref="backup/shortcake-sync-1-child",
                pr_number=42,
                original_base="parent",
                created_pr_number=None,
            )
        ],
        actions=["backup child", "rebase child"],
    )

    journal.save(temp_repo)
    loaded = OperationJournal.load(temp_repo)

    assert loaded == journal

    journal.delete(temp_repo)
    assert OperationJournal.load(temp_repo) is None
```

- [ ] **Step 2: Implement journal models and file IO**

Create `src/shortcake/_operation_journal.py`:

```python
from pathlib import Path

from pydantic import BaseModel

from shortcake._git._core import Repo

STATE_FILE = "shortcake-undo.json"
STATE_VERSION = 1


class JournalEntry(BaseModel):
    branch: str
    original_ref: str | None = None
    backup_ref: str | None = None
    pr_number: int | None = None
    original_base: str | None = None
    created_pr_number: int | None = None


class OperationJournal(BaseModel):
    version: int = STATE_VERSION
    original_branch: str | None = None
    entries: list[JournalEntry]
    actions: list[str]

    @classmethod
    def path(cls, repo: Repo) -> Path:
        return Path(repo.path) / STATE_FILE

    @classmethod
    def load(cls, repo: Repo) -> "OperationJournal | None":
        path = cls.path(repo)
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_bytes())

    def save(self, repo: Repo) -> None:
        path = self.path(repo)
        path.write_bytes(self.model_dump_json(indent=2).encode())

    def delete(self, repo: Repo) -> None:
        self.path(repo).unlink(missing_ok=True)
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
uv run pytest tests/test_operation_journal.py -q
```

Expected: pass.

## Task 4: Outcome-Oriented Stack Rendering

**Files:**

- Create: `src/shortcake/_stack_render.py`
- Create: `tests/test_stack_render.py`

- [ ] **Step 1: Write rendering tests**

Create `tests/test_stack_render.py`:

```python
from shortcake._operation_plan import (
    BackupBranch,
    OperationPlan,
    RebaseBranch,
    RetargetPullRequest,
    UpdatePullRequestBody,
)
from shortcake._stack_render import render_operation_plan


def test_render_operation_plan_summary() -> None:
    plan = OperationPlan(
        title="Sync preview",
        items=[
            BackupBranch(branch="child", backup="backup/shortcake-sync-1-child"),
            RebaseBranch(branch="child", onto="main"),
            RetargetPullRequest(number=42, base="main"),
            UpdatePullRequestBody(number=42),
        ],
    )

    output = render_operation_plan(plan, apply=False)

    assert "Sync preview" in output
    assert "would backup child" in output
    assert "would rebase child onto main" in output
    assert "would retarget #42 to main" in output
    assert "would update #42 stack block" in output
    assert "Apply:" in output
```

- [ ] **Step 2: Implement renderer**

Create `src/shortcake/_stack_render.py`:

```python
from shortcake._operation_plan import OperationPlan


def render_operation_plan(plan: OperationPlan, *, apply: bool) -> str:
    prefix = "" if apply else "would "
    lines = [plan.title, ""]
    for item in plan.items:
        if item.kind == "backup_branch":
            lines.append(f"- {prefix}backup {item.branch} -> {item.backup}")
        elif item.kind == "rebase_branch":
            lines.append(f"- {prefix}rebase {item.branch} onto {item.onto}")
        elif item.kind == "push_branch":
            lines.append(f"- {prefix}push {item.branch}")
        elif item.kind == "retarget_pull_request":
            lines.append(f"- {prefix}retarget #{item.number} to {item.base}")
        elif item.kind == "update_pull_request_body":
            lines.append(f"- {prefix}update #{item.number} stack block")
        elif item.kind == "create_pull_request":
            lines.append(f"- {prefix}create PR for {item.branch} -> {item.base}")
        elif item.kind == "delete_branch":
            lines.append(f"- {prefix}delete branch {item.branch}")
    if not apply and plan.items:
        lines.extend(["", "Apply:", "  rerun with --apply or --yes"])
    if apply and plan.items:
        lines.extend(["", "Undo:", "  sc undo --apply"])
    return "\n".join(lines)
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
uv run pytest tests/test_stack_render.py -q
```

Expected: pass.

## Task 5: GitHub PR-Base Inference

**Files:**

- Create: `src/shortcake/_github_stack_inference.py`
- Create: `tests/test_github_stack_inference.py`
- Modify: `src/shortcake/commands/sync.py`

- [ ] **Step 1: Write inference tests**

Create `tests/test_github_stack_inference.py`:

```python
from shortcake._github_stack_inference import PullRef, infer_parent_links


def test_infer_parent_links_skips_standalone_trunk_pr() -> None:
    pulls = [
        PullRef(number=1, head="root", base="main"),
        PullRef(number=2, head="child", base="root"),
    ]

    assert infer_parent_links(pulls, trunks={"main"}, local_branches={"root", "child"}) == {
        "root": "main",
        "child": "root",
    }


def test_infer_parent_links_requires_local_branches() -> None:
    pulls = [PullRef(number=1, head="child", base="missing-parent")]

    assert infer_parent_links(pulls, trunks={"main"}, local_branches={"child"}) == {}
```

- [ ] **Step 2: Implement inference**

Create `src/shortcake/_github_stack_inference.py`:

```python
from pydantic import BaseModel


class PullRef(BaseModel):
    number: int
    head: str
    base: str


def infer_parent_links(
    pulls: list[PullRef],
    *,
    trunks: set[str],
    local_branches: set[str],
) -> dict[str, str]:
    child_bases = {pull.base for pull in pulls if pull.base not in trunks}
    result: dict[str, str] = {}

    for pull in pulls:
        if pull.head not in local_branches:
            continue
        if pull.base not in trunks and pull.base not in local_branches:
            continue
        if pull.head == pull.base:
            continue
        if pull.base in trunks and pull.head not in child_bases:
            continue
        result[pull.head] = pull.base

    return _remove_cycles(result, trunks)


def _remove_cycles(links: dict[str, str], trunks: set[str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for branch, parent in links.items():
        seen = {branch}
        current = parent
        cycle = False
        while current not in trunks and current in links:
            if current in seen:
                cycle = True
                break
            seen.add(current)
            current = links[current]
        if not cycle:
            clean[branch] = parent
    return clean
```

- [ ] **Step 3: Run the focused inference test**

Run:

```bash
uv run pytest tests/test_github_stack_inference.py -q
```

Expected: pass.

## Task 6: Sync Safety Upgrade

**Files:**

- Modify: `src/shortcake/commands/sync.py`
- Modify: `tests/test_sync.py`
- Use: `src/shortcake/_operation_plan.py`
- Use: `src/shortcake/_operation_journal.py`
- Use: `src/shortcake/_stack_render.py`

- [ ] **Step 1: Add tests for dry-run no mutation**

Add or update tests so `sc sync --dry-run` verifies:

```python
assert git.branch_exists(repo, "branch_a")
assert OperationJournal.load(repo) is None
```

- [ ] **Step 2: Add tests for backup before reparent/delete**

Add a sync test where a merged parent with a child produces:

```python
journal = OperationJournal.load(repo)
assert journal is not None
assert any(entry.branch == "branch_b" for entry in journal.entries)
```

- [ ] **Step 3: Split `_sync` into plan and apply helpers**

Inside `src/shortcake/commands/sync.py`, add:

```python
def _plan_sync(repo: Repo, *, force: bool, dry_run: bool) -> OperationPlan:
    return OperationPlan(title="Sync preview" if dry_run else "Sync plan", items=[])


def _apply_sync_plan(repo: Repo, plan: OperationPlan) -> SyncResult:
    return SyncResult(trunk_updated=False)
```

Move existing detection logic into `_plan_sync` incrementally. The initial empty plan keeps the test suite passing while the next steps add branch deletion, reparenting, restack, and GitHub stale detection items.

- [ ] **Step 4: Write the journal before destructive steps**

Before deleting a branch, rebasing a child, changing a PR base, or creating a PR, save an `OperationJournal` entry with the original branch ref, PR base, and created PR number when available.

- [ ] **Step 5: Render previews and applied summaries from the same plan**

Replace scattered dry-run `typer.echo` calls such as `Would delete merged branch` with `render_operation_plan(plan, apply=False)`, and render `render_operation_plan(plan, apply=True)` after successful apply.

- [ ] **Step 6: Verify sync tests**

Run:

```bash
uv run pytest tests/test_sync.py tests/test_operation_plan.py tests/test_operation_journal.py tests/test_stack_render.py -q
```

Expected: pass.

## Task 7: History And Undo Commands

**Files:**

- Create: `src/shortcake/commands/history.py`
- Create: `src/shortcake/commands/undo.py`
- Modify: `src/shortcake/cli.py`
- Create: `tests/test_undo.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_undo.py`:

```python
from typer.testing import CliRunner

from shortcake._operation_journal import JournalEntry, OperationJournal
from shortcake.cli import app

runner = CliRunner()


def test_history_no_journal(temp_repo, monkeypatch) -> None:
    monkeypatch.chdir(temp_repo.workdir)

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert "No applied Shortcake mutation recorded." in result.output


def test_undo_dry_run_prints_actions(temp_repo, monkeypatch) -> None:
    monkeypatch.chdir(temp_repo.workdir)
    OperationJournal(
        original_branch="main",
        entries=[JournalEntry(branch="feature", original_ref="abc123")],
        actions=["delete branch feature"],
    ).save(temp_repo)

    result = runner.invoke(app, ["undo"])

    assert result.exit_code == 0
    assert "would restore stack metadata" in result.output
```

- [ ] **Step 2: Implement `history`**

Create `src/shortcake/commands/history.py`:

```python
import typer

from shortcake import _git as git
from shortcake._operation_journal import OperationJournal


def history() -> None:
    """Show the last applied Shortcake mutation."""
    repo = git.open_repo()
    journal = OperationJournal.load(repo)
    if journal is None:
        typer.echo("No applied Shortcake mutation recorded.")
        return
    typer.echo("Last Shortcake mutation:")
    for action in journal.actions:
        typer.echo(f"  {action}")
    typer.echo("Undo with: sc undo --apply")
```

- [ ] **Step 3: Implement `undo` dry-run first**

Create `src/shortcake/commands/undo.py`:

```python
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake._operation_journal import OperationJournal


def undo(
    apply: Annotated[bool, typer.Option("--apply", help="Restore the last mutation")] = False,
) -> None:
    """Restore the last applied Shortcake mutation."""
    repo = git.open_repo()
    journal = OperationJournal.load(repo)
    if journal is None:
        typer.echo("Nothing to undo.")
        return

    prefix = "" if apply else "would "
    for entry in journal.entries:
        if entry.backup_ref:
            typer.echo(f"{prefix}restore {entry.branch} from {entry.backup_ref}")
        elif entry.original_ref:
            typer.echo(f"{prefix}restore {entry.branch} to {entry.original_ref}")
        if entry.created_pr_number:
            typer.echo(f"{prefix}close #{entry.created_pr_number}")
        if entry.pr_number and entry.original_base:
            typer.echo(f"{prefix}retarget #{entry.pr_number} to {entry.original_base}")
    typer.echo(f"{prefix}restore stack metadata")
```

Add apply behavior after dry-run tests pass.

- [ ] **Step 4: Register commands**

Modify `src/shortcake/cli.py`:

```python
from shortcake.commands.history import history
from shortcake.commands.undo import undo

app.command()(history)
app.command()(undo)
```

- [ ] **Step 5: Verify**

Run:

```bash
uv run pytest tests/test_undo.py tests/test_cli.py -q
```

Expected: pass.

## Task 8: Doctor Command

**Files:**

- Create: `src/shortcake/commands/doctor.py`
- Modify: `src/shortcake/cli.py`
- Create: `tests/test_doctor.py`

- [ ] **Step 1: Write doctor tests**

Create `tests/test_doctor.py`:

```python
from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_doctor_reports_current_branch(repo_with_stack, monkeypatch) -> None:
    monkeypatch.chdir(repo_with_stack.workdir)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "ok current branch:" in result.output
    assert "ok tracked branches:" in result.output
```

- [ ] **Step 2: Implement command**

Create `src/shortcake/commands/doctor.py`:

```python
import typer

from shortcake import _git as git
from shortcake._operation_journal import OperationJournal


def doctor() -> None:
    """Inspect repository, GitHub, and Shortcake state."""
    repo = git.open_repo()
    current = git.get_current_branch(repo)
    typer.echo(f"ok current branch: {current}" if current else "warn detached HEAD")

    dirty = git.has_uncommitted_changes(repo)
    typer.echo("warn worktree dirty" if dirty else "ok worktree clean")

    branches = git.get_tracked_branches(repo)
    typer.echo(f"ok tracked branches: {len(branches)}")

    journal = OperationJournal.load(repo)
    typer.echo("info undo journal present" if journal else "ok undo journal: none")
```

- [ ] **Step 3: Register and verify**

Run:

```bash
uv run pytest tests/test_doctor.py -q
uv run shortcake doctor
```

Expected: test passes, command prints health lines.

## Task 9: Root Landing Flow

**Files:**

- Create: `src/shortcake/commands/land.py`
- Modify: `src/shortcake/cli.py`
- Create: `tests/test_land.py`
- Modify: `src/shortcake/_github.py`: add `merge_pr` helper.

- [ ] **Step 1: Write dry-run test**

Create `tests/test_land.py`:

```python
from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_land_dry_run_requires_tracked_stack(repo_with_stack, monkeypatch) -> None:
    monkeypatch.chdir(repo_with_stack.workdir)

    result = runner.invoke(app, ["land"])

    assert result.exit_code == 0
    assert "Land preview" in result.output
```

- [ ] **Step 2: Implement dry-run root detection**

Create `src/shortcake/commands/land.py`:

```python
from typing import Annotated

import typer

from shortcake import _git as git
from shortcake._operation_plan import OperationPlan
from shortcake._stack_render import render_operation_plan
from shortcake.commands.restack import _get_stack_in_order


def land(
    branch: Annotated[str | None, typer.Argument(help="Stack branch to land from")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Merge the root PR")] = False,
) -> None:
    """Land the root PR in the current stack and repair descendants."""
    repo = git.open_repo()
    current = branch or git.get_current_branch(repo)
    if current is None:
        typer.echo("Error: not on a branch", err=True)
        raise typer.Exit(1)

    stack = _get_stack_in_order(repo, current)
    if not stack:
        typer.echo(f"Error: branch '{current}' is not tracked by Shortcake.", err=True)
        raise typer.Exit(1)

    root = stack[0]
    plan = OperationPlan(title=f"Land preview for {root}", items=[])
    typer.echo(render_operation_plan(plan, apply=apply))
```

- [ ] **Step 3: Add root landing plan items**

Extend `land.py` with this helper:

```python
from shortcake._operation_plan import BackupBranch, PushBranch, RebaseBranch


def _plan_land_root(root: str, trunk: str, descendants: list[str]) -> OperationPlan:
    items = [BackupBranch(branch=root, backup=f"backup/shortcake-land-preview-{root}")]
    for branch in descendants:
        items.extend(
            [
                RebaseBranch(branch=branch, onto=trunk),
                PushBranch(branch=branch),
            ]
        )
    return OperationPlan(title=f"Land preview for {root}", items=items)
```

Replace the empty `OperationPlan` in `land()` with:

```python
all_branches = set(git.get_all_local_branches(repo))
trunk = git.get_branch_parent(repo, root, all_branches) or git.get_default_branch(repo) or "main"
plan = _plan_land_root(root, trunk, stack[1:])
```

- [ ] **Step 4: Add GitHub merge helper**

Add this method to `GitHubClient` in `src/shortcake/_github.py`:

```python
def merge_pr(self, number: int, *, admin: bool = False) -> None:
    data: dict[str, object] = {"merge_method": "squash"}
    if admin:
        data["admin"] = True
    response = self.client.put(
        f"/repos/{self.owner}/{self.repo}/pulls/{number}/merge",
        json=data,
    )
    response.raise_for_status()
```

Do not expose auto-merge in this plan. The first landing milestone supports dry-run and `--apply`.

- [ ] **Step 5: Verify focused behavior**

Run:

```bash
uv run pytest tests/test_land.py tests/test_submit.py tests/test_pr_stack.py -q
```

Expected: pass.

## Task 10: Refactor Only After Behavior Is Locked

**Files:**

- Modify: `src/shortcake/commands/ui.py`
- Modify: `src/shortcake/commands/move_lines.py`
- Modify: tests matching moved behavior.

- [ ] **Step 1: Split UI payload builders from HTTP server**

Move pure payload functions into `src/shortcake/_ui_payloads.py`:

```python
from shortcake.commands.ui import StackDiffBranch

def build_stack_payload_from_branches(current_branch: str | None, branches: list[StackDiffBranch]) -> dict:
    return {
        "currentBranch": current_branch,
        "branches": [branch.__dict__ for branch in branches],
    }
```

Keep endpoint wiring in `commands/ui.py`.

- [ ] **Step 2: Split move-line rollback helpers**

Move rollback state helpers from `commands/move_lines.py` into `src/shortcake/_branch_snapshot.py`:

```python
from pydantic import BaseModel


class BranchSnapshot(BaseModel):
    refs: dict[str, str]
    created_branches: list[str] = []
```

Use it from move, split, and future journal-backed operations.

- [ ] **Step 3: Verify broad behavior**

Run:

```bash
uv run pytest tests/test_move_lines.py tests/test_ui.py tests/e2e/ -q
```

Expected: pass.

## Final Verification

Run the same checks CI runs:

```bash
uv run --group linting ruff check src/ tests/
uv run --group linting ruff format --check src/ tests/
uv run --group typing ty check src/
uv run pytest tests/ -q
uv run pytest tests/e2e/ -q
uv build
```

Expected: all commands pass.

## Execution Notes

- Implement Tasks 1-4 first. They are low-risk and create the foundation for all safety work.
- Implement Tasks 5-7 before changing destructive sync behavior.
- Implement Task 9 only after undo journal restoration is real, because landing touches GitHub PR bases and branch refs.
- Task 10 is intentionally last. Refactoring large modules before the journal and plan behavior is locked would add risk without improving user safety.

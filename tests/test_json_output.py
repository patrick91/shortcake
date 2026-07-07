"""End-to-end tests for --json output on mutating commands."""

import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortcake._trailers import Trailers
from shortcake.cli import app
from tests._git_helpers import (
    Repo,
    add_paths,
    commit,
    get_ref,
    run_git,
    set_ref,
    switch_branch,
)

runner = CliRunner()


def _tracked_feature(repo: Repo, tmp_path: Path, content: str = "feature\n") -> None:
    """Create tracked branch 'feature' off main with f.txt."""
    main_sha = get_ref(repo, "refs/heads/main")
    set_ref(repo, "refs/heads/feature", main_sha)
    repo.set_head("refs/heads/feature")
    (tmp_path / "f.txt").write_text(content)
    add_paths(repo, tmp_path / "f.txt")
    commit(repo, Trailers(parent_branch="main").apply_to("Add feature"))


def _advance_main(repo: Repo, tmp_path: Path, name: str, content: str) -> None:
    """Add a commit to main."""
    switch_branch(repo, "main")
    (tmp_path / name).write_text(content)
    add_paths(repo, tmp_path / name)
    commit(repo, b"Advance main")
    switch_branch(repo, "feature")


# --- restack --json ---


def test_restack_json_success(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)
    _advance_main(temp_repo, tmp_path, "m.txt", "main\n")

    result = runner.invoke(app, ["restack", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["restacked"] == ["feature"]
    assert document["data"]["conflict"] is None


def test_restack_json_up_to_date(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)

    result = runner.invoke(app, ["restack", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["restacked"] == []
    assert document["data"]["current_branch_untracked"] is False


def test_restack_json_untracked(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["restack", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["current_branch_untracked"] is True


def test_restack_json_dry_run_planned(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)
    _advance_main(temp_repo, tmp_path, "m.txt", "main\n")

    result = runner.invoke(app, ["restack", "--json", "--dry-run"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["planned"] == [{"branch": "feature", "onto": "main"}]
    assert document["data"]["restacked"] == []


def test_restack_json_error_envelope(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)
    (tmp_path / "f.txt").write_text("dirty\n")

    result = runner.invoke(app, ["restack", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "restack_failed"
    assert "uncommitted changes" in document["error"]["message"]


def test_restack_json_conflict_then_continue_json(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflicted restack emits a structured conflict; continue completes it."""
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path, content="feature version\n")
    _advance_main(temp_repo, tmp_path, "f.txt", "main version\n")

    result = runner.invoke(app, ["restack", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    conflict = document["data"]["conflict"]
    assert conflict["branch"] == "feature"
    assert conflict["files"] == ["f.txt"]
    assert "sc continue" in conflict["resolve"]
    # No progress lines may pollute the JSON document
    assert result.output.strip().count("\n") == 0

    # Resolve and continue with JSON output (stage via git CLI: the fixture's
    # long-lived pygit2 index snapshot must not clobber the rebase state)
    (tmp_path / "f.txt").write_text("merged version\n")
    run_git(temp_repo, "add", "f.txt")

    continue_result = runner.invoke(app, ["continue", "--json"])

    assert continue_result.exit_code == 0
    continue_document = json.loads(continue_result.output)
    assert continue_document["data"]["restacked"] == ["feature"]
    assert continue_document["data"]["conflict"] is None


def test_continue_json_nothing_in_progress(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["continue", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "continue_failed"
    assert "No restack in progress" in document["error"]["message"]


# --- create --json ---


def test_create_json_success(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "new.txt").write_text("new\n")
    add_paths(temp_repo, tmp_path / "new.txt")

    result = runner.invoke(app, ["create", "my-branch", "-m", "Add new", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["branch"].endswith("my-branch")
    assert document["data"]["parent"] == "main"
    assert document["data"]["conflict"] is None


def test_create_json_requires_message(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "new.txt").write_text("new\n")
    add_paths(temp_repo, tmp_path / "new.txt")

    result = runner.invoke(app, ["create", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "message_required"
    assert document["error"]["hint"] == "Pass -m 'message'"


def test_create_json_no_staged_changes(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["create", "-m", "Nothing", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "no_staged_changes"


def test_create_json_hook_failure_captures_output(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hook output is captured into the error envelope, not stdout."""
    monkeypatch.chdir(tmp_path)
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho 'lint exploded'\nexit 1\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    (tmp_path / "new.txt").write_text("new\n")
    add_paths(temp_repo, tmp_path / "new.txt")

    result = runner.invoke(app, ["create", "-m", "Add new", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "hook_failed"
    assert "lint exploded" in document["error"]["message"]


def test_create_json_hook_self_heal_keeps_stdout_pure(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A formatter-style hook failure self-heals without polluting the JSON."""
    monkeypatch.chdir(tmp_path)
    hooks_dir = Path(temp_repo.path.rstrip("/")) / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text(
        "#!/bin/sh\n"
        "if grep -q unformatted new.txt; then\n"
        "  echo formatted > new.txt\n"
        "  echo 'reformatted new.txt'\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n"
    )
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

    (tmp_path / "new.txt").write_text("unformatted")
    add_paths(temp_repo, tmp_path / "new.txt")

    result = runner.invoke(app, ["create", "-m", "Add new", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["parent"] == "main"


# --- modify --json ---


def test_modify_json_amend(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)
    (tmp_path / "extra.txt").write_text("extra\n")
    add_paths(temp_repo, tmp_path / "extra.txt")

    result = runner.invoke(app, ["modify", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"] == {"branch": "feature", "action": "amended"}


def test_modify_json_new_commit(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)
    (tmp_path / "extra.txt").write_text("extra\n")
    add_paths(temp_repo, tmp_path / "extra.txt")

    result = runner.invoke(app, ["modify", "-m", "Add extra", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"] == {"branch": "feature", "action": "committed"}


def test_modify_json_rejects_edit(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)

    result = runner.invoke(app, ["modify", "--edit", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "invalid_options"
    assert "non-interactively" in document["error"]["hint"]


def test_modify_json_target_fold(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)

    # child branch on top of feature
    feature_sha = get_ref(temp_repo, "refs/heads/feature")
    set_ref(temp_repo, "refs/heads/child", feature_sha)
    temp_repo.set_head("refs/heads/child")
    (tmp_path / "c.txt").write_text("child\n")
    add_paths(temp_repo, tmp_path / "c.txt")
    commit(temp_repo, Trailers(parent_branch="feature").apply_to("Add child"))

    # stage a change on child, fold into feature
    (tmp_path / "folded.txt").write_text("folded\n")
    add_paths(temp_repo, tmp_path / "folded.txt")

    result = runner.invoke(app, ["modify", "-t", "feature", "--json"])

    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["data"]["action"] == "folded"
    assert document["data"]["target"] == "feature"


def test_modify_json_error_envelope(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _tracked_feature(temp_repo, tmp_path)

    result = runner.invoke(app, ["modify", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "no_staged_changes"


def test_continue_json_hits_next_conflict(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continue that runs into another conflict reports it structurally."""
    monkeypatch.chdir(tmp_path)

    # main edits f.txt and g.txt after the stack was built
    _tracked_feature(temp_repo, tmp_path, content="feature f\n")
    feature_sha = get_ref(temp_repo, "refs/heads/feature")
    set_ref(temp_repo, "refs/heads/child", feature_sha)
    temp_repo.set_head("refs/heads/child")
    (tmp_path / "g.txt").write_text("child g\n")
    add_paths(temp_repo, tmp_path / "g.txt")
    commit(temp_repo, Trailers(parent_branch="feature").apply_to("Add child"))

    switch_branch(temp_repo, "main")
    (tmp_path / "f.txt").write_text("main f\n")
    (tmp_path / "g.txt").write_text("main g\n")
    add_paths(temp_repo, tmp_path / "f.txt")
    add_paths(temp_repo, tmp_path / "g.txt")
    commit(temp_repo, b"Main edits both")
    switch_branch(temp_repo, "child")

    result = runner.invoke(app, ["restack", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["data"]["conflict"]["branch"] == "feature"

    # Resolve the first conflict, continue — the child then conflicts on g.txt
    (tmp_path / "f.txt").write_text("merged f\n")
    run_git(temp_repo, "add", "f.txt")

    continue_result = runner.invoke(app, ["continue", "--json"])

    assert continue_result.exit_code == 1
    document = json.loads(continue_result.output)
    assert document["data"]["restacked"] == ["feature"]
    assert document["data"]["conflict"]["branch"] == "child"
    assert document["data"]["conflict"]["files"] == ["g.txt"]


def test_create_json_invalid_branch_name(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsluggable message fails structurally instead of prompting."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "new.txt").write_text("new\n")
    add_paths(temp_repo, tmp_path / "new.txt")

    result = runner.invoke(app, ["create", "-m", "!!!", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["error"]["code"] == "invalid_branch_name"
    assert "positional argument" in document["error"]["hint"]


def test_create_json_insert_before_conflict(
    temp_repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflicting insert reports the conflicted branch and exits 1."""
    monkeypatch.chdir(tmp_path)

    # main carries f.txt so both feature and the inserted branch edit it
    (tmp_path / "f.txt").write_text("base\n")
    add_paths(temp_repo, tmp_path / "f.txt")
    commit(temp_repo, b"Add f to main")

    _tracked_feature(temp_repo, tmp_path, content="feature version\n")

    # Stage a conflicting edit and insert it before feature
    (tmp_path / "f.txt").write_text("inserted version\n")
    add_paths(temp_repo, tmp_path / "f.txt")

    result = runner.invoke(
        app, ["create", "--before", "-m", "Insert conflicting", "--json"]
    )

    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["data"]["conflict"] == "feature"
    assert document["data"]["inserted_before"] == "feature"

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from shortcake import _git as git
from shortcake import _recap
from shortcake._recap import (
    RecapError,
    build_recap_context,
    create_recap,
    delete_recap,
    list_recaps,
    load_recap,
    parse_patch_file_stats,
    split_frontmatter,
    stored_recap_payload,
    validate_restricted_mdx,
)
from shortcake.cli import app
from shortcake.commands.ui import _build_request_handler
from tests._git_helpers import Repo, commit_files, set_ref, switch_branch

runner = CliRunner()


def _mdx_from_context(
    context: dict,
    *,
    title: str = "Local recap",
    body: str = "<FileMap />",
) -> str:
    frontmatter = {
        "shortcakeRecap": 1,
        "title": title,
        "source": context["source"],
    }
    frontmatter_yaml = yaml.safe_dump(frontmatter, sort_keys=False)
    return f"---\n{frontmatter_yaml}---\n\n# {title}\n\n{body}\n"


def test_branch_context_includes_source_patch_files_and_template(
    repo_with_stack: Repo,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    template_frontmatter = yaml.safe_load(context["template"].split("---\n", 2)[1])

    assert context["source"]["kind"] == "branch"
    assert context["source"]["branch"] == "branch_b"
    assert context["source"]["parent"] == "branch_a"
    assert (
        context["source"]["head"]
        == git.get_branch_head(repo_with_stack, "branch_b").decode()
    )
    assert context["patchHash"].startswith("sha256:")
    assert "diff --git a/b.txt b/b.txt" in context["patch"]
    assert context["files"] == [
        {"path": "b.txt", "additions": 1, "deletions": 0, "status": "added"}
    ]
    assert template_frontmatter["title"] == "branch_b"
    assert "shortcakeRecap: 1" in context["template"]
    assert "<FileMap />" in context["template"]
    assert "Use `annotations='[...]'`" in context["template"]
    assert "Write a short validation summary" in context["template"]


def test_context_allows_git_base_for_current_branch(repo_with_stack: Repo) -> None:
    context = build_recap_context(repo_with_stack, branch="main")
    template_frontmatter = yaml.safe_load(context["template"].split("---\n", 2)[1])

    assert context["source"]["kind"] == "branch"
    assert context["source"]["branch"] == "branch_b"
    assert context["source"]["parent"] == "main"
    assert (
        context["source"]["head"]
        == git.get_branch_head(repo_with_stack, "branch_b").decode()
    )
    assert "diff --git a/a.txt b/a.txt" in context["patch"]
    assert "diff --git a/b.txt b/b.txt" in context["patch"]
    assert context["files"] == [
        {"path": "a.txt", "additions": 1, "deletions": 0, "status": "added"},
        {"path": "b.txt", "additions": 1, "deletions": 0, "status": "added"},
    ]
    assert template_frontmatter["title"] == "branch_b"
    assert "main...branch_b" not in context["template"]

    stored = create_recap(repo_with_stack, _mdx_from_context(context))
    assert stored.patch == context["patch"]


def test_context_defaults_to_main_for_untracked_current_branch(
    repo_with_feature: Repo,
) -> None:
    context = build_recap_context(repo_with_feature)

    assert context["source"]["kind"] == "branch"
    assert context["source"]["branch"] == "feature"
    assert context["source"]["parent"] == "main"
    assert "diff --git a/feature.txt b/feature.txt" in context["patch"]
    assert "title: feature" in context["template"]


def test_working_context_captures_uncommitted_changes(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    (tmp_path / "working.txt").write_text("working content\n")

    context = build_recap_context(repo_with_stack, working=True)

    assert context["source"]["kind"] == "working"
    assert context["source"]["branch"] == "branch_b"
    assert "working.txt" in context["patch"]
    assert "title: Working changes" in context["template"]
    assert context["files"] == [
        {
            "path": "working.txt",
            "additions": 1,
            "deletions": 0,
            "status": "added",
        }
    ]


def test_create_recap_stores_private_artifacts_under_git_shortcake(
    repo_with_stack: Repo,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(
        context,
        body='<Diff path="b.txt" summary="Adds branch B." />',
    )

    stored = create_recap(repo_with_stack, mdx)

    recap_dir = Path(repo_with_stack.path) / "shortcake" / "recaps" / stored.meta.id
    assert (recap_dir / "recap.mdx").read_text() == mdx
    assert (recap_dir / "patch.diff").read_text() == context["patch"]
    assert json.loads((recap_dir / "meta.json").read_text())["title"] == "Local recap"

    assert list_recaps(repo_with_stack)[0].id == stored.meta.id
    assert load_recap(repo_with_stack, stored.meta.id) == stored
    assert stored_recap_payload(stored)["patch"] == context["patch"]


def test_parse_patch_file_stats_handles_statuses_and_empty_patch() -> None:
    patch = """diff --git a/deleted.txt b/deleted.txt
deleted file mode 100644
index 1111111..0000000 100644
--- a/deleted.txt
+++ /dev/null
@@ -1 +0,0 @@
-old
diff --git a/old-name.txt b/new-name.txt
similarity index 100%
rename from old-name.txt
rename to new-name.txt
diff --git a/changed.txt b/changed.txt
index 1111111..2222222 100644
--- a/changed.txt
+++ b/changed.txt
@@ -1 +1,2 @@
-old
+new
+extra
"""

    assert parse_patch_file_stats("") == []
    assert parse_patch_file_stats("\n" + patch)[0].path == "deleted.txt"
    assert [item.model_dump(mode="json") for item in parse_patch_file_stats(patch)] == [
        {
            "path": "deleted.txt",
            "additions": 0,
            "deletions": 1,
            "status": "deleted",
        },
        {
            "path": "new-name.txt",
            "additions": 0,
            "deletions": 0,
            "status": "renamed",
        },
        {
            "path": "changed.txt",
            "additions": 2,
            "deletions": 1,
            "status": "modified",
        },
    ]


def test_split_frontmatter_returns_frontmatter_and_body(repo_with_stack: Repo) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(context, body="Body text")

    frontmatter, body = split_frontmatter(mdx)

    assert frontmatter.title == "Local recap"
    assert body.startswith("\n# Local recap")


@pytest.mark.parametrize("recap_id", [".", "..", "bad.id", "../escape"])
def test_load_recap_rejects_path_like_ids(repo_with_stack: Repo, recap_id: str) -> None:
    with pytest.raises(RecapError, match="Invalid recap id"):
        load_recap(repo_with_stack, recap_id)


def test_create_recap_rejects_stale_patch_hash(repo_with_stack: Repo) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    context["source"]["patchHash"] = "sha256:" + "0" * 64
    mdx = _mdx_from_context(context)

    with pytest.raises(RecapError, match="patch hash"):
        create_recap(repo_with_stack, mdx)


@pytest.mark.parametrize(
    ("mdx", "match"),
    [
        ("# Missing frontmatter\n", "must start with YAML frontmatter"),
        (
            "---\n- not an object\n---\n\n<FileMap />",
            "frontmatter must be a YAML object",
        ),
        (
            "---\nsource: [\n---\n\n<FileMap />",
            "Invalid recap frontmatter",
        ),
        (
            "---\nshortcakeRecap: 1\ntitle: '   '\nsource:\n  kind: branch\n"
            "  branch: branch_b\n  parent: branch_a\n  head: abc\n"
            "  patchHash: sha256:" + "0" * 64 + "\n---\n\n<FileMap />",
            "title must not be empty",
        ),
        (
            "---\nshortcakeRecap: 1\ntitle: Bad\nsource:\n  kind: branch\n"
            "  branch: branch_b\n  parent: branch_a\n  head: abc\n"
            "  patchHash: bad\n---\n\n<FileMap />",
            "patchHash must be",
        ),
        (
            "---\nshortcakeRecap: 1\ntitle: Bad\nsource:\n  kind: branch\n"
            "  branch: branch_b\n  head: abc\n  patchHash: sha256:"
            + "0" * 64
            + "\n---\n\n<FileMap />",
            "Branch recaps require",
        ),
        (
            "---\nshortcakeRecap: 1\ntitle: Bad\nsource:\n  kind: working\n"
            "  branch: branch_b\n  parent: main\n  head: abc\n  patchHash: sha256:"
            + "0" * 64
            + "\n---\n\n<FileMap />",
            "Working recaps must not set",
        ),
    ],
)
def test_validate_restricted_mdx_rejects_bad_frontmatter(
    mdx: str,
    match: str,
) -> None:
    with pytest.raises(RecapError, match=match):
        validate_restricted_mdx(mdx)


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("import Thing from './thing'\n", "import/export"),
        ("<Callout />", "Unsupported"),
        ('<Diff path="b.txt" onClick="alert(1)" />', "event handler"),
        ('<Diff path={"b.txt"} />', "JS expression"),
        ('<Diff path="b.txt" path="b.txt" />', "repeats prop"),
        ("<Diff path='b.txt'", "Unclosed MDX component"),
        ("<Diff path='b.txt' " + "x" * 200, "Unclosed MDX component"),
        ("{answer}\n", "MDX expression"),
        ('<Diff path="b.txt" disabled />', "non-static prop"),
    ],
)
def test_validate_restricted_mdx_rejects_unsafe_constructs(
    repo_with_stack: Repo,
    body: str,
    match: str,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(context, body=body)

    with pytest.raises(RecapError, match=match):
        validate_restricted_mdx(mdx)


def test_validate_restricted_mdx_allows_multiline_json_attributes(
    repo_with_stack: Repo,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(
        context,
        body="""<Diff
  path="b.txt"
  summary="Adds branch B."
  annotations='[
    {
      "line": 1,
      "side": "right",
      "title": "Branch entrypoint",
      "text": "The generated branch behavior starts here.",
      "severity": "info"
    }
  ]'
/>""",
    )

    validate_restricted_mdx(mdx)


def test_validate_restricted_mdx_ignores_fenced_code_and_validates_diff_tabs(
    repo_with_stack: Repo,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(
        context,
        body="""```js
{notMdx}
```

<DiffTabs files='[
  "b.txt",
  {
    "path": "b.txt",
    "summary": "Shows branch B.",
    "annotations": [
      {
        "startLine": 1,
        "endLine": 1,
        "side": "right",
        "text": "Range annotation.",
        "model": "gpt-5"
      }
    ]
  }
]' />""",
    )

    validate_restricted_mdx(mdx)


def test_validate_restricted_mdx_reports_original_file_line(
    repo_with_stack: Repo,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(context, body="<Callout />")
    original_line = mdx.splitlines().index("<Callout />") + 1

    with pytest.raises(RecapError) as exc_info:
        validate_restricted_mdx(mdx)

    assert f"line {original_line}" in str(exc_info.value)
    assert "<Callout />" in str(exc_info.value)


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ('<Diff path="b.txt" tone="loud" />', "unsupported prop 'tone'"),
        (
            """<Diff
  path="b.txt"
  annotations='[
    {"line": 1, "side": "center", "text": "Bad side."}
  ]'
/>""",
            "allowed values",
        ),
        (
            (
                '<Diff path="b.txt" '
                'annotations=\'[{"side": "right", "text": "No line."}]\' />'
            ),
            "requires annotations\\[0\\].line or annotations\\[0\\].startLine",
        ),
        (
            '<DiffTabs files=\'[{"summary": "Missing path."}]\' />',
            "files\\[0\\].path",
        ),
        ("<Diff />", "missing required prop 'path'"),
        ("<Diff path=\"b.txt\" annotations='{}' />", "must be a JSON array"),
        (
            "<Diff path=\"b.txt\" annotations='[42]' />",
            "to be an object, got number",
        ),
        (
            '<Diff path="b.txt" annotations=\'[{"line": 1, "side": "right", '
            '"text": "ok", "extra": true}]\' />',
            "unsupported annotation key",
        ),
        (
            '<Diff path="b.txt" annotations=\'[{"line": 0, "side": "right", '
            '"text": "ok"}]\' />',
            "positive integer",
        ),
        (
            '<Diff path="b.txt" annotations=\'[{"startLine": 8, "endLine": 3, '
            '"side": "right", "text": "ok"}]\' />',
            "greater than or equal",
        ),
        (
            '<Diff path="b.txt" annotations=\'[{"line": 1, "side": "right", '
            '"text": "ok", "severity": "loud"}]\' />',
            "severity",
        ),
        (
            '<DiffTabs files=\'[{"path": "b.txt", "unexpected": true}]\' />',
            "unsupported file key",
        ),
        (
            "<DiffTabs files='[42]' />",
            "string or object, got number",
        ),
        (
            "<DiffTabs files='[\"\"]' />",
            "non-empty path",
        ),
        (
            '<DiffTabs files=\'[{"path": "b.txt", "summary": ""}]\' />',
            "files\\[0\\].summary",
        ),
        (
            '<DiffTabs files=\'[{"path": "b.txt", "annotations": {}}]\' />',
            "must be a JSON array",
        ),
        ("<Diff path=\"b.txt\" annotations='[}' />", "must be valid JSON"),
        ("<DiffTabs files='{}' />", "must be a JSON array"),
        (
            "<Diff path=\"b.txt\" annotations='[true]' />",
            "got boolean",
        ),
        (
            '<Diff path="b.txt" annotations=\'["bad"]\' />',
            "got string",
        ),
        (
            "<Diff path=\"b.txt\" annotations='[[]]' />",
            "got array",
        ),
        (
            "<Diff path=\"b.txt\" annotations='[null]' />",
            "got null",
        ),
    ],
)
def test_validate_restricted_mdx_validates_component_schemas(
    repo_with_stack: Repo,
    body: str,
    match: str,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(context, body=body)

    with pytest.raises(RecapError, match=match):
        validate_restricted_mdx(mdx)


def test_build_recap_context_errors_for_ambiguous_or_missing_sources(
    repo_with_stack: Repo,
) -> None:
    with pytest.raises(RecapError, match="either a branch or --working"):
        build_recap_context(repo_with_stack, branch="main", working=True)

    repo_with_stack.references.delete("refs/heads/branch_a")
    with pytest.raises(RecapError, match="Parent branch"):
        build_recap_context(repo_with_stack, branch="branch_b")


def test_build_recap_context_errors_for_untracked_default_branch(
    temp_repo: Repo,
) -> None:
    with pytest.raises(RecapError, match="not tracked"):
        build_recap_context(temp_repo)


def test_build_recap_context_errors_for_unknown_git_base(repo_with_stack: Repo) -> None:
    with pytest.raises(RecapError, match=r"does not exist|Needed a single revision"):
        build_recap_context(repo_with_stack, branch="missing-base")


def test_build_recap_context_errors_in_detached_head(repo_with_stack: Repo) -> None:
    set_ref(repo_with_stack, "HEAD", git.get_branch_head(repo_with_stack, "branch_b"))

    with pytest.raises(RecapError, match="detached HEAD"):
        build_recap_context(repo_with_stack)


def test_create_recap_rejects_branch_head_changes(
    repo_with_stack: Repo, tmp_path: Path
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    mdx = _mdx_from_context(context)

    switch_branch(repo_with_stack, "branch_b")
    commit_files(
        repo_with_stack,
        {tmp_path / "changed.txt": "changed\n"},
        "feat: change branch b",
    )

    with pytest.raises(RecapError, match="changed since context"):
        create_recap(repo_with_stack, mdx)


def test_create_working_recap_rejects_head_changes(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    (tmp_path / "working.txt").write_text("working content\n")
    context = build_recap_context(repo_with_stack, working=True)
    mdx = _mdx_from_context(context)

    switch_branch(repo_with_stack, "branch_b")
    commit_files(
        repo_with_stack,
        {tmp_path / "head-change.txt": "changed\n"},
        "feat: change head",
    )

    with pytest.raises(RecapError, match="HEAD changed"):
        create_recap(repo_with_stack, mdx)


def test_create_working_recap_ignores_untracked_mdx_argument(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    repo_path = Path(repo_with_stack.workdir)
    (tmp_path / "working.txt").write_text("working content\n")
    context = build_recap_context(repo_with_stack, working=True)
    mdx_path = repo_path / "recap.mdx"
    mdx_path.write_text(_mdx_from_context(context))

    stored = create_recap(repo_with_stack, mdx_path.read_text(), mdx_path=mdx_path)

    assert "working.txt" in stored.patch
    assert "recap.mdx" not in stored.patch


def test_create_working_recap_allows_mdx_path_outside_repo(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    (tmp_path / "working.txt").write_text("working content\n")
    context = build_recap_context(repo_with_stack, working=True)
    mdx_path = tmp_path.parent / "outside-recap.mdx"
    mdx_path.write_text(_mdx_from_context(context))

    stored = create_recap(repo_with_stack, mdx_path.read_text(), mdx_path=mdx_path)

    assert stored.meta.title == "Local recap"


def test_create_working_recap_allows_tracked_mdx_argument(
    repo_with_stack: Repo,
    tmp_path: Path,
) -> None:
    recap_path = tmp_path / "tracked-recap.mdx"
    recap_path.write_text("placeholder\n")
    commit_files(
        repo_with_stack, {recap_path: recap_path.read_text()}, "docs: add recap"
    )

    (tmp_path / "working.txt").write_text("working content\n")
    context = build_recap_context(repo_with_stack, working=True)

    stored = create_recap(
        repo_with_stack, _mdx_from_context(context), mdx_path=recap_path
    )

    assert "working.txt" in stored.patch


def test_create_recap_replaces_existing_temporary_directory(
    repo_with_stack: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    root = Path(repo_with_stack.path) / "shortcake" / "recaps"
    tmp_dir = root / ".tmp-fixed-id"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "stale.txt").write_text("stale")

    monkeypatch.setattr("shortcake._recap._new_recap_id", lambda: "fixed-id")
    stored = create_recap(repo_with_stack, _mdx_from_context(context))

    assert stored.meta.id == "fixed-id"
    assert not (root / ".tmp-fixed-id").exists()


def test_create_recap_cleans_up_temporary_directory_on_write_error(
    repo_with_stack: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    root = Path(repo_with_stack.path) / "shortcake" / "recaps"

    monkeypatch.setattr("shortcake._recap._new_recap_id", lambda: "fixed-id")
    monkeypatch.setattr(
        "shortcake._recap.os.replace",
        lambda *args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        create_recap(repo_with_stack, _mdx_from_context(context))

    assert not (root / ".tmp-fixed-id").exists()


def test_delete_recap_wraps_delete_errors(
    repo_with_stack: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    stored = create_recap(repo_with_stack, _mdx_from_context(context))

    monkeypatch.setattr(
        "shortcake._recap.shutil.rmtree",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("blocked")),
    )

    with pytest.raises(RecapError, match="Could not delete"):
        delete_recap(repo_with_stack, stored.meta.id)


def test_list_recaps_skips_invalid_metadata(repo_with_stack: Repo) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    stored = create_recap(repo_with_stack, _mdx_from_context(context))
    bad_dir = Path(repo_with_stack.path) / "shortcake" / "recaps" / "bad"
    bad_dir.mkdir()
    (bad_dir / "meta.json").write_text("{not json")

    assert [item.id for item in list_recaps(repo_with_stack)] == [stored.meta.id]


def test_load_recap_wraps_invalid_metadata(repo_with_stack: Repo) -> None:
    bad_dir = Path(repo_with_stack.path) / "shortcake" / "recaps" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "meta.json").write_text("{not json")
    (bad_dir / "recap.mdx").write_text("")
    (bad_dir / "patch.diff").write_text("")

    with pytest.raises(RecapError, match="Could not read recap"):
        load_recap(repo_with_stack, "bad")


def test_load_recap_reports_missing_recap(repo_with_stack: Repo) -> None:
    with pytest.raises(FileNotFoundError, match="was not found"):
        load_recap(repo_with_stack, "missing")


class FakeHandler:
    def __init__(self, path: str) -> None:
        self.path = path
        self._status: int | None = None
        self._headers: list[tuple[str, str]] = []
        self.wfile = __import__("io").BytesIO()

    def send_response(self, code: int) -> None:
        self._status = code

    def send_header(self, key: str, value: str) -> None:
        self._headers.append((key, value))

    def end_headers(self) -> None:
        pass

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def response_json(self) -> dict:
        return json.loads(self.wfile.getvalue())


def _make_handler(repo: Repo, path: str) -> FakeHandler:
    handler_cls = _build_request_handler(Path(repo.workdir))
    fake = FakeHandler(path)
    handler_cls.do_GET(fake)  # type: ignore[arg-type]
    return fake


def test_recap_api_list_and_show(repo_with_stack: Repo) -> None:
    context = build_recap_context(repo_with_stack, branch="branch_b")
    stored = create_recap(repo_with_stack, _mdx_from_context(context))

    list_handler = _make_handler(repo_with_stack, "/api/recaps")
    assert list_handler._status == 200
    assert list_handler.response_json()["recaps"][0]["id"] == stored.meta.id

    show_handler = _make_handler(repo_with_stack, f"/api/recaps/{stored.meta.id}")
    assert show_handler._status == 200
    data = show_handler.response_json()
    assert data["id"] == stored.meta.id
    assert data["mdx"].startswith("---")
    assert data["patch"] == context["patch"]


def test_recap_api_errors(repo_with_stack: Repo) -> None:
    missing_id = _make_handler(repo_with_stack, "/api/recaps/")
    assert missing_id._status == 400
    assert "Missing recap id" in missing_id.response_json()["error"]

    missing = _make_handler(repo_with_stack, "/api/recaps/missing")
    assert missing._status == 404

    with pytest.raises(RecapError, match="Invalid recap id"):
        load_recap(repo_with_stack, "../bad")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "shortcake.commands.ui.list_recaps",
            lambda repo: (_ for _ in ()).throw(RuntimeError("list failed")),
        )
        list_error = _make_handler(repo_with_stack, "/api/recaps")
    assert list_error._status == 500
    assert "list failed" in list_error.response_json()["error"]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "shortcake.commands.ui.load_recap",
            lambda repo, recap_id: (_ for _ in ()).throw(RecapError("bad recap")),
        )
        bad_recap = _make_handler(repo_with_stack, "/api/recaps/example")
    assert bad_recap._status == 400
    assert "bad recap" in bad_recap.response_json()["error"]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "shortcake.commands.ui.load_recap",
            lambda repo, recap_id: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        unexpected = _make_handler(repo_with_stack, "/api/recaps/example")
    assert unexpected._status == 500
    assert "boom" in unexpected.response_json()["error"]


def test_recap_cli_context_create_show_list_and_skill(
    repo_with_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    context_result = runner.invoke(app, ["recap", "context", "branch_b", "--json"])
    assert context_result.exit_code == 0
    context = json.loads(context_result.output)

    context_text_result = runner.invoke(app, ["recap", "context", "branch_b"])
    assert context_text_result.exit_code == 0
    assert "<FileMap />" in context_text_result.output

    mdx_path = tmp_path / "recap.mdx"
    mdx_path.write_text(_mdx_from_context(context))
    create_result = runner.invoke(app, ["recap", "create", "--mdx", f"@{mdx_path}"])
    assert create_result.exit_code == 0
    created = json.loads(create_result.output)

    validate_result = runner.invoke(app, ["recap", "validate", "--mdx", f"@{mdx_path}"])
    assert validate_result.exit_code == 0
    assert json.loads(validate_result.output)["valid"] is True

    validate_path_result = runner.invoke(
        app, ["recap", "validate", "--mdx", str(mdx_path)]
    )
    assert validate_path_result.exit_code == 0

    validate_inline_result = runner.invoke(
        app, ["recap", "validate", "--mdx", _mdx_from_context(context)]
    )
    assert validate_inline_result.exit_code == 0

    show_result = runner.invoke(app, ["recap", "show", created["id"], "--json"])
    assert show_result.exit_code == 0
    assert json.loads(show_result.output)["id"] == created["id"]

    show_text_result = runner.invoke(app, ["recap", "show", created["id"]])
    assert show_text_result.exit_code == 0
    assert created["id"] in show_text_result.output

    components_result = runner.invoke(app, ["recap", "components", "--json"])
    assert components_result.exit_code == 0
    components = json.loads(components_result.output)
    diff_schema = next(
        component
        for component in components["components"]
        if component["name"] == "Diff"
    )
    assert diff_schema["requiredProps"] == ["path"]
    assert "right" in components["annotation"]["sideValues"]

    components_text_result = runner.invoke(app, ["recap", "components"])
    assert components_text_result.exit_code == 0
    assert "<Diff>" in components_text_result.output
    assert "Annotation" in components_text_result.output

    list_result = runner.invoke(app, ["recap", "list", "--json"])
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)["recaps"][0]["id"] == created["id"]

    list_text_result = runner.invoke(app, ["recap", "list"])
    assert list_text_result.exit_code == 0
    assert created["id"] in list_text_result.output

    delete_result = runner.invoke(app, ["recap", "delete", created["id"], "--json"])
    assert delete_result.exit_code == 0
    assert json.loads(delete_result.output)["deleted"] == created["id"]

    delete_text_context = build_recap_context(repo_with_stack, branch="branch_b")
    delete_text_stored = create_recap(
        repo_with_stack, _mdx_from_context(delete_text_context)
    )
    delete_text_result = runner.invoke(
        app, ["recap", "delete", delete_text_stored.meta.id]
    )
    assert delete_text_result.exit_code == 0
    assert f"Deleted recap {delete_text_stored.meta.id}" in delete_text_result.output

    assert list_recaps(repo_with_stack) == []

    empty_list_result = runner.invoke(app, ["recap", "list"])
    assert empty_list_result.exit_code == 0
    assert "No local recaps found" in empty_list_result.output

    skill_result = runner.invoke(app, ["skill", "--print", "shortcake-visual-recap"])
    assert skill_result.exit_code == 0
    assert "shortcake recap context" in skill_result.output
    assert "shortcake recap validate" in skill_result.output
    assert "shortcake recap open <id> --background" in skill_result.output
    assert "Add inline annotations" in skill_result.output
    assert "Do not leave it as a bare" in skill_result.output
    assert "Use prose for one validation item" in skill_result.output

    skill_list_result = runner.invoke(app, ["skill"])
    assert skill_list_result.exit_code == 0
    assert "Available skills" in skill_list_result.output

    unknown_skill_result = runner.invoke(app, ["skill", "--print", "missing"])
    assert unknown_skill_result.exit_code == 1
    assert "Unknown skill" in unknown_skill_result.output


def test_recap_private_type_name_helper_covers_json_shapes() -> None:
    assert _recap._json_type_name({}) == "object"
    assert _recap._json_type_name(object()) == "object"


def test_recap_cli_errors(
    repo_with_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = Path(repo_with_stack.workdir)
    monkeypatch.chdir(repo_path)
    context = build_recap_context(repo_with_stack, branch="branch_b")
    bad_mdx = tmp_path / "bad.mdx"
    bad_mdx.write_text(_mdx_from_context(context, body="<Callout />"))

    context_result = runner.invoke(app, ["recap", "context", "main", "--working"])
    assert context_result.exit_code == 1
    assert "either a branch or --working" in context_result.output

    create_result = runner.invoke(app, ["recap", "create", "--mdx", f"@{bad_mdx}"])
    assert create_result.exit_code == 1
    assert "Unsupported MDX component" in create_result.output

    validate_result = runner.invoke(app, ["recap", "validate", "--mdx", f"@{bad_mdx}"])
    assert validate_result.exit_code == 1
    assert "Unsupported MDX component" in validate_result.output

    show_result = runner.invoke(app, ["recap", "show", "missing"])
    assert show_result.exit_code == 1
    assert "was not found" in show_result.output

    delete_result = runner.invoke(app, ["recap", "delete", "missing"])
    assert delete_result.exit_code == 1
    assert "was not found" in delete_result.output

    open_result = runner.invoke(app, ["recap", "open", "missing"])
    assert open_result.exit_code == 1
    assert "was not found" in open_result.output


def test_recap_cli_open_static_and_dev_paths(
    repo_with_stack: Repo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(Path(repo_with_stack.workdir))
    context = build_recap_context(repo_with_stack, branch="branch_b")
    stored = create_recap(repo_with_stack, _mdx_from_context(context))

    static_calls: list[dict[str, object]] = []

    def fake_open_static(*args: object, **kwargs: object) -> None:
        static_calls.append(kwargs)

    monkeypatch.setattr(
        "shortcake.commands.recap._open_or_start_static_ui", fake_open_static
    )
    static_result = runner.invoke(
        app,
        [
            "recap",
            "open",
            stored.meta.id,
            "--background",
            "--build-ui",
            "--skip-install",
            "--ui-port",
            "9000",
        ],
    )
    assert static_result.exit_code == 0
    assert static_calls[0]["background"] is True
    assert static_calls[0]["route_hash"] == f"#/recap/{stored.meta.id}"

    dev_background_result = runner.invoke(
        app,
        ["recap", "open", stored.meta.id, "--dev", "--background"],
    )
    assert dev_background_result.exit_code == 1
    assert "--background is only supported" in dev_background_result.output

    monkeypatch.setattr(
        "shortcake.commands.recap._resolve_frontend_dir", lambda _: None
    )
    missing_frontend_result = runner.invoke(
        app,
        ["recap", "open", stored.meta.id, "--dev"],
    )
    assert missing_frontend_result.exit_code == 1
    assert "frontend directory not found" in missing_frontend_result.output

    monkeypatch.setattr(
        "shortcake.commands.recap._resolve_frontend_dir", lambda _: tmp_path
    )
    monkeypatch.setattr("shortcake.commands.recap._resolve_js_runtime", lambda: None)
    missing_runtime_result = runner.invoke(
        app,
        ["recap", "open", stored.meta.id, "--dev"],
    )
    assert missing_runtime_result.exit_code == 1
    assert "Neither 'pybun' nor 'bun'" in missing_runtime_result.output

    mock_server = MagicMock()
    opened: list[str] = []
    monkeypatch.setattr("shortcake.commands.recap._resolve_js_runtime", lambda: "bun")
    monkeypatch.setattr(
        "shortcake.commands.recap._start_api_server_on_available_port",
        lambda *args, **kwargs: (mock_server, 9001),
    )
    monkeypatch.setattr("shortcake.commands.recap._find_open_port", lambda *args: 6174)
    monkeypatch.setattr("shortcake.commands.recap._run_install", lambda *args: "bun")
    monkeypatch.setattr(
        "shortcake.commands.recap.webbrowser.open", lambda url: opened.append(url)
    )
    monkeypatch.setattr("shortcake.commands.recap._run_dev_server", lambda *args: 0)
    dev_result = runner.invoke(
        app,
        [
            "recap",
            "open",
            stored.meta.id,
            "--dev",
            "--ui-port",
            "9000",
            "--web-port",
            "6173",
        ],
    )
    assert dev_result.exit_code == 0
    assert "Port 9000 is in use" in dev_result.output
    assert "Port 6173 is in use" in dev_result.output
    assert opened == [f"http://127.0.0.1:6174/#/recap/{stored.meta.id}"]
    mock_server.shutdown.assert_called()
    mock_server.server_close.assert_called()

    monkeypatch.setattr(
        "shortcake.commands.recap._start_api_server_on_available_port",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bind failed")),
    )
    bind_error_result = runner.invoke(
        app,
        ["recap", "open", stored.meta.id, "--dev"],
    )
    assert bind_error_result.exit_code == 1
    assert "bind failed" in bind_error_result.output

    monkeypatch.setattr(
        "shortcake.commands.recap._start_api_server_on_available_port",
        lambda *args, **kwargs: (mock_server, 9000),
    )
    monkeypatch.setattr("shortcake.commands.recap._run_dev_server", lambda *args: 2)
    nonzero_result = runner.invoke(
        app,
        ["recap", "open", stored.meta.id, "--dev"],
    )
    assert nonzero_result.exit_code == 2

    monkeypatch.setattr(
        "shortcake.commands.recap._run_install",
        lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    interrupted_result = runner.invoke(
        app,
        ["recap", "open", stored.meta.id, "--dev"],
    )
    assert interrupted_result.exit_code == 0

    monkeypatch.setattr(
        "shortcake.commands.recap._run_install",
        lambda *args: (_ for _ in ()).throw(ValueError("install failed")),
    )
    install_error_result = runner.invoke(
        app,
        ["recap", "open", stored.meta.id, "--dev"],
    )
    assert install_error_result.exit_code == 1
    assert "install failed" in install_error_result.output

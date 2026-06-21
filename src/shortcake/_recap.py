from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shortcake import _git as git
from shortcake._git._core import Repo

RECAP_VERSION = 1
RECAP_DIR = "recaps"
PATCH_HASH_PREFIX = "sha256:"
SUPPORTED_COMPONENTS = {
    "FileMap",
    "Diff",
    "DiffTabs",
    "Mermaid",
    "DataModel",
    "Endpoint",
    "StateSummary",
}

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_COMPONENT_START_RE = re.compile(r"<\s*(?P<closing>/?)\s*(?P<name>[A-Z][A-Za-z0-9]*)\b")
_ATTR_RE = re.compile(r"([A-Za-z_:][\w:.-]*)\s*=\s*(\"[^\"]*\"|'[^']*')")
ANNOTATION_SIDES = {"additions", "deletions", "left", "right"}
ANNOTATION_SEVERITIES = {"info", "warning", "danger"}
ANNOTATION_KEYS = {
    "line",
    "startLine",
    "endLine",
    "side",
    "title",
    "text",
    "severity",
    "model",
}
DIFF_TAB_FILE_KEYS = {"path", "summary", "annotations"}
COMPONENT_PROPS = {
    "FileMap": {
        "required": set(),
        "optional": set(),
        "example": "<FileMap />",
    },
    "Diff": {
        "required": {"path"},
        "optional": {"summary", "annotations"},
        "example": (
            '<Diff path="src/app.py" summary="Explains the change." '
            'annotations=\'[{"line": 12, "side": "right", '
            '"title": "Entrypoint", "text": "New behavior starts here."}]\' />'
        ),
    },
    "DiffTabs": {
        "required": {"files"},
        "optional": set(),
        "example": (
            '<DiffTabs files=\'[{"path": "src/app.py", "summary": "Main change."}]\' />'
        ),
    },
    "Mermaid": {
        "required": set(),
        "optional": {"title"},
        "example": '<Mermaid title="Flow">\\ngraph TD\\n  A --> B\\n</Mermaid>',
    },
    "DataModel": {
        "required": set(),
        "optional": {"title"},
        "example": (
            '<DataModel title="Payload">\\n```json\\n'
            '{"name": "Example"}\\n```\\n</DataModel>'
        ),
    },
    "Endpoint": {
        "required": set(),
        "optional": {"method", "path", "title"},
        "example": (
            '<Endpoint method="POST" path="/api/items">\\n```json\\n'
            '{"request": "ItemCreate"}\\n```\\n</Endpoint>'
        ),
    },
    "StateSummary": {
        "required": set(),
        "optional": {"title"},
        "example": (
            '<StateSummary title="State">\\n```json\\n'
            '{"before": "old", "after": "new"}\\n```\\n</StateSummary>'
        ),
    },
}


class RecapError(ValueError):
    """Raised when a local recap cannot be created or read."""


class RecapSource(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["branch", "working"]
    branch: str | None = None
    parent: str | None = None
    head: str
    patch_hash: str = Field(alias="patchHash")

    @field_validator("patch_hash")
    @classmethod
    def _validate_patch_hash(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("patchHash must be a sha256:<hex> digest")
        return value


class RecapFrontmatter(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    shortcake_recap: Literal[1] = Field(alias="shortcakeRecap")
    title: str
    source: RecapSource

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be empty")
        return value.strip()


class RecapFileStat(BaseModel):
    path: str
    additions: int
    deletions: int
    status: Literal["added", "deleted", "renamed", "modified"]


class RecapMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    created_at: str = Field(alias="createdAt")
    source: RecapSource
    files: list[RecapFileStat]


class StoredRecap(BaseModel):
    meta: RecapMeta
    mdx: str
    patch: str


class ValidatedRecap(BaseModel):
    frontmatter: RecapFrontmatter
    patch: str
    files: list[RecapFileStat]


def _run_git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _tracked_branch_parents(repo: Repo) -> dict[str, str]:
    all_branches = set(git.get_all_local_branches(repo))
    branch_heads = {name: git.get_branch_head(repo, name) for name in all_branches}

    tracked: dict[str, str] = {}
    for branch in all_branches:
        parent = git.get_branch_parent(repo, branch, all_branches, branch_heads)
        if parent is not None:
            tracked[branch] = parent

    return tracked


def patch_hash(patch: str) -> str:
    return PATCH_HASH_PREFIX + hashlib.sha256(patch.encode()).hexdigest()


def _pathspec_excludes(exclude_paths: set[str] | None) -> list[str]:
    if not exclude_paths:
        return []
    return [f":(exclude){path}" for path in sorted(exclude_paths)]


def build_branch_patch(repo_path: Path, parent: str, branch: str) -> str:
    result = _run_git(
        repo_path,
        [
            "diff",
            "--no-color",
            "--find-renames",
            "--full-index",
            f"{parent}...{branch}",
        ],
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "Failed to build diff patch"
        raise RecapError(message)
    return result.stdout


def _resolve_commit(repo_path: Path, revision: str) -> str:
    result = _run_git(repo_path, ["rev-parse", "--verify", f"{revision}^{{commit}}"])
    if result.returncode != 0:
        message = result.stderr.strip() or f"Revision '{revision}' does not exist"
        raise RecapError(message)
    return result.stdout.strip()


def build_working_patch(
    repo_path: Path,
    *,
    exclude_paths: set[str] | None = None,
) -> str:
    args = ["diff", "--no-color", "--find-renames", "--full-index", "HEAD"]
    excludes = _pathspec_excludes(exclude_paths)
    if excludes:
        args.extend(["--", ".", *excludes])

    result = _run_git(repo_path, args)
    if result.returncode != 0:
        message = result.stderr.strip() or "Failed to build working diff"
        raise RecapError(message)
    diff = result.stdout

    untracked = _run_git(repo_path, ["ls-files", "--others", "--exclude-standard"])
    for filepath in untracked.stdout.splitlines():
        filepath = filepath.strip()
        if not filepath or filepath in (exclude_paths or set()):
            continue

        full_path = repo_path / filepath
        try:
            content = full_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines(True)
        line_count = len(lines)
        body = "".join(
            f"+{line}"
            if line.endswith("\n")
            else f"+{line}\n\\ No newline at end of file\n"
            for line in lines
        )
        diff += (
            f"diff --git a/{filepath} b/{filepath}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{filepath}\n"
            f"@@ -0,0 +1,{line_count} @@\n"
            f"{body}"
        )

    return diff


def parse_patch_file_stats(patch: str) -> list[RecapFileStat]:
    if not patch.strip():
        return []

    sections = patch.split("\ndiff --git ")
    if sections and sections[0].startswith("diff --git "):
        sections[0] = sections[0][len("diff --git ") :]

    stats: list[RecapFileStat] = []
    for index, section in enumerate(sections):
        if not section.strip():
            continue
        file_patch = section if index == 0 else f"diff --git {section}"
        if not file_patch.startswith("diff --git "):
            file_patch = f"diff --git {file_patch}"

        path_match = re.search(r"^diff --git a/.+ b/(.+)$", file_patch, re.MULTILINE)
        path = path_match.group(1) if path_match else f"file-{index}"
        status: Literal["added", "deleted", "renamed", "modified"]
        if re.search(r"^new file mode ", file_patch, re.MULTILINE):
            status = "added"
        elif re.search(r"^deleted file mode ", file_patch, re.MULTILINE):
            status = "deleted"
        elif re.search(r"^rename from ", file_patch, re.MULTILINE):
            status = "renamed"
        else:
            status = "modified"

        additions = 0
        deletions = 0
        in_hunk = False
        for line in file_patch.splitlines():
            if line.startswith("@@"):
                in_hunk = True
                continue
            if not in_hunk:
                continue
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        stats.append(
            RecapFileStat(
                path=path,
                additions=additions,
                deletions=deletions,
                status=status,
            )
        )

    return stats


def split_frontmatter(mdx: str) -> tuple[RecapFrontmatter, str]:
    frontmatter, body, _line_offset = _split_frontmatter_with_line_offset(mdx)
    return frontmatter, body


def _split_frontmatter_with_line_offset(mdx: str) -> tuple[RecapFrontmatter, str, int]:
    match = _FRONTMATTER_RE.match(mdx)
    if match is None:
        raise RecapError("Recap MDX must start with YAML frontmatter")

    try:
        raw_frontmatter = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise RecapError(f"Invalid recap frontmatter: {exc}") from exc

    if not isinstance(raw_frontmatter, dict):
        raise RecapError("Recap frontmatter must be a YAML object")

    try:
        frontmatter = RecapFrontmatter.model_validate(raw_frontmatter)
    except ValidationError as exc:
        raise RecapError(f"Invalid recap frontmatter: {exc}") from exc

    source = frontmatter.source
    if source.kind == "branch" and (not source.branch or not source.parent):
        raise RecapError("Branch recaps require source.branch and source.parent")
    if source.kind == "working" and source.parent:
        raise RecapError("Working recaps must not set source.parent")

    return frontmatter, mdx[match.end() :], mdx[: match.end()].count("\n")


def _iter_non_fenced_lines(content: str, line_offset: int) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    in_fence = False
    for index, line in enumerate(content.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append((line_offset + index, line))
    return lines


def _one_line_snippet(value: str, *, limit: int = 140) -> str:
    snippet = " ".join(part.strip() for part in value.splitlines()).strip()
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 1].rstrip() + "..."


def _json_type_name(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _require_string(
    value: object,
    *,
    component: str,
    attr: str,
    path: str,
    line_number: int,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecapError(
            f"<{component}> {attr} on line {line_number} requires "
            f"{path} to be a non-empty string"
        )
    return value


def _require_positive_int(
    value: object,
    *,
    component: str,
    attr: str,
    path: str,
    line_number: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RecapError(
            f"<{component}> {attr} on line {line_number} requires "
            f"{path} to be a positive integer"
        )
    return value


def _validate_annotation_object(
    value: object,
    *,
    component: str,
    attr: str,
    path: str,
    line_number: int,
) -> None:
    if not isinstance(value, dict):
        raise RecapError(
            f"<{component}> {attr} on line {line_number} requires {path} "
            f"to be an object, got {_json_type_name(value)}"
        )

    unknown = sorted(set(value) - ANNOTATION_KEYS)
    if unknown:
        allowed = ", ".join(sorted(ANNOTATION_KEYS))
        raise RecapError(
            f"<{component}> {attr} on line {line_number} has unsupported "
            f"annotation key {unknown[0]!r}; allowed keys: {allowed}"
        )

    _require_string(
        value.get("text"),
        component=component,
        attr=attr,
        path=f"{path}.text",
        line_number=line_number,
    )
    if "title" in value:
        _require_string(
            value["title"],
            component=component,
            attr=attr,
            path=f"{path}.title",
            line_number=line_number,
        )
    if "model" in value:
        _require_string(
            value["model"],
            component=component,
            attr=attr,
            path=f"{path}.model",
            line_number=line_number,
        )
    if "severity" in value:
        severity = _require_string(
            value["severity"],
            component=component,
            attr=attr,
            path=f"{path}.severity",
            line_number=line_number,
        )
        if severity not in ANNOTATION_SEVERITIES:
            allowed = ", ".join(sorted(ANNOTATION_SEVERITIES))
            raise RecapError(
                f"<{component}> {attr} on line {line_number} has invalid "
                f"{path}.severity {severity!r}; allowed values: {allowed}"
            )

    side = _require_string(
        value.get("side"),
        component=component,
        attr=attr,
        path=f"{path}.side",
        line_number=line_number,
    )
    if side not in ANNOTATION_SIDES:
        allowed = ", ".join(sorted(ANNOTATION_SIDES))
        raise RecapError(
            f"<{component}> {attr} on line {line_number} has invalid "
            f"{path}.side {side!r}; allowed values: {allowed}"
        )

    has_line = "line" in value
    has_start_line = "startLine" in value
    has_end_line = "endLine" in value
    if not has_line and not has_start_line:
        raise RecapError(
            f"<{component}> {attr} on line {line_number} requires {path}.line "
            f"or {path}.startLine"
        )

    if has_line:
        _require_positive_int(
            value["line"],
            component=component,
            attr=attr,
            path=f"{path}.line",
            line_number=line_number,
        )
    start_line: int | None = None
    end_line: int | None = None
    if has_start_line:
        start_line = _require_positive_int(
            value["startLine"],
            component=component,
            attr=attr,
            path=f"{path}.startLine",
            line_number=line_number,
        )
    if has_end_line:
        end_line = _require_positive_int(
            value["endLine"],
            component=component,
            attr=attr,
            path=f"{path}.endLine",
            line_number=line_number,
        )
    if start_line is not None and end_line is not None and end_line < start_line:
        raise RecapError(
            f"<{component}> {attr} on line {line_number} requires {path}.endLine "
            "to be greater than or equal to startLine"
        )


def _validate_annotations_payload(
    value: object,
    *,
    component: str,
    attr: str,
    line_number: int,
) -> None:
    if not isinstance(value, list):
        raise RecapError(
            f"<{component}> {attr} on line {line_number} must be a JSON array"
        )
    for index, item in enumerate(value):
        _validate_annotation_object(
            item,
            component=component,
            attr=attr,
            path=f"{attr}[{index}]",
            line_number=line_number,
        )


def _validate_diff_tabs_payload(
    value: object,
    *,
    component: str,
    attr: str,
    line_number: int,
) -> None:
    if not isinstance(value, list):
        raise RecapError(
            f"<{component}> {attr} on line {line_number} must be a JSON array"
        )

    for index, item in enumerate(value):
        if isinstance(item, str):
            if not item.strip():
                raise RecapError(
                    f"<{component}> {attr} on line {line_number} requires "
                    f"{attr}[{index}] to be a non-empty path"
                )
            continue
        if not isinstance(item, dict):
            raise RecapError(
                f"<{component}> {attr} on line {line_number} requires "
                f"{attr}[{index}] to be a string or object, got "
                f"{_json_type_name(item)}"
            )

        unknown = sorted(set(item) - DIFF_TAB_FILE_KEYS)
        if unknown:
            allowed = ", ".join(sorted(DIFF_TAB_FILE_KEYS))
            raise RecapError(
                f"<{component}> {attr} on line {line_number} has unsupported "
                f"file key {unknown[0]!r}; allowed keys: {allowed}"
            )
        _require_string(
            item.get("path"),
            component=component,
            attr=attr,
            path=f"{attr}[{index}].path",
            line_number=line_number,
        )
        if "summary" in item:
            _require_string(
                item["summary"],
                component=component,
                attr=attr,
                path=f"{attr}[{index}].summary",
                line_number=line_number,
            )
        if "annotations" in item:
            _validate_annotations_payload(
                item["annotations"],
                component=component,
                attr=f"{attr}[{index}].annotations",
                line_number=line_number,
            )


def _parse_json_attr(
    *,
    component: str,
    attr: str,
    value: str,
    line_number: int,
) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise RecapError(
            f"<{component}> {attr} on line {line_number} must be valid JSON: {exc.msg}"
        ) from exc


def _validate_json_props(
    component: str,
    parsed_attrs: dict[str, str],
    line_number: int,
) -> None:
    if component == "Diff" and "annotations" in parsed_attrs:
        _validate_annotations_payload(
            _parse_json_attr(
                component=component,
                attr="annotations",
                value=parsed_attrs["annotations"],
                line_number=line_number,
            ),
            component=component,
            attr="annotations",
            line_number=line_number,
        )
    if component == "DiffTabs" and "files" in parsed_attrs:
        _validate_diff_tabs_payload(
            _parse_json_attr(
                component=component,
                attr="files",
                value=parsed_attrs["files"],
                line_number=line_number,
            ),
            component=component,
            attr="files",
            line_number=line_number,
        )


def _parse_component_attrs(
    name: str,
    attrs: str,
    line_number: int,
    raw_tag: str,
) -> dict[str, str]:
    event_match = re.search(r"\b(on[A-Z][A-Za-z0-9_]*)\s*=", attrs)
    if event_match:
        raise RecapError(
            f"<{name}> prop {event_match.group(1)!r} on line {line_number} "
            f"is an event handler; event props are not supported: "
            f"{_one_line_snippet(raw_tag)}"
        )

    expression_match = re.search(r"([A-Za-z_:][\w:.-]*)\s*=\s*{", attrs)
    if expression_match:
        raise RecapError(
            f"<{name}> prop {expression_match.group(1)!r} on line {line_number} "
            f"uses a JS expression; use a quoted static string: "
            f"{_one_line_snippet(raw_tag)}"
        )

    parsed_attrs: dict[str, str] = {}
    consumed: list[tuple[int, int]] = []
    for match in _ATTR_RE.finditer(attrs):
        attr_name = match.group(1)
        if attr_name in parsed_attrs:
            raise RecapError(
                f"<{name}> on line {line_number} repeats prop {attr_name!r}"
            )
        parsed_attrs[attr_name] = match.group(2)[1:-1]
        consumed.append(match.span())

    remainder = attrs
    for start, end in reversed(consumed):
        remainder = remainder[:start] + " " * (end - start) + remainder[end:]

    if remainder.strip().strip("/"):
        raise RecapError(
            f"<{name}> on line {line_number} has a non-static prop near "
            f"{_one_line_snippet(remainder)!r}: {_one_line_snippet(raw_tag)}"
        )

    return parsed_attrs


def _validate_component_attrs(
    name: str,
    attrs: str,
    line_number: int,
    raw_tag: str,
) -> None:
    parsed_attrs = _parse_component_attrs(name, attrs, line_number, raw_tag)
    schema = COMPONENT_PROPS[name]
    required = schema["required"]
    optional = schema["optional"]
    allowed = set(required) | set(optional)

    missing = sorted(required - set(parsed_attrs))
    if missing:
        raise RecapError(
            f"<{name}> on line {line_number} is missing required prop {missing[0]!r}"
        )

    unknown = sorted(set(parsed_attrs) - allowed)
    if unknown:
        allowed_text = ", ".join(sorted(allowed)) or "none"
        raise RecapError(
            f"<{name}> on line {line_number} has unsupported prop "
            f"{unknown[0]!r}; allowed props: {allowed_text}"
        )

    _validate_json_props(name, parsed_attrs, line_number)


def _find_unquoted_tag_end(value: str, *, start: int = 0) -> int | None:
    quote: str | None = None
    for index in range(start, len(value)):
        char = value[index]
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == ">":
            return index
    return None


def _iter_component_tags(
    value: str,
    *,
    start_line_number: int,
) -> list[tuple[str, str, bool, int, str]]:
    tags: list[tuple[str, str, bool, int, str]] = []
    search_from = 0
    while True:
        match = _COMPONENT_START_RE.search(value, search_from)
        if match is None:
            return tags

        tag_end = _find_unquoted_tag_end(value, start=match.end())
        if tag_end is None:
            line_number = start_line_number + value[: match.start()].count("\n")
            raise RecapError(
                f"Unclosed MDX component <{match.group('name')}> starting on "
                f"line {line_number}: {_one_line_snippet(value[match.start() :])}"
            )

        line_number = start_line_number + value[: match.start()].count("\n")
        raw_tag = value[match.start() : tag_end + 1]
        attrs = value[match.end() : tag_end]
        tags.append(
            (
                match.group("name"),
                attrs,
                bool(match.group("closing")),
                line_number,
                raw_tag,
            )
        )
        search_from = tag_end + 1


def _validate_component_tags(value: str, start_line_number: int) -> None:
    for name, attrs, closing, line_number, raw_tag in _iter_component_tags(
        value,
        start_line_number=start_line_number,
    ):
        if name not in SUPPORTED_COMPONENTS:
            raise RecapError(
                f"Unsupported MDX component <{name}> on line {line_number}: "
                f"{_one_line_snippet(raw_tag)}"
            )
        if not closing:
            _validate_component_attrs(name, attrs, line_number, raw_tag)


def _starts_incomplete_component_tag(line: str) -> bool:
    match = _COMPONENT_START_RE.search(line)
    if match is None:
        return False
    return _find_unquoted_tag_end(line, start=match.end()) is None


def validate_restricted_mdx(mdx: str) -> RecapFrontmatter:
    frontmatter, body, line_offset = _split_frontmatter_with_line_offset(mdx)

    pending_tag: list[str] = []
    pending_line_number: int | None = None

    for line_number, line in _iter_non_fenced_lines(body, line_offset):
        if pending_tag:
            pending_tag.append(line)
            joined = "\n".join(pending_tag)
            if _find_unquoted_tag_end(joined) is not None:
                _validate_component_tags(joined, pending_line_number or line_number)
                pending_tag = []
                pending_line_number = None
            continue

        if _starts_incomplete_component_tag(line):
            pending_tag = [line]
            pending_line_number = line_number
            continue

        if re.match(r"\s*(import|export)\b", line):
            raise RecapError(
                f"MDX import/export is not supported on line {line_number}"
            )
        if re.match(r"\s*{.*}\s*$", line):
            raise RecapError(
                f"MDX expression is not supported on line {line_number}: "
                f"{_one_line_snippet(line)}"
            )

        _validate_component_tags(line, line_number)

    if pending_tag:
        _validate_component_tags("\n".join(pending_tag), pending_line_number or 1)

    return frontmatter


def _current_head(repo: Repo) -> str:
    if repo.head_is_unborn:  # pragma: no cover
        raise RecapError("Cannot create a recap before the first commit")
    return str(repo.head.target)


def _source_for_branch(repo: Repo, branch: str) -> tuple[RecapSource, str]:
    tracked = _tracked_branch_parents(repo)
    if branch not in tracked:  # pragma: no cover
        raise RecapError(f"Branch '{branch}' is not tracked by Shortcake")

    parent = tracked[branch]
    all_branches = set(git.get_all_local_branches(repo))
    if parent not in all_branches:
        raise RecapError(f"Parent branch '{parent}' does not exist locally")

    head = git.get_branch_head(repo, branch).decode()
    patch = build_branch_patch(Path(repo.workdir), parent, branch)
    source = RecapSource(
        kind="branch",
        branch=branch,
        parent=parent,
        head=head,
        patchHash=patch_hash(patch),
    )
    return source, patch


def _source_for_git_base(
    repo: Repo,
    base: str,
    *,
    target_ref: str | None = None,
) -> tuple[RecapSource, str]:
    repo_path = Path(repo.workdir)
    resolved_target = target_ref or git.get_current_branch(repo) or "HEAD"

    _resolve_commit(repo_path, base)
    head = _resolve_commit(repo_path, resolved_target)
    patch = build_branch_patch(repo_path, base, resolved_target)
    source = RecapSource(
        kind="branch",
        branch=resolved_target,
        parent=base,
        head=head,
        patchHash=patch_hash(patch),
    )
    return source, patch


def _source_for_default_base(
    repo: Repo,
    current_branch: str,
) -> tuple[RecapSource, str]:
    default_branch = git.get_default_branch(repo)
    if default_branch and default_branch != current_branch:
        return _source_for_git_base(repo, default_branch, target_ref=current_branch)

    raise RecapError(
        f"Branch '{current_branch}' is not tracked by Shortcake. "
        "Pass a git base revision to diff against, for example "
        "`shortcake recap context main --json`."
    )


def _source_for_working(
    repo: Repo,
    *,
    exclude_paths: set[str] | None = None,
) -> tuple[RecapSource, str]:
    patch = build_working_patch(Path(repo.workdir), exclude_paths=exclude_paths)
    source = RecapSource(
        kind="working",
        branch=git.get_current_branch(repo),
        head=_current_head(repo),
        patchHash=patch_hash(patch),
    )
    return source, patch


def _frontmatter_yaml(title: str, source: RecapSource) -> str:
    payload = {
        "shortcakeRecap": RECAP_VERSION,
        "title": title,
        "source": source.model_dump(mode="json", by_alias=True, exclude_none=True),
    }
    return yaml.safe_dump(payload, sort_keys=False).strip()


def _build_template(title: str, source: RecapSource, files: list[RecapFileStat]) -> str:
    diff_blocks = "\n".join(
        f'<Diff path="{file.path}" summary="Explain the change in this file." />'
        for file in files[:3]
    )
    body = f"""# {title}

## Summary
- Describe the user-visible outcome.
- Call out the implementation path and any review risks.

<FileMap />

## Key Changes
Use `annotations='[...]'` on `Diff` or `DiffTabs` entries for important lines.
Each annotation should include `line` or `startLine`/`endLine`, `side`, `title`,
and `text`.
Attributes are JSX-style quoted strings: use double quotes for plain prose,
single quotes for JSON payloads, and avoid backslash-escaped inner quotes.

{diff_blocks or "- No file changes were captured."}

## Validation
Write a short validation summary before any command list. Say what passed,
failed, was manually checked, or was not run, then list the evidence when
there are multiple commands or checks. Use prose for a single validation item.
"""
    return f"---\n{_frontmatter_yaml(title, source)}\n---\n\n{body}"


def _recap_title_for_source(source: RecapSource) -> str:
    if source.kind == "working":
        return "Working changes"
    return source.branch or "Branch recap"


def build_recap_context(
    repo: Repo,
    *,
    branch: str | None = None,
    working: bool = False,
) -> dict[str, Any]:
    if branch and working:
        raise RecapError("Pass either a branch or --working, not both")

    if working:
        source, patch = _source_for_working(repo)
    else:
        resolved_branch = branch or git.get_current_branch(repo)
        if resolved_branch is None:
            raise RecapError("Cannot infer branch in detached HEAD state")
        tracked = _tracked_branch_parents(repo)
        if branch is not None and resolved_branch not in tracked:
            source, patch = _source_for_git_base(repo, branch)
        elif resolved_branch in tracked:
            source, patch = _source_for_branch(repo, resolved_branch)
        else:
            source, patch = _source_for_default_base(repo, resolved_branch)

    files = parse_patch_file_stats(patch)
    title = _recap_title_for_source(source)
    template = _build_template(title, source, files)

    return {
        "source": source.model_dump(mode="json", by_alias=True, exclude_none=True),
        "patchHash": source.patch_hash,
        "patch": patch,
        "files": [file.model_dump(mode="json") for file in files],
        "template": template,
    }


def _git_relative_untracked_path(repo_path: Path, path: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(repo_path.resolve())
    except ValueError:
        return None

    result = _run_git(repo_path, ["ls-files", "--error-unmatch", str(relative)])
    if result.returncode == 0:
        return None
    return str(relative)


def _validated_patch_for_source(
    repo: Repo,
    source: RecapSource,
    *,
    mdx_path: Path | None = None,
) -> str:
    repo_path = Path(repo.workdir)

    if source.kind == "branch":
        if not source.branch or not source.parent:  # pragma: no cover
            raise RecapError("Branch recaps require source.branch and source.parent")
        current_head = _resolve_commit(repo_path, source.branch)
        if current_head != source.head:
            raise RecapError(
                f"Ref '{source.branch}' changed since context was generated"
            )
        patch = build_branch_patch(repo_path, source.parent, source.branch)
    else:
        exclude_paths: set[str] = set()
        if mdx_path is not None:
            relative = _git_relative_untracked_path(repo_path, mdx_path)
            if relative is not None:
                exclude_paths.add(relative)
        current_head = _current_head(repo)
        if current_head != source.head:
            raise RecapError("HEAD changed since context was generated")
        patch = build_working_patch(
            repo_path,
            exclude_paths=exclude_paths or None,
        )

    current_hash = patch_hash(patch)
    if current_hash != source.patch_hash:
        raise RecapError(
            "Current patch hash does not match recap frontmatter "
            f"({current_hash} != {source.patch_hash})"
        )

    return patch


def validate_recap(
    repo: Repo,
    mdx: str,
    *,
    mdx_path: Path | None = None,
) -> ValidatedRecap:
    frontmatter = validate_restricted_mdx(mdx)
    patch = _validated_patch_for_source(repo, frontmatter.source, mdx_path=mdx_path)
    files = parse_patch_file_stats(patch)
    return ValidatedRecap(frontmatter=frontmatter, patch=patch, files=files)


def _recaps_root(repo: Repo) -> Path:
    root = Path(repo.path) / "shortcake" / RECAP_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _new_recap_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(3)}"


def create_recap(
    repo: Repo,
    mdx: str,
    *,
    mdx_path: Path | None = None,
) -> StoredRecap:
    validated = validate_recap(repo, mdx, mdx_path=mdx_path)
    frontmatter = validated.frontmatter
    patch = validated.patch
    files = validated.files
    root = _recaps_root(repo)

    for _ in range(10):
        recap_id = _new_recap_id()
        target_dir = root / recap_id
        if not target_dir.exists():
            break
    else:  # pragma: no cover
        raise RecapError("Could not generate a unique recap id")

    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    meta = RecapMeta(
        id=recap_id,
        title=frontmatter.title,
        createdAt=created_at,
        source=frontmatter.source,
        files=files,
    )
    stored = StoredRecap(meta=meta, mdx=mdx, patch=patch)

    tmp_dir = root / f".tmp-{recap_id}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    try:
        (tmp_dir / "recap.mdx").write_text(mdx)
        (tmp_dir / "patch.diff").write_text(patch)
        (tmp_dir / "meta.json").write_text(
            json.dumps(meta.model_dump(mode="json", by_alias=True), indent=2) + "\n"
        )
        os.replace(tmp_dir, target_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return stored


def delete_recap(repo: Repo, recap_id: str) -> RecapMeta:
    stored = load_recap(repo, recap_id)
    recap_dir = _recaps_root(repo) / recap_id
    try:
        shutil.rmtree(recap_dir)
    except OSError as exc:
        raise RecapError(f"Could not delete recap '{recap_id}': {exc}") from exc
    return stored.meta


def list_recaps(repo: Repo) -> list[RecapMeta]:
    root = _recaps_root(repo)
    metas: list[RecapMeta] = []
    for meta_path in root.glob("*/meta.json"):
        try:
            meta = RecapMeta.model_validate_json(meta_path.read_text())
        except (OSError, ValidationError):
            continue
        metas.append(meta)
    return sorted(metas, key=lambda item: item.created_at, reverse=True)


def load_recap(repo: Repo, recap_id: str) -> StoredRecap:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", recap_id):
        raise RecapError("Invalid recap id")

    recap_dir = _recaps_root(repo) / recap_id
    try:
        meta = RecapMeta.model_validate_json((recap_dir / "meta.json").read_text())
        mdx = (recap_dir / "recap.mdx").read_text()
        patch = (recap_dir / "patch.diff").read_text()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Recap '{recap_id}' was not found") from exc
    except (OSError, ValidationError) as exc:
        raise RecapError(f"Could not read recap '{recap_id}': {exc}") from exc

    return StoredRecap(meta=meta, mdx=mdx, patch=patch)


def stored_recap_payload(stored: StoredRecap) -> dict[str, Any]:
    payload = stored.meta.model_dump(mode="json", by_alias=True)
    payload["mdx"] = stored.mdx
    payload["patch"] = stored.patch
    return payload


def validated_recap_payload(validated: ValidatedRecap) -> dict[str, Any]:
    return {
        "valid": True,
        "title": validated.frontmatter.title,
        "source": validated.frontmatter.source.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        ),
        "files": [file.model_dump(mode="json") for file in validated.files],
        "patchHash": validated.frontmatter.source.patch_hash,
    }


def recap_component_schema_payload() -> dict[str, Any]:
    return {
        "components": [
            {
                "name": name,
                "requiredProps": sorted(schema["required"]),
                "optionalProps": sorted(schema["optional"]),
                "example": schema["example"],
            }
            for name, schema in COMPONENT_PROPS.items()
        ],
        "annotation": {
            "required": ["line or startLine", "side", "text"],
            "optional": ["endLine", "title", "severity", "model"],
            "sideValues": sorted(ANNOTATION_SIDES),
            "severityValues": sorted(ANNOTATION_SEVERITIES),
            "lineSemantics": (
                "Use right/additions for new-file lines and left/deletions for "
                "old-file lines."
            ),
        },
        "quoting": (
            "Attributes are JSX-style quoted strings. Use double quotes for "
            "plain prose and single quotes for JSON payloads; backslash-escaped "
            "inner quotes do not protect double-quoted attributes."
        ),
    }

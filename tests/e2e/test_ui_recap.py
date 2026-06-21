import json
import re
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import Page, expect

from shortcake._recap import build_recap_context, create_recap
from tests._git_helpers import Repo

pytestmark = pytest.mark.e2e


def _mdx_from_context(context: dict, body: str) -> str:
    frontmatter = {
        "shortcakeRecap": 1,
        "title": "Visual recap",
        "source": context["source"],
    }
    return f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n{body}\n"


def _simple_patch(path: str) -> str:
    blob = f"{sum(path.encode()) % 0xFFFFFFF:07x}"
    function_name = re.sub(r"\W+", "_", path)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        f"index 0000000..{blob}\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1,2 @@\n"
        f"+def example_{function_name}():\n"
        f"+    return {path!r}\n"
    )


def _recap_payload(
    recap_id: str,
    mdx: str,
    patch: str,
    *,
    title: str = "File map select",
) -> dict:
    return {
        "id": recap_id,
        "title": title,
        "createdAt": "2026-06-20T00:00:00Z",
        "source": {
            "kind": "working",
            "branch": "feature",
            "head": "abc123",
            "patchHash": "sha256:" + "0" * 64,
        },
        "files": [],
        "patch": patch,
        "mdx": mdx,
    }


@pytest.fixture
def visual_recap_id(e2e_repo: Repo, tmp_path: Path) -> str:
    context = build_recap_context(e2e_repo, branch="branch_b")
    annotations = json.dumps(
        [
            {
                "line": 1,
                "side": "right",
                "title": "Branch entrypoint",
                "text": "Entry point for the new branch behavior.",
                "severity": "info",
            }
        ],
        indent=2,
    )
    tabs = json.dumps(
        [
            {
                "path": "feature_b.py",
                "summary": "Tabbed diff summary.",
                "annotations": [
                    {
                        "line": 1,
                        "side": "right",
                        "text": "Tabbed annotation.",
                    }
                ],
            }
        ],
        indent=2,
    )
    mdx = _mdx_from_context(
        context,
        f"""# Visual Recap

## Summary
- Adds the farewell branch behavior and keeps the explanation connected
  across a wrapped continuation line.
- Shows a second summary item in the same styled list.

<FileMap />

<Diff path="feature_b.py" summary="Adds farewell." annotations='{annotations}' />

<DiffTabs files='{tabs}' />

```mermaid
flowchart TD
  A["Start<br/>request"] --> B["Done"]
  B -->|@defer / @stream| C["Patch<br/>frames"]
```

<DataModel title="Feature Model">
```json
{{"entity": "FeatureB", "owner": "tests"}}
```
</DataModel>
""",
    )
    mdx_path = tmp_path / "visual-recap.mdx"
    mdx_path.write_text(mdx)
    return create_recap(e2e_repo, mdx, mdx_path=mdx_path).meta.id


def test_recap_route_renders_stored_mdx(
    page: Page,
    ui_url: str,
    visual_recap_id: str,
) -> None:
    page.goto(f"{ui_url}#/recap/{visual_recap_id}")

    expect(page.locator("main > section > header")).to_contain_text("Viewing recap")
    expect(page.locator("article")).to_contain_text("Visual recap")
    expect(page.locator("main")).to_contain_text("Files changed")
    article_header = page.locator("article > header")
    expect(article_header).to_contain_text("Visual recap")
    expect(article_header).not_to_contain_text("branch_b -> branch_a")
    expect(article_header).not_to_contain_text("1 file")
    expect(page.locator("article")).to_contain_text("feature_b.py")
    expect(page.locator("article")).to_contain_text("Adds farewell.")
    expect(page.locator("article")).to_contain_text(
        "Entry point for the new branch behavior."
    )
    inline_comment = page.locator("[data-recap-inline-comment]").first
    expect(inline_comment).to_contain_text("Branch entrypoint")
    expect(inline_comment).not_to_contain_text("shortcake review")
    expect(inline_comment).not_to_contain_text("recap")
    expect(inline_comment).not_to_contain_text("info")
    expect(
        page.locator('[data-recap-diff-path="feature_b.py"] > div > h2')
    ).to_have_count(0)
    file_entry = page.locator(
        'file-tree-container [data-item-type="file"][data-item-path="feature_b.py"]'
    )
    expect(file_entry).to_be_visible()
    host_box = page.locator("file-tree-container").bounding_box()
    file_box = file_entry.bounding_box()
    assert host_box is not None
    assert file_box is not None
    assert file_box["y"] + 1 >= host_box["y"]
    assert file_box["y"] + file_box["height"] <= host_box["y"] + host_box["height"] + 1
    expect(page.get_by_role("button", name="feature_b.py")).to_be_visible()
    page.get_by_role("link", name="Summary").click()
    page.wait_for_timeout(100)
    expect(page.locator("article")).to_contain_text(
        "Adds the farewell branch behavior and keeps the explanation connected "
        "across a wrapped continuation line."
    )
    first_list = page.locator("article ul").first
    expect(first_list.locator("li")).to_have_count(2)
    assert (
        first_list.evaluate("(node) => getComputedStyle(node).listStyleType") == "disc"
    )
    assert page.evaluate("window.location.hash").startswith(
        f"#/recap/{visual_recap_id}?section=recap-summary-"
    )
    expect(page.locator("[data-recap-mermaid-svg] svg")).to_be_visible()
    expect(page.locator("article")).not_to_contain_text("flowchart TD")
    expect(page.locator("article")).to_contain_text("FeatureB")


def test_recap_file_map_selects_tabbed_and_unmentioned_files(
    page: Page,
    ui_url: str,
) -> None:
    recap_id = "file-map-select"
    tabs = json.dumps([{"path": "src/core/b.py"}, {"path": "src/web/a.py"}])
    mdx = (
        "---\n"
        "shortcakeRecap: 1\n"
        "title: File map select\n"
        "source:\n"
        "  kind: working\n"
        "  branch: feature\n"
        "  head: abc123\n"
        f"  patchHash: sha256:{'0' * 64}\n"
        "---\n\n"
        "# File Map Select\n\n"
        "## Summary\n\n"
        "This summary should stay above selected file diffs.\n\n"
        "<FileMap />\n\n"
        "## Key Changes\n\n"
        f"<DiffTabs files='{tabs}' />\n"
    )
    patch = (
        _simple_patch("src/core/c.py")
        + "\n"
        + _simple_patch("src/web/a.py")
        + "\n"
        + _simple_patch("src/core/b.py")
    )

    page.route(
        re.compile(r"/api/recaps/file-map-select$"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _recap_payload(
                    recap_id,
                    mdx,
                    patch,
                    title="Recap: main...feature",
                )
            ),
        ),
    )

    page.goto(f"{ui_url}#/recap/{recap_id}")

    article_header = page.locator("article > header")
    expect(article_header).to_contain_text("feature")
    expect(article_header).not_to_contain_text("Recap:")
    expect(article_header).not_to_contain_text("main...")

    file_map_overflow = page.locator("[data-recap-file-map-list]").evaluate(
        "(node) => getComputedStyle(node).overflowY"
    )
    assert file_map_overflow == "visible"

    file_paths = page.locator(
        'file-tree-container [data-item-type="file"]'
    ).evaluate_all("(nodes) => nodes.map((node) => node.dataset.itemPath)")
    assert file_paths == ["src/core/b.py", "src/web/a.py", "src/core/c.py"]

    page.locator(
        'file-tree-container [data-item-type="file"][data-item-path="src/web/a.py"]'
    ).click()
    expect(page.locator('[data-recap-diff-path="src/web/a.py"]')).to_be_visible()
    expect(page.locator('[data-recap-diff-path="src/web/a.py"]')).to_contain_text(
        "src/web/a.py"
    )

    page.locator(
        'file-tree-container [data-item-type="file"][data-item-path="src/core/c.py"]'
    ).click()
    selected_diff = page.locator('[data-recap-diff-path="src/core/c.py"]')
    expect(selected_diff).to_be_visible()
    expect(selected_diff).to_contain_text("src/core/c.py")
    other_files_section = page.locator("article [data-recap-other-files-section]")
    expect(other_files_section).to_be_visible()
    expect(other_files_section).to_contain_text("Other files")
    expect(other_files_section).to_contain_text("src/core/c.py")
    positions = page.locator("article").evaluate(
        """(article) => {
            const headings = Array.from(article.querySelectorAll('h2'));
            const summary = headings.find((node) => node.textContent === 'Summary');
            const keyChanges = headings.find(
                (node) => node.textContent === 'Key Changes',
            );
            const selected = article.querySelector(
                '[data-recap-diff-path="src/core/c.py"]',
            );
            const otherFiles = article.querySelector(
                '[data-recap-other-files-section]',
            );
            return {
                summary: summary?.getBoundingClientRect().top,
                selected: selected?.getBoundingClientRect().top,
                keyChanges: keyChanges?.getBoundingClientRect().top,
                otherFiles: otherFiles?.getBoundingClientRect().top,
            };
        }"""
    )
    assert positions["summary"] < positions["keyChanges"]
    assert positions["keyChanges"] < positions["otherFiles"] <= positions["selected"]


def test_recap_route_reports_unknown_components(page: Page, ui_url: str) -> None:
    bad_mdx = (
        "---\n"
        "shortcakeRecap: 1\n"
        "title: Bad\n"
        "source:\n"
        "  kind: branch\n"
        "  branch: branch_b\n"
        "  parent: branch_a\n"
        "  head: abc123\n"
        f"  patchHash: sha256:{'0' * 64}\n"
        "---\n\n"
        "<Callout />\n"
    )

    page.route(
        re.compile(r"/api/recaps/bad-component$"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "id": "bad-component",
                    "title": "Bad",
                    "createdAt": "2026-06-20T00:00:00Z",
                    "source": {
                        "kind": "branch",
                        "branch": "branch_b",
                        "parent": "branch_a",
                        "head": "abc123",
                        "patchHash": "sha256:" + "0" * 64,
                    },
                    "files": [],
                    "patch": _simple_patch("example.py"),
                    "mdx": bad_mdx,
                }
            ),
        ),
    )

    page.goto(f"{ui_url}#/recap/bad-component")

    expect(page.locator("main")).to_contain_text("Unsupported MDX component <Callout>")

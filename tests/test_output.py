import json
from typing import Any

import pytest
import typer

from shortcake._output import ShortcakeRichToolkit, get_rich_toolkit


def test_get_rich_toolkit_defaults_to_human() -> None:
    toolkit = get_rich_toolkit()
    assert toolkit.mode == "human"


def test_get_rich_toolkit_json_mode() -> None:
    toolkit = get_rich_toolkit(json_output=True)
    assert toolkit.mode == "json"


def test_success_json_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    toolkit = ShortcakeRichToolkit(mode="json")

    toolkit.success({"branch": "feature"})

    document = json.loads(capsys.readouterr().out)
    assert document == {"data": {"branch": "feature"}}


def test_success_json_with_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    toolkit = ShortcakeRichToolkit(mode="json")

    toolkit.success({"branch": "feature"}, warnings=["no GitHub token"])

    document = json.loads(capsys.readouterr().out)
    assert document == {
        "data": {"branch": "feature"},
        "warnings": ["no GitHub token"],
    }


def test_success_human_prints_data(capsys: pytest.CaptureFixture[str]) -> None:
    toolkit = ShortcakeRichToolkit(mode="human")

    toolkit.success("All done")

    assert "All done" in capsys.readouterr().out


def test_fail_json_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    toolkit = ShortcakeRichToolkit(mode="json")

    with pytest.raises(typer.Exit) as exc_info:
        toolkit.fail("not_tracked", "Branch 'x' is not tracked")

    assert exc_info.value.exit_code == 1
    document = json.loads(capsys.readouterr().out)
    assert document == {
        "error": {"code": "not_tracked", "message": "Branch 'x' is not tracked"}
    }


def test_fail_json_with_hint_and_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    toolkit = ShortcakeRichToolkit(mode="json")

    with pytest.raises(typer.Exit) as exc_info:
        toolkit.fail(
            "not_tracked",
            "Branch 'x' is not tracked",
            hint="Run 'sc adopt -f -p <parent>' to track it",
            exit_code=2,
        )

    assert exc_info.value.exit_code == 2
    document: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert document["error"]["hint"] == "Run 'sc adopt -f -p <parent>' to track it"


def test_fail_human_prints_error(capsys: pytest.CaptureFixture[str]) -> None:
    toolkit = ShortcakeRichToolkit(mode="human")

    with pytest.raises(typer.Exit):
        toolkit.fail("not_tracked", "Branch 'x' is not tracked")

    captured = capsys.readouterr()
    assert captured.err == "Error: Branch 'x' is not tracked\n"


def test_fail_human_prints_hint(capsys: pytest.CaptureFixture[str]) -> None:
    toolkit = ShortcakeRichToolkit(mode="human")

    with pytest.raises(typer.Exit):
        toolkit.fail(
            "not_tracked",
            "Branch 'x' is not tracked",
            hint="Run 'sc adopt -f -p <parent>' to track it",
        )

    captured = capsys.readouterr()
    assert "Error: Branch 'x' is not tracked" in captured.err
    assert "hint: Run 'sc adopt -f -p <parent>' to track it" in captured.err

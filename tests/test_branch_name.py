import pytest

from shortcake.cli import _generate_branch_name


@pytest.mark.parametrize(
    ("input_message", "expected"),
    [
        ("Add new feature", "add-new-feature"),
        ("Add  new   feature", "add-new-feature"),
        ("Add new feature!@#$%", "add-new-feature"),
        ("🚀 Add new feature", "add-new-feature"),
        ("!!! Add feature !!!", "add-feature"),
        ("Add NEW Feature", "add-new-feature"),
    ],
    ids=[
        "basic_message",
        "multiple_spaces",
        "special_characters",
        "emoji_removed_by_default",
        "leading_trailing_hyphens_removed",
        "mixed_case_converted_to_lowercase",
    ],
)
def test_branch_name_generation(input_message: str, expected: str) -> None:
    assert _generate_branch_name(input_message) == expected


@pytest.mark.parametrize(
    ("input_message", "expected"),
    [
        ("🚀 Add new feature", "🚀-add-new-feature"),
        ("🔥 🚀 Add feature", "🔥-🚀-add-feature"),
    ],
    ids=["single_emoji_kept", "multiple_emojis_kept"],
)
def test_branch_name_with_emoji_kept(input_message: str, expected: str) -> None:
    result = _generate_branch_name(input_message, keep_emoji=True)

    assert result == expected


def test_length_limit() -> None:
    long_message = "a" * 100
    result = _generate_branch_name(long_message)

    assert len(result) == 50


def test_empty_after_cleanup() -> None:
    result = _generate_branch_name("🚀🔥", keep_emoji=False)

    assert result == ""

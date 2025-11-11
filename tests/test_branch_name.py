"""Tests for branch name generation."""

from shortcake.cli import _generate_branch_name


def test_basic_message():
    """Test basic commit message conversion."""
    assert _generate_branch_name("Add new feature") == "add-new-feature"


def test_multiple_spaces():
    """Test that multiple spaces are converted to single hyphen."""
    assert _generate_branch_name("Add  new   feature") == "add-new-feature"


def test_special_characters():
    """Test that special characters are removed."""
    assert _generate_branch_name("Add new feature!@#$%") == "add-new-feature"


def test_emoji_removed_by_default():
    """Test that emojis are removed by default."""
    assert _generate_branch_name("🚀 Add new feature") == "add-new-feature"


def test_emoji_kept_when_flag_set():
    """Test that emojis are kept when keep_emoji=True."""
    result = _generate_branch_name("🚀 Add new feature", keep_emoji=True)
    assert result == "🚀-add-new-feature"


def test_multiple_emojis_kept():
    """Test multiple emojis are kept when flag is set."""
    result = _generate_branch_name("🔥 🚀 Add feature", keep_emoji=True)
    assert result == "🔥-🚀-add-feature"


def test_length_limit():
    """Test that branch names are limited to 50 characters."""
    long_message = "a" * 100
    result = _generate_branch_name(long_message)
    assert len(result) == 50


def test_leading_trailing_hyphens_removed():
    """Test that leading and trailing hyphens are removed."""
    assert _generate_branch_name("!!! Add feature !!!") == "add-feature"


def test_empty_after_cleanup():
    """Test handling of messages that become empty after cleanup."""
    # Just emojis with keep_emoji=False
    result = _generate_branch_name("🚀🔥", keep_emoji=False)
    assert result == ""


def test_mixed_case_converted_to_lowercase():
    """Test that mixed case is converted to lowercase."""
    assert _generate_branch_name("Add NEW Feature") == "add-new-feature"

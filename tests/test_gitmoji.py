from unittest.mock import patch

from shortcake._gitmoji import GITMOJIS, Gitmoji, pick_gitmoji


class Keys:
    """Key codes for simulating keyboard input."""

    ENTER = "\r"
    DOWN_ARROW = "\x1b[B"
    CTRL_C = "\x03"
    ESCAPE = "\x1b"


def test_gitmoji_data_exists() -> None:
    """Test GITMOJIS list is populated."""
    assert len(GITMOJIS) > 0


def test_gitmoji_has_common_emojis() -> None:
    """Test includes common development emojis."""
    emojis = {g.emoji for g in GITMOJIS}

    # Common emojis that should be present
    assert "✨" in emojis  # sparkles - new feature
    assert "🐛" in emojis  # bug - fix
    assert "📝" in emojis  # memo - docs
    assert "♻️" in emojis  # recycle - refactor
    assert "✅" in emojis  # check mark - tests


def test_gitmoji_has_required_fields() -> None:
    """Test each gitmoji has all required fields."""
    for gm in GITMOJIS:
        assert isinstance(gm, Gitmoji)
        assert gm.emoji
        assert gm.code
        assert gm.description


def test_gitmoji_codes_are_valid() -> None:
    """Test gitmoji codes follow shortcode format."""
    for gm in GITMOJIS:
        assert gm.code.startswith(":")
        assert gm.code.endswith(":")


# Interactive picker tests


def test_pick_gitmoji_select_first() -> None:
    """Test selecting first gitmoji with Enter."""
    steps = [Keys.ENTER]

    with patch("rich_toolkit.container.getchar") as mock_getchar:
        mock_getchar.side_effect = steps
        result = pick_gitmoji()

    assert result is not None
    assert result == GITMOJIS[0]


def test_pick_gitmoji_select_second() -> None:
    """Test selecting second gitmoji with Down+Enter."""
    steps = [Keys.DOWN_ARROW, Keys.ENTER]

    with patch("rich_toolkit.container.getchar") as mock_getchar:
        mock_getchar.side_effect = steps
        result = pick_gitmoji()

    assert result is not None
    assert result == GITMOJIS[1]


def test_pick_gitmoji_cancel_with_ctrl_c() -> None:
    """Test canceling with Ctrl+C returns None."""
    with patch("rich_toolkit.container.getchar") as mock_getchar:
        mock_getchar.side_effect = KeyboardInterrupt
        result = pick_gitmoji()

    assert result is None


def test_pick_gitmoji_cancel_with_eof() -> None:
    """Test canceling with EOF returns None."""
    with patch("rich_toolkit.container.getchar") as mock_getchar:
        mock_getchar.side_effect = EOFError
        result = pick_gitmoji()

    assert result is None


def test_pick_gitmoji_returns_none_when_ask_returns_none() -> None:
    """Test returns None when toolkit.ask() returns None."""
    with patch("shortcake._gitmoji.RichToolkit") as mock_toolkit_class:
        mock_toolkit = mock_toolkit_class.return_value
        mock_toolkit.ask.return_value = None
        result = pick_gitmoji()

    assert result is None

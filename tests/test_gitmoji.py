from shortcake._gitmoji import GITMOJIS, Gitmoji


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

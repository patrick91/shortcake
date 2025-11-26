"""Tests for the gitmoji module."""

from shortcake.gitmoji import GITMOJIS, Gitmoji


def test_gitmoji_dataclass():
    gitmoji = Gitmoji("🎨", ":art:", "Improve structure / format of the code")
    assert gitmoji.emoji == "🎨"
    assert gitmoji.code == ":art:"
    assert gitmoji.description == "Improve structure / format of the code"


def test_gitmojis_list_not_empty():
    assert len(GITMOJIS) > 0


def test_gitmojis_have_required_fields():
    for gitmoji in GITMOJIS:
        assert gitmoji.emoji, f"Gitmoji missing emoji: {gitmoji}"
        assert gitmoji.code, f"Gitmoji missing code: {gitmoji}"
        assert gitmoji.description, f"Gitmoji missing description: {gitmoji}"


def test_common_gitmojis_present():
    emojis = {g.emoji for g in GITMOJIS}
    codes = {g.code for g in GITMOJIS}

    # Check some common gitmojis are present
    assert "✨" in emojis  # sparkles - new features
    assert "🐛" in emojis  # bug - fix bugs
    assert "📝" in emojis  # memo - documentation
    assert "♻️" in emojis  # recycle - refactor

    assert ":sparkles:" in codes
    assert ":bug:" in codes
    assert ":memo:" in codes
    assert ":recycle:" in codes

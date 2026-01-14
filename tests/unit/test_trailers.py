from shortcake.trailers import (
    SHORTCAKE_PARENT_TRAILER,
    get_trailer_value,
    update_trailers,
)


def test_update_trailers_adds_block():
    message = "Subject line"
    updated = update_trailers(message, {SHORTCAKE_PARENT_TRAILER: "main"})

    assert updated == "Subject line\n\nShortcake-Parent: main"


def test_update_trailers_replaces_shortcake_only():
    message = (
        "Subject line\n\n"
        "Body text\n\n"
        "Signed-off-by: Test User <test@example.com>\n"
        "Shortcake-Parent: old-parent"
    )
    updated = update_trailers(message, {SHORTCAKE_PARENT_TRAILER: "new-parent"})

    assert "Signed-off-by: Test User <test@example.com>" in updated
    assert "Shortcake-Parent: old-parent" not in updated
    assert "Shortcake-Parent: new-parent" in updated


def test_get_trailer_value_returns_last():
    message = (
        "Subject\n\n"
        "Shortcake-Parent: old-parent\n"
        "Shortcake-Parent: new-parent"
    )
    assert get_trailer_value(message, SHORTCAKE_PARENT_TRAILER) == "new-parent"

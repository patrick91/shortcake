CHANGELOG
=========

1.0.2 - 2026-06-18
------------------

**`sc ui`**: the diff pane now lists files in the same order as the sidebar
file tree (folders first, then files, natural sort) instead of raw git-diff
order, so the two stay in sync as you scroll. The ordering reuses the file
tree's own sort, so it's guaranteed to match.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#119](https://github.com/patrick91/shortcake/pull/119)

1.0.1 - 2026-06-18
------------------

Two small fixes:

- **README on PyPI**: the logo and doc links used repo-relative paths, which
  PyPI doesn't resolve, so the logo showed as a broken image. They now use
  absolute URLs.
- **`sc submit`**: the stack section added to each PR description now links back
  to [shortcake](https://shortcake.patrick.wtf) with a 🍰 on its heading.

This release was contributed by [@patrick91](https://github.com/patrick91) in [#118](https://github.com/patrick91/shortcake/pull/118)

1.0.0 - 2026-06-18
------------------

Initial release of Shortcake! 🍰

This release was contributed by [@patrick91](https://github.com/patrick91) in [#117](https://github.com/patrick91/shortcake/pull/117)
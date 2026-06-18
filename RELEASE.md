---
release type: patch
---

Two small fixes:

- **README on PyPI**: the logo and doc links used repo-relative paths, which
  PyPI doesn't resolve, so the logo showed as a broken image. They now use
  absolute URLs.
- **`sc submit`**: the stack section added to each PR description now ends with
  a 🍰 footer linking back to [shortcake](https://shortcake.patrick.wtf).

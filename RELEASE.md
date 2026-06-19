---
release type: patch
---

**`sc ui`**: the "Large file" diff placeholder (the "Show changes" prompt for
big files) used fixed dark-theme yellows, so in light mode the pale text and
button sat on a pale tint with almost no contrast. The diff pane now uses
theme-aware `warning` color tokens that adapt to both light and dark themes,
so the placeholder and its button stay legible either way.

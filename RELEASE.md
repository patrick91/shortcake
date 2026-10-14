---
release type: patch
---

Make it obvious in `sc ui` when consecutive diff hunks are in different functions.
Hunk headers now show the enclosing function (git's hunk context) in the code font,
with the `@@` line range faded. A hunk that moves into a new function gets a bold,
ruled header, while one that continues the function above keeps its name dimmed.

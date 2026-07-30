---
release type: patch
---

`sc sync` now shows its cleanup as the stack it is changing, rather than flat
lines: branches are marked deleted in place, and branches that move show where
they ended up.

Fixes three things in its output: the header was printed twice, the question
"Delete the local copies?" appeared above an option reading "Delete it", and
there was no blank line under the header while the scan ran.

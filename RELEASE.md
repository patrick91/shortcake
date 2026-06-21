---
release type: minor
---

This release adds persistent review state to `sc ui`.

The review UI now remembers which files you marked as Viewed and whether you
prefer the unified or split diff layout across reloads. Viewed files are matched
to the current patch for each file, so Shortcake shows a file as unviewed again
when its diff changes instead of hiding fresh changes behind an old Viewed mark.
